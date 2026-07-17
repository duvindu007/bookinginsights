"""Coordinates one upload: read -> validate -> insert -> log. Depends only
on CsvReader/BookingRepository/ErrorLogger abstractions and a
validator_factory — never constructs a concrete database/filesystem/validator."""
import logging
from typing import Callable, Dict, List, Set

from .csv_reader import CsvReader
from .error_logger import ErrorLogger
from .parsing import parse_amount, parse_date
from .repositories import BookingRepository
from .schemas import UploadResult
from .validators import RowValidationPipeline, RowValidator

logger = logging.getLogger(__name__)

ValidatorFactory = Callable[[Set[str]], List[RowValidator]]


class BookingUploadService:
    def __init__(
        self,
        repository: BookingRepository,
        error_logger: ErrorLogger,
        csv_reader: CsvReader,
        validator_factory: ValidatorFactory,
    ):
        self._repository = repository
        self._error_logger = error_logger
        self._csv_reader = csv_reader
        self._validator_factory = validator_factory

    def process(self, filename: str, csv_text: str) -> UploadResult:
        logger.info("Upload started: filename=%s size_bytes=%d", filename, len(csv_text))

        rows = self._csv_reader.read(csv_text)  # raises CsvHeaderError on bad headers

        existing_booking_nos = self._repository.get_existing_booking_numbers()
        logger.debug("Loaded %d existing booking numbers for duplicate check", len(existing_booking_nos))
        pipeline = RowValidationPipeline(self._validator_factory(existing_booking_nos))

        valid_rows: List[Dict] = []
        errors = []

        for row_number, row in enumerate(rows, start=2):  # row 1 is the header
            row_errors = pipeline.validate(row, row_number)
            if row_errors:
                errors.extend(row_errors)
                continue
            valid_rows.append(self._to_record(row))

        inserted = self._repository.bulk_insert(valid_rows) if valid_rows else 0
        logger.info(
            "Validation complete: filename=%s valid_rows=%d rejected_rows=%d",
            filename, len(valid_rows), len(errors),
        )

        log_file = self._error_logger.log(filename, errors)
        if log_file:
            logger.warning(
                "Rejected %d row(s) from %s; details written to %s",
                len(errors), filename, log_file,
            )

        logger.info("Upload finished: filename=%s rows_inserted=%d", filename, inserted)

        return UploadResult(
            filename=filename,
            rows_inserted=inserted,
            errors=errors,
            error_log_file=log_file,
        )

    @staticmethod
    def _to_record(row: Dict[str, str]) -> Dict:
        """A row that passed every validator is safe to parse without re-checking."""
        return {
            "booking_no": row["Booking No"].strip(),
            "agent": row["Agent"].strip(),
            "country": row["Country"].strip(),
            "tour_type": row["Tour Type"].strip(),
            "booking_date": parse_date(row["Booking Date"].strip()),
            "amount": parse_amount(row["Amount"].strip()),
            "status": row["Status"].strip(),
        }
