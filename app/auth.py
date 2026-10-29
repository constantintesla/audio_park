"""Local accounts, persistent session signing key and role checks."""
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from datetime import timedelta

from flask import g, jsonify, redirect, request, session
from werkzeug.security import generate_password_hash

ROLES = {"expert", "moderator", "admin"}
MANAGERS = {"moderator", "admin"}


def connection():
    from flask import current_app
    db = sqlite3.connect(current_app.config["AUTH_DB_PATH"], timeout=15)
    db.row_factory = sqlite3.Row
    return db


def public_user(row):
    return {key: row[key] for key in ("id", "login", "role", "active", "created_at")}


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
    app.config.update(
        SECRET_KEY=secret,
        AUTH_DB_PATH=str(auth_dir / "users.sqlite3"),
        SESSION_COOKIE_NAME="audio_park_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        MAX_CONTENT_LENGTH=50 * 1024 * 1024,
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
                return jsonify(error="Сессия устарела. Обновите страницу"), 403
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
            return jsonify(error="Неверный логин или пароль"), 401
        # Limit by source address and by login, so rotating logins cannot flood scrypt.
        now = time.time()
        keys = ["ip:" + (request.remote_addr or "unknown"), "login:" + login_name]
        with connection() as db:
            db.execute("DELETE FROM login_attempts WHERE first_at < ?", (now - 900,))
            attempts = [db.execute("SELECT failures FROM login_attempts WHERE attempt_key = ?", (key,)).fetchone()
                        for key in keys]
            if any(row and row["failures"] >= 20 for row in attempts):
                return jsonify(error="Слишком много попыток. Повторите через 15 минут"), 429
            row = db.execute("SELECT * FROM users WHERE login = ? AND active = 1", (login_name,)).fetchone()
            valid = password_matches(row["password_hash"], password) if row else False
            if not valid:
                for key in keys:
                    db.execute("INSERT INTO login_attempts VALUES (?, 1, ?) ON CONFLICT(attempt_key) "
                               "DO UPDATE SET failures = failures + 1", (key, now))
                return jsonify(error="Неверный логин или пароль"), 401
            for key in keys:
                db.execute("DELETE FROM login_attempts WHERE attempt_key = ?", (key,))
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
        token = session.get("session_token", "")
        with connection() as db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (hashlib.sha256(token.encode()).hexdigest(),))
        session.clear()
        return jsonify(status="ok")

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
