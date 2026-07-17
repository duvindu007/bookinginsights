"""
ErrorLogger is the abstraction the service layer depends on for persisting
rejected rows. A future swap to, say, a database table or S3 only requires
a new implementation of this interface.
"""
import csv
import logging
import os
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional

from .schemas import ValidationError

logger = logging.getLogger(__name__)


class ErrorLogger(ABC):
    @abstractmethod
    def log(self, source_filename: str, errors: List[ValidationError]) -> Optional[str]:
        """Persist the errors; return an identifier (e.g. filename), or None if there were none."""
        raise NotImplementedError

    @abstractmethod
    def list_logs(self) -> List[str]:
        raise NotImplementedError

    @abstractmethod
    def get_log_path(self, log_id: str) -> Optional[str]:
        """Return a filesystem path for the given log identifier, or None if it doesn't exist."""
        raise NotImplementedError


class CsvFileErrorLogger(ErrorLogger):
    def __init__(self, log_dir: str = None):
        self._log_dir = log_dir or os.environ.get("UPLOAD_LOG_DIR", "upload_logs")
        os.makedirs(self._log_dir, exist_ok=True)

    def log(self, source_filename: str, errors: List[ValidationError]) -> Optional[str]:
        if not errors:
            return None

        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
        safe_name = os.path.splitext(os.path.basename(source_filename))[0]
        log_filename = f"{timestamp}_{safe_name}_{uuid.uuid4().hex[:8]}_errors.csv"
        log_path = os.path.join(self._log_dir, log_filename)

        with open(log_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["row", "booking_no", "reason"])
            for error in errors:
                writer.writerow([error.row, error.booking_no, error.reason])

        logger.info("Wrote %d error(s) to log file %s", len(errors), log_path)
        return log_filename

    def list_logs(self) -> List[str]:
        return sorted(os.listdir(self._log_dir), reverse=True)

    def get_log_path(self, log_id: str) -> Optional[str]:
        safe_id = os.path.basename(log_id)  # guards against path traversal
        path = os.path.join(self._log_dir, safe_id)
        return path if os.path.isfile(path) else None
