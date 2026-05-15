"""Application logging setup."""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler


def configure_logging(
    log_name: str = "tdnet.log",
    *,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 10,
) -> Path:
    """Configure console and file logging for CLI/service jobs."""
    log_dir = Path.cwd() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / log_name

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    )

    if not any(isinstance(handler, logging.StreamHandler) for handler in root.handlers):
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)

    if not any(
        isinstance(handler, logging.FileHandler)
        and Path(handler.baseFilename) == log_path
        for handler in root.handlers
    ):
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    return log_path
