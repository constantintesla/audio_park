"""Report stale uploads by default; --delete explicitly removes expired drafts.

Run with the same UPLOAD_DIR/AUTH_DATA_DIR as the API. Active jobs and completed
results are excluded. Retention is seven days by default.
"""
import argparse
import shutil
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
from app.api import app
from app.uploads import database, root


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--delete', action='store_true')
    parser.add_argument('--days', type=int, default=7)
    args = parser.parse_args()
    if args.days < 1:
        parser.error('--days must be positive')
    with app.app_context(), database() as db:
        for row in db.execute("SELECT * FROM uploads WHERE state IN ('uploading','cancelled','failed') AND updated < ?", (time.time() - args.days * 86400,)).fetchall():
            directory = root() / row['id']
            # IDs are server-generated, still validate the resolved deletion path.
            if directory.resolve().parent != root().resolve():
                raise RuntimeError('Unsafe upload path')
            print(row['id'], row['state'], 'delete' if args.delete else 'report')
            if args.delete:
                if directory.exists():
                    shutil.rmtree(directory)
                db.execute('DELETE FROM parts WHERE upload_id=?', (row['id'],))
                db.execute('DELETE FROM uploads WHERE id=?', (row['id'],))


if __name__ == '__main__':
    main()
