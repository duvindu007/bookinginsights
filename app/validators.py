"""Each validation rule is its own class implementing RowValidator — a new
rule is a new subclass, no existing code changes."""
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Set

from .parsing import DEFAULT_DATE_FORMATS, parse_amount, parse_date
from .schemas import ValidationError

REQUIRED_FIELDS: List[str] = [
    "Booking No", "Agent", "Country", "Tour Type", "Booking Date", "Amount", "Status",
]


class RowValidator(ABC):
    """A single validation rule applied to one CSV row."""

    @abstractmethod
    def validate(self, row: Dict[str, str], row_number: int) -> Optional[ValidationError]:
        """Return a ValidationError if the row fails this rule, else None."""
        raise NotImplementedError

    @staticmethod
    def _booking_no(row: Dict[str, str]) -> Optional[str]:
        value = (row.get("Booking No") or "").strip()
        return value or None


class MandatoryFieldsValidator(RowValidator):
    """Rejects a row if any required column is blank."""

    def __init__(self, required_fields: List[str] = REQUIRED_FIELDS):
        self.required_fields = required_fields

    def validate(self, row: Dict[str, str], row_number: int) -> Optional[ValidationError]:
        missing = [f for f in self.required_fields if not (row.get(f) or "").strip()]
        if missing:
            return ValidationError(
                row=row_number,
                booking_no=self._booking_no(row),
                reason=f"Missing mandatory field(s): {', '.join(missing)}",
            )
        return None


class DateFormatValidator(RowValidator):
    """Rejects a row whose date field doesn't parse against any accepted format."""

    def __init__(self, field_name: str = "Booking Date", formats: List[str] = DEFAULT_DATE_FORMATS):
        self.field_name = field_name
        self.formats = formats

    def validate(self, row: Dict[str, str], row_number: int) -> Optional[ValidationError]:
        value = (row.get(self.field_name) or "").strip()
        if not value:
            return None  # MandatoryFieldsValidator already reports blanks
        if parse_date(value, self.formats) is None:
            return ValidationError(
                row=row_number,
                booking_no=self._booking_no(row),
                reason=f"Invalid date: '{value}'",
            )
        return None


class NegativeAmountValidator(RowValidator):
    """Rejects a row whose amount is non-numeric or negative."""

    def __init__(self, field_name: str = "Amount"):
        self.field_name = field_name

    def validate(self, row: Dict[str, str], row_number: int) -> Optional[ValidationError]:
        value = (row.get(self.field_name) or "").strip()
        if not value:
            return None  # MandatoryFieldsValidator already reports blanks

        amount = parse_amount(value)
        if amount is None:
            return ValidationError(
                row=row_number,
                booking_no=self._booking_no(row),
                reason=f"Invalid amount: '{value}'",
            )
        if amount < 0:
            return ValidationError(
                row=row_number,
                booking_no=self._booking_no(row),
                reason=f"Negative amount: {amount}",
            )
        return None


class DuplicateBookingNoValidator(RowValidator):
    """Rejects a duplicate Booking No (in the DB or earlier in this file).
    Holds per-upload state, so use a fresh instance per upload."""

    def __init__(self, existing_booking_nos: Set[str]):
        self._existing = existing_booking_nos
        self._seen_in_file: Set[str] = set()

    def validate(self, row: Dict[str, str], row_number: int) -> Optional[ValidationError]:
        booking_no = self._booking_no(row)
        if not booking_no:
            return None  # MandatoryFieldsValidator already reports blanks

        if booking_no in self._seen_in_file or booking_no in self._existing:
            return ValidationError(row=row_number, booking_no=booking_no, reason="Duplicate Booking No")

        self._seen_in_file.add(booking_no)
        return None


class RowValidationPipeline:
    """Runs a row through every configured validator and collects all failures."""

    def __init__(self, validators: List[RowValidator]):
        self._validators = validators

    def validate(self, row: Dict[str, str], row_number: int) -> List[ValidationError]:
        errors = []
        for validator in self._validators:
            error = validator.validate(row, row_number)
            if error is not None:
                errors.append(error)
        return errors
