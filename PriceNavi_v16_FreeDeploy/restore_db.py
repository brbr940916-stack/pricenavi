#!/usr/bin/env python3
"""Restore a SQLite backup after making a safety copy of the current database."""
import os, sqlite3, argparse, shutil
from pathlib import Path
from datetime import datetime, timezone
base=Path(__file__).resolve().parent
current=Path(os.environ.get('DATABASE_PATH',str(base/'cloud_price_tracker.db')))
ap=argparse.ArgumentParser(description='Restore a price tracker SQLite backup')
ap.add_argument('backup',help='path to a verified .sqlite3 backup')
ap.add_argument('--yes',action='store_true',help='confirm replacement of current database')
a=ap.parse_args(); source=Path(a.backup).resolve()
if not source.is_file(): raise SystemExit('Backup file not found')
with sqlite3.connect(f'file:{source}?mode=ro',uri=True) as c:
    check=c.execute('PRAGMA integrity_check').fetchone()[0]
    if check!='ok': raise SystemExit(f'Backup integrity check failed: {check}')
if not a.yes: raise SystemExit('No changes made. Re-run with --yes after stopping the app and verifying the backup.')
current.parent.mkdir(parents=True,exist_ok=True)
if current.exists():
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    safety=current.with_name(current.name+'.pre-restore-'+stamp)
    shutil.copy2(current,safety)
tmp=current.with_suffix(current.suffix+'.restore-tmp')
with sqlite3.connect(f'file:{source}?mode=ro',uri=True) as src, sqlite3.connect(tmp) as dst:
    src.backup(dst)
    if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
        tmp.unlink(missing_ok=True); raise SystemExit('Restored copy failed integrity check')
os.replace(tmp,current)
print(f'Restore OK: {current}')
