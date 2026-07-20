from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

LOG_DIR = Path(os.getenv("LOG_DIR", "logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)


def setup_logging() -> None:
    root = logging.getLogger()
    if root.handlers:
        return

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    root.setLevel(level)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    app_file = logging.handlers.RotatingFileHandler(
        LOG_DIR / "liquidityplus.log",
        maxBytes=5_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    app_file.setFormatter(formatter)
    root.addHandler(app_file)

    error_file = logging.handlers.RotatingFileHandler(
        LOG_DIR / "errors.log",
        maxBytes=5_000_000,
        backupCount=10,
        encoding="utf-8",
    )
    error_file.setLevel(logging.ERROR)
    error_file.setFormatter(formatter)
    root.addHandler(error_file)
