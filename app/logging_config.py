
import logging
import logging.handlers
import os

LOG_DIR = os.environ.get("LOG_DIR", "logs")
LOG_FILE = os.path.join(LOG_DIR, "app.log")

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(level: int = logging.INFO) -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    root = logging.getLogger()

    if root.handlers:
        return  # already configured (e.g. re-imported under --reload)

    root.setLevel(level)
    formatter = logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    # Rotates at 5MB, keeps 5 backups, so the log directory doesn't grow unbounded.
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5,
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # Uvicorn's own loggers are noisy at DEBUG; leave them at their defaults
    # but make sure they still flow through our handlers/format.
    for noisy_logger in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(noisy_logger).handlers = []
        logging.getLogger(noisy_logger).propagate = True
