"""
NeoGap — Centralised logger setup (colorlog + rotating file handler).
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

try:
    import colorlog
    _HAS_COLORLOG = True
except ImportError:
    _HAS_COLORLOG = False

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_COLOR_FORMAT = "%(log_color)s%(asctime)s | %(levelname)-8s | %(name)s | %(message)s%(reset)s"

_LOG_COLORS = {
    "DEBUG": "cyan",
    "INFO": "green",
    "WARNING": "yellow",
    "ERROR": "red",
    "CRITICAL": "bold_red",
}


def get_logger(name: str, level: str = "INFO", log_file: str = "logs/gap_strategy.log") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Console handler
    if _HAS_COLORLOG:
        ch = colorlog.StreamHandler()
        ch.setFormatter(colorlog.ColoredFormatter(_COLOR_FORMAT, log_colors=_LOG_COLORS))
    else:
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter(_LOG_FORMAT))
    logger.addHandler(ch)

    # Rotating file handler
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    fh = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=5)
    fh.setFormatter(logging.Formatter(_LOG_FORMAT))
    logger.addHandler(fh)

    logger.propagate = False
    return logger
