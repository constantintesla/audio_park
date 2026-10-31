"""Local accounts, persistent session signing key and role checks."""
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
import ipaddress
import uuid
from datetime import timedelta

from flask import current_app, g, jsonify, redirect, request, session
from werkzeug.security import generate_password_hash

ROLES = {"expert", "moderator", "admin"}
MANAGERS = {"moderator", "admin"}


def client_address():
    peer = request.remote_addr or "unknown"
    trusted = os.getenv("TRUSTED_PROXY_CIDRS", "").split(",")
    try:
        if any(value.strip() and ipaddress.ip_address(peer) in ipaddress.ip_network(value.strip()) for value in trusted):
            chain = request.headers.get("X-Forwarded-For", "").split(",")
            for value in reversed(chain):
                address = ipaddress.ip_address(value.strip())
                if not any(net.strip() and address in ipaddress.ip_network(net.strip()) for net in trusted):
                    return str(address)
    except ValueError:
        pass
    return peer


def audit_auth(event, login_name="", reason="", db=None):
    def insert(conn):
        conn.execute("DELETE FROM auth_audit WHERE created_at < strftime('%Y-%m-%dT%H:%M:%SZ','now','-90 days')")
        conn.execute("INSERT INTO auth_audit(event,login,reason,ip) VALUES (?,?,?,?)",
                     (event, login_name[:200], reason, client_address()))
    if db is not None:
        insert(db)
    else:
        with connection() as conn:
            insert(conn)


def connection():
    from flask import current_app
    db = sqlite3.connect(current_app.config["AUTH_DB_PATH"], timeout=15)
    db.row_factory = sqlite3.Row
    return db


def public_user(row):
    return {key: row[key] for key in ("id", "login", "role", "active", "created_at")}


def public_patient(row):
    return {
        "uuid": row["uuid"],
        "owner_id": row["owner_id"],
        "local_number": row["local_number"],
        "label": f"Пациент {row['local_number']}",
        "status": row["status"],
        "created_at": row["created_at"],
        "archived_at": row["archived_at"],
    }


def create_patient(owner_id, legacy_id=None):
    """Atomically allocate the next doctor-local number and a global opaque UUID."""
    legacy_id = str(legacy_id).strip()[:100] if legacy_id else None
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        if legacy_id:
            existing = db.execute(
                "SELECT * FROM patients WHERE owner_id = ? AND legacy_id = ?",
                (owner_id, legacy_id),
            ).fetchone()
            if existing:
                return public_patient(existing)
        local_number = db.execute(
            "SELECT COALESCE(MAX(local_number), 0) + 1 FROM patients WHERE owner_id = ?",
            (owner_id,),
        ).fetchone()[0]
        patient_uuid = str(uuid.uuid4())
        db.execute(
            "INSERT INTO patients(uuid, owner_id, local_number, legacy_id) VALUES (?, ?, ?, ?)",
            (patient_uuid, owner_id, local_number, legacy_id),
        )
        return public_patient(db.execute("SELECT * FROM patients WHERE uuid = ?", (patient_uuid,)).fetchone())


def patient_identity_key(owner_id):
    """Derive an account key from a master stored outside SQLite."""
    master = bytes.fromhex(current_app.config["PATIENT_HMAC_MASTER_KEY"])
    message = f"audio-park-patient-v1:{owner_id}".encode("ascii")
    return hmac.new(master, message, hashlib.sha256).hexdigest()


def patients_by_lookup_token(owner_id, lookup_token):
    with connection() as db:
        rows = db.execute(
            "SELECT patients.* FROM patient_lookup_tokens "
            "JOIN patients ON patients.uuid = patient_lookup_tokens.patient_uuid "
            "WHERE patient_lookup_tokens.owner_id = ? AND patient_lookup_tokens.lookup_token = ? "
            "AND patients.status = 'active' "
            "ORDER BY patients.local_number DESC",
            (owner_id, lookup_token),
        ).fetchall()
    return [public_patient(row) for row in rows]


def create_patient_with_lookup_token(owner_id, lookup_token):
    """Create a distinct patient; one HMAC may intentionally map to multiple same-name people."""
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        local_number = db.execute(
            "SELECT COALESCE(MAX(local_number), 0) + 1 FROM patients WHERE owner_id = ?",
            (owner_id,),
        ).fetchone()[0]
        patient_uuid = str(uuid.uuid4())
        db.execute(
            "INSERT INTO patients(uuid, owner_id, local_number) VALUES (?, ?, ?)",
            (patient_uuid, owner_id, local_number),
        )
        db.execute(
            "INSERT INTO patient_lookup_tokens(owner_id, lookup_token, patient_uuid) VALUES (?, ?, ?)",
            (owner_id, lookup_token, patient_uuid),
        )
        row = db.execute("SELECT * FROM patients WHERE uuid = ?", (patient_uuid,)).fetchone()
        return public_patient(row)


def bind_patient_lookup_token(owner_id, patient_uuid, lookup_token):
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        patient = db.execute(
            "SELECT * FROM patients WHERE uuid = ? AND owner_id = ? AND status = 'active'",
            (str(patient_uuid), owner_id),
        ).fetchone()
        if not patient:
            return None, "not_found"
        db.execute(
            "INSERT OR IGNORE INTO patient_lookup_tokens(owner_id, lookup_token, patient_uuid) VALUES (?, ?, ?)",
            (owner_id, lookup_token, str(patient_uuid)),
        )
        return public_patient(patient), None


def get_patient(patient_uuid, owner_id):
    with connection() as db:
        row = db.execute(
            "SELECT * FROM patients WHERE uuid = ? AND owner_id = ?",
            (str(patient_uuid), owner_id),
        ).fetchone()
    return public_patient(row) if row else None


def get_patient_by_legacy_id(owner_id, legacy_id):
    with connection() as db:
        row = db.execute(
            "SELECT * FROM patients WHERE owner_id = ? AND legacy_id = ?",
            (owner_id, str(legacy_id)),
        ).fetchone()
    return public_patient(row) if row else None


def list_patients(owner_id, include_archived=False):
    sql = "SELECT * FROM patients WHERE owner_id = ?"
    params = [owner_id]
    if not include_archived:
        sql += " AND status = 'active'"
    sql += " ORDER BY local_number DESC"
    with connection() as db:
        rows = db.execute(sql, params).fetchall()
    return [public_patient(row) for row in rows]


def archive_patient(patient_uuid, owner_id):
    with connection() as db:
        cursor = db.execute(
            "UPDATE patients SET status = 'archived', archived_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') "
            "WHERE uuid = ? AND owner_id = ? AND status = 'active'",
            (str(patient_uuid), owner_id),
        )
        if not cursor.rowcount:
            return None
        row = db.execute("SELECT * FROM patients WHERE uuid = ?", (str(patient_uuid),)).fetchone()
    return public_patient(row)


def password_matches(stored, password):
    """Werkzeug scrypt format, including Python 3.10 development machines."""
    try:
        method, salt, expected = stored.split("$")
        kind, *params = method.split(":")
        if kind == "scrypt":
            n, r, p = map(int, params)
            actual = hashlib.scrypt(password.encode(), salt=salt.encode(), n=n, r=r, p=p,
                                    maxmem=132 * n * r * p).hex()
        elif kind == "pbkdf2":
            algorithm, iterations = params
            actual = hashlib.pbkdf2_hmac(algorithm, password.encode(), salt.encode(), int(iterations)).hex()
        else:
            return False
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, OverflowError):
        return False


def create_user(login, password=None, role="expert", password_hash=None, source_id=None):
    login = str(login or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9@._+\-]{1,199}", login):
        raise ValueError("Логин: 2–200 символов, латинские буквы, цифры или email")
    if role not in ROLES:
        raise ValueError("Неизвестная роль")
    if password_hash is None:
        if not isinstance(password, str) or not 8 <= len(password) <= 256:
            raise ValueError("Пароль должен содержать от 8 до 256 символов")
        # PBKDF2 is available in both the Python 3.10 dev and Python 3.11 prod runtimes.
        password_hash = generate_password_hash(password, method="pbkdf2:sha256:600000")
    with connection() as db:
        cursor = db.execute("INSERT INTO users(login, password_hash, role, source_id) VALUES (?, ?, ?, ?)",
                            (login, password_hash, role, source_id))
        return public_user(db.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone())


def is_manager():
    return bool(g.user and g.user["role"] in MANAGERS)


def owns_result(result):
    return bool(g.user and result.get("owner_id") == g.user["id"])


def visible_results(results):
    all_scope = request.args.get("scope") == "all" and is_manager()
    selected = results if all_scope else [r for r in results if owns_result(r)]
    patient = request.args.get("patient_id", "").strip()
    owner = request.args.get("owner_id", type=int)
    query = request.args.get("q", "").strip().casefold()
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")
    def matches(result):
        info = result.get("user_info", {})
        date = info.get("timestamp", "")[:10]
        haystack = " ".join(str(v) for v in (result.get("study_id", ""), result.get("patient_id", ""), info.get("tg_username", ""))).casefold()
        return ((not patient or result.get("patient_id") == patient)
                and (not owner or result.get("owner_id") == owner)
                and (not query or query in haystack)
                and (not date_from or date >= date_from)
                and (not date_to or date <= date_to))
    return [r for r in selected if matches(r)]


def init_auth(app):
    auth_dir = Path(os.getenv("AUTH_DATA_DIR", "auth_data")).resolve()
    auth_dir.mkdir(parents=True, exist_ok=True)
    key_file = auth_dir / "session.key"
    secret = os.getenv("SECRET_KEY")
    if not secret:
        try:
            with key_file.open("x", encoding="ascii") as stream:
                stream.write(secrets.token_hex(32))
            key_file.chmod(0o600)
        except FileExistsError:
            pass
        secret = key_file.read_text(encoding="ascii").strip()
    patient_key_file = auth_dir / "patient-hmac.key"
    patient_master = os.getenv("PATIENT_HMAC_MASTER_KEY")
    if not patient_master:
        try:
            with patient_key_file.open("x", encoding="ascii") as stream:
                stream.write(secrets.token_hex(32))
            patient_key_file.chmod(0o600)
        except FileExistsError:
            pass
        patient_master = patient_key_file.read_text(encoding="ascii").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{64}", patient_master):
        raise RuntimeError("PATIENT_HMAC_MASTER_KEY должен содержать 64 шестнадцатеричных символа")
    app.config.update(
        SECRET_KEY=secret,
        AUTH_DB_PATH=str(auth_dir / "users.sqlite3"),
        SESSION_COOKIE_NAME="audio_park_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        MAX_CONTENT_LENGTH=50 * 1024 * 1024,
        PATIENT_HMAC_MASTER_KEY=patient_master.lower(),
    )
    with app.app_context(), connection() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            login TEXT UNIQUE NOT NULL COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('expert', 'moderator', 'admin')),
            active INTEGER NOT NULL DEFAULT 1,
            source_id INTEGER UNIQUE,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );
        CREATE TABLE IF NOT EXISTS login_attempts (
            attempt_key TEXT PRIMARY KEY, failures INTEGER NOT NULL, first_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS auth_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            event TEXT NOT NULL, login TEXT NOT NULL, reason TEXT NOT NULL, ip TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS patients (
            uuid TEXT PRIMARY KEY,
            owner_id INTEGER NOT NULL REFERENCES users(id),
            local_number INTEGER NOT NULL CHECK(local_number > 0),
            legacy_id TEXT,
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'archived')),
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            archived_at TEXT,
            UNIQUE(owner_id, local_number),
            UNIQUE(owner_id, legacy_id)
        );
        CREATE INDEX IF NOT EXISTS patients_owner_status ON patients(owner_id, status, local_number);
        CREATE TABLE IF NOT EXISTS patient_lookup_tokens (
            owner_id INTEGER NOT NULL REFERENCES users(id),
            lookup_token TEXT NOT NULL,
            patient_uuid TEXT NOT NULL REFERENCES patients(uuid),
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            PRIMARY KEY(owner_id, lookup_token, patient_uuid)
        );
        CREATE INDEX IF NOT EXISTS patient_lookup_owner_token ON patient_lookup_tokens(owner_id, lookup_token);
        """)

    @app.before_request
    def authenticate():
        g.user = None
        user_id = session.get("user_id")
        token = session.get("session_token", "")
        if user_id and token:
            with connection() as db:
                row = db.execute("SELECT users.* FROM users JOIN sessions ON sessions.user_id = users.id "
                                 "WHERE users.id = ? AND active = 1 AND token_hash = ? AND expires_at > ?",
                                 (user_id, hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
            if row:
                g.user = public_user(row)
        if request.path in {"/healthz", "/api/health"} or request.path.startswith("/assets/"):
            return None
        if request.path not in {"/login", "/api/auth/login", "/api/auth/csrf"} and not g.user:
            if request.path.startswith("/api/"):
                return jsonify(error="Войдите в учётную запись"), 401
            # Relative locations preserve the /audio_park reverse-proxy prefix.
            return redirect("../" * max(0, request.path.count("/") - 1) + "login")
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            expected = session.get("csrf_token", "")
            supplied = request.headers.get("X-CSRF-Token", "")
            if not expected or not hmac.compare_digest(expected, supplied):
                if request.path == "/api/auth/login":
                    audit_auth("login_failed", reason="csrf_invalid")
                return jsonify(error="Сессия устарела. Обновите страницу"), 403
        if request.path in {"/auth-logs", "/api/admin/auth-logs"} and g.user["role"] != "admin":
            return jsonify(error="Недостаточно прав"), 403
        manager_routes = {"/all-results", "/users", "/api/users", "/api/recalculate-all"}
        if request.path in manager_routes and not is_manager():
            return jsonify(error="Недостаточно прав"), 403

    @app.after_request
    def protect_response(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "same-origin"
        if not request.path.startswith("/assets/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/healthz")
    def health():
        return jsonify(status="ok")

    @app.get("/api/auth/csrf")
    def csrf():
        session.setdefault("csrf_token", secrets.token_urlsafe(32))
        return jsonify(csrf_token=session["csrf_token"])

    @app.post("/api/auth/login")
    def login():
        data = request.get_json(silent=True) or {}
        login_name = str(data.get("login", "")).strip().lower()[:200]
        password = data.get("password", "")
        if not isinstance(password, str) or len(password) > 256:
            audit_auth("login_failed", login_name, "invalid_password_format")
            return jsonify(error="Неверный логин или пароль"), 401
        # Limit by source address and by login, so rotating logins cannot flood scrypt.
        now = time.time()
        keys = ["ip:" + client_address(), "login:" + login_name]
        with connection() as db:
            db.execute("DELETE FROM login_attempts WHERE first_at < ?", (now - 900,))
            attempts = [db.execute("SELECT failures FROM login_attempts WHERE attempt_key = ?", (key,)).fetchone()
                        for key in keys]
            if any(row and row["failures"] >= 20 for row in attempts):
                audit_auth("login_failed", login_name, "rate_limited", db)
                return jsonify(error="Слишком много попыток. Повторите через 15 минут"), 429
            row = db.execute("SELECT * FROM users WHERE login = ? AND active = 1", (login_name,)).fetchone()
            valid = password_matches(row["password_hash"], password) if row else False
            if not valid:
                existing = db.execute("SELECT active FROM users WHERE login = ?", (login_name,)).fetchone()
                reason = "unknown_login" if not existing else ("inactive_account" if not existing["active"] else "wrong_password")
                audit_auth("login_failed", login_name, reason, db)
                for key in keys:
                    db.execute("INSERT INTO login_attempts VALUES (?, 1, ?) ON CONFLICT(attempt_key) "
                               "DO UPDATE SET failures = failures + 1", (key, now))
                return jsonify(error="Неверный логин или пароль"), 401
            for key in keys:
                db.execute("DELETE FROM login_attempts WHERE attempt_key = ?", (key,))
            audit_auth("login_success", login_name, db=db)
        session.clear()
        session.permanent = True
        session["user_id"] = row["id"]
        session["session_token"] = secrets.token_urlsafe(32)
        session["csrf_token"] = secrets.token_urlsafe(32)
        with connection() as db:
            db.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            db.execute("INSERT INTO sessions VALUES (?, ?, ?)",
                       (hashlib.sha256(session["session_token"].encode()).hexdigest(), row["id"], now + 12 * 3600))
        return jsonify(user=public_user(row), csrf_token=session["csrf_token"])

    @app.get("/api/auth/me")
    def me():
        session.setdefault("csrf_token", secrets.token_urlsafe(32))
        return jsonify(user=g.user, csrf_token=session["csrf_token"])

    @app.post("/api/auth/logout")
    def logout():
        audit_auth("logout", g.user["login"])
        token = session.get("session_token", "")
        with connection() as db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (hashlib.sha256(token.encode()).hexdigest(),))
        session.clear()
        return jsonify(status="ok")

    @app.get("/api/admin/auth-logs")
    def auth_logs():
        before = request.args.get("before", type=int)
        with connection() as db:
            rows = db.execute("SELECT * FROM auth_audit WHERE (? IS NULL OR id < ?) ORDER BY id DESC LIMIT 100",
                              (before, before)).fetchall()
        return jsonify(events=[dict(row) for row in rows])

    @app.get("/api/users")
    def users():
        with connection() as db:
            rows = db.execute("SELECT * FROM users ORDER BY id").fetchall()
        return jsonify(users=[public_user(row) for row in rows])

    @app.post("/api/users")
    def add_user():
        data = request.get_json(silent=True) or {}
        try:
            user = create_user(data.get("login"), data.get("password"), data.get("role", "expert"))
            return jsonify(user=user), 201
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except sqlite3.IntegrityError:
            return jsonify(error="Такая учётная запись уже существует"), 409
