from __future__ import annotations

import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("LIQUIDITYPLUS_DATA_DIR", str(PROJECT_DIR))).expanduser().resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)


def data_path(filename: str) -> str:
    """Returns one stable absolute path for every runtime database."""
    return str(DATA_DIR / filename)


def project_path(filename: str) -> Path:
    return PROJECT_DIR / filename
