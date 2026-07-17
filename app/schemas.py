"""
Internal data-transfer objects. These carry data between layers (validation,
service, API) without any of those layers depending on each other's internals.
"""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ValidationError:
    row: int
    booking_no: Optional[str]
    reason: str


@dataclass
class UploadResult:
    filename: str
    rows_inserted: int
    errors: List[ValidationError] = field(default_factory=list)
    error_log_file: Optional[str] = None

    @property
    def total_rows_processed(self) -> int:
        return self.rows_inserted + len(self.errors)

    @property
    def rows_rejected(self) -> int:
        return len(self.errors)
