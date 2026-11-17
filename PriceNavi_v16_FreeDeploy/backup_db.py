#!/usr/bin/env python3
"""Consistent SQLite online backup with retention."""
import os, sqlite3, argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta

base=Path(__file__).resolve().parent
src=Path(os.environ.get("DATABASE_PATH", str(base/"cloud_price_tracker.db")))
dest=Path(os.environ.get("BACKUP_DIR", str(src.parent/"backups")))
ap=argparse.ArgumentParser()
ap.add_argument("--keep-days",type=int,default=int(os.environ.get("BACKUP_KEEP_DAYS","14")))
a=ap.parse_args()
if not src.exists(): raise SystemExit(f"Database not found: {src}")
dest.mkdir(parents=True,exist_ok=True)
stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
out=dest/f"price_tracker_{stamp}.sqlite3"
with sqlite3.connect(f"file:{src}?mode=ro",uri=True,timeout=30) as source:
    with sqlite3.connect(out) as target:
        source.backup(target)
        check=target.execute("PRAGMA integrity_check").fetchone()[0]
        if check != "ok":
            out.unlink(missing_ok=True)
            raise SystemExit(f"Backup integrity check failed: {check}")
for old in dest.glob("price_tracker_*.sqlite3"):
    if old != out and datetime.fromtimestamp(old.stat().st_mtime,timezone.utc) < datetime.now(timezone.utc)-timedelta(days=max(1,a.keep_days)):
        old.unlink(missing_ok=True)
print(f"Backup OK: {out} ({out.stat().st_size} bytes)")
