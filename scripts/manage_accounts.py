"""Import the PostgreSQL text export without putting credentials into Git."""
import argparse
import getpass
from pathlib import Path
import re
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from flask import Flask
from auth import connection, create_user, init_auth


def import_accounts(path):
    pattern = re.compile(r"^\s*(\d+)\s*\|\s*([^|]+?)\s*\|\s*(expert|moderator|admin)\s*\|\s*(\S+)\s*$")
    rows = []
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        match = pattern.match(line)
        if match:
            source_id, login, role, hashed = match.groups()
            if not re.fullmatch(r"scrypt:32768:8:1\$[A-Za-z0-9]+\$[a-f0-9]{128}", hashed):
                raise ValueError("Неподдерживаемый формат хэша в файле импорта")
            rows.append((source_id, login, role, hashed))
    if not rows:
        raise ValueError("В файле не найдены учётные записи")
    imported = skipped = 0
    for source_id, login, role, hashed in rows:
        with connection() as db:
            exists = db.execute("SELECT id FROM users WHERE login = ? OR source_id = ?", (login.lower(), int(source_id))).fetchone()
        if exists:
            skipped += 1
            continue
        create_user(login, role=role, password_hash=hashed, source_id=int(source_id))
        imported += 1
    return imported, skipped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export_file", nargs="?", help="PostgreSQL text export, e.g. 123.txt")
    parser.add_argument("--admin", help="Create an additional local admin, e.g. tesla")
    parser.add_argument("--password-stdin", action="store_true", help="Read new admin password from stdin")
    args = parser.parse_args()
    if not args.export_file and not args.admin:
        parser.error("Укажите файл импорта или --admin")
    app = Flask(__name__)
    init_auth(app)
    with app.app_context():
        if args.export_file:
            imported, skipped = import_accounts(args.export_file)
            print(f"Imported: {imported}; already present: {skipped}")
        if args.admin:
            with connection() as db:
                exists = db.execute("SELECT id FROM users WHERE login = ?", (args.admin.lower(),)).fetchone()
            if exists:
                print("Admin already exists; password unchanged")
            else:
                password = sys.stdin.readline().rstrip("\r\n") if args.password_stdin else getpass.getpass("Admin password: ")
                create_user(args.admin, password, role="admin")
                print("Admin created")


if __name__ == "__main__":
    main()
