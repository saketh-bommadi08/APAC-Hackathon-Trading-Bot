import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from config import LOG_DIR

_CONFIGURED = False

def setup_logging():
    global _CONFIGURED
    if _CONFIGURED:
        return
    Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        "%Y-%m-%d %H:%M:%S%z",
    )
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)
    file_handler = RotatingFileHandler(
        Path(LOG_DIR) / "bot.log",
        maxBytes=5_000_000,
        backupCount=10,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)
    _CONFIGURED = True

def get_logger(name):
    setup_logging()
    return logging.getLogger(name)
