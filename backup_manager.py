from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from project_paths import DATA_DIR, PROJECT_DIR

logger = logging.getLogger(__name__)
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", str(DATA_DIR / "backups"))).expanduser().resolve()
INTERVAL_HOURS = max(1, int(os.getenv("BACKUP_INTERVAL_HOURS", "24")))
KEEP_BACKUPS = max(3, int(os.getenv("BACKUP_KEEP_COUNT", "14")))


def _sqlite_backup(source: Path, destination: Path) -> None:
    src = sqlite3.connect(source)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def create_backup() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    folder = BACKUP_DIR / stamp
    folder.mkdir(parents=True, exist_ok=True)

    for path in DATA_DIR.glob("*.db"):
        try:
            _sqlite_backup(path, folder / path.name)
        except Exception:
            logger.exception("Failed to backup %s", path)

    for name in (".env", "config.py", "requirements.txt"):
        path = PROJECT_DIR / name
        if path.exists():
            shutil.copy2(path, folder / path.name)

    archive = shutil.make_archive(str(folder), "zip", root_dir=folder)
    shutil.rmtree(folder, ignore_errors=True)
    _cleanup_old_backups()
    logger.info("Backup created: %s", archive)
    return Path(archive)


def _cleanup_old_backups() -> None:
    archives = sorted(BACKUP_DIR.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in archives[KEEP_BACKUPS:]:
        try:
            old.unlink()
        except OSError:
            logger.exception("Failed deleting old backup %s", old)


async def automatic_backup_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(create_backup)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Automatic backup failed")
        await asyncio.sleep(INTERVAL_HOURS * 3600)
