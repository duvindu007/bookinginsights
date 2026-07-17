import csv
import io
from typing import Iterable, List


class CsvHeaderError(ValueError):
    """Raised when the uploaded CSV is missing one or more required columns."""


class CsvReader:
    """Turns raw CSV text into row dictionaries, after checking the header."""

    def __init__(self, required_headers: List[str]):
        self.required_headers = required_headers

    def read(self, text: str) -> Iterable[dict]:
        reader = csv.DictReader(io.StringIO(text))
        missing = [h for h in self.required_headers if h not in (reader.fieldnames or [])]
        if missing:
            raise CsvHeaderError(f"CSV is missing required column(s): {', '.join(missing)}")
        return reader
