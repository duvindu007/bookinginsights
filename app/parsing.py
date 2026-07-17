"""
Small, pure parsing helpers with no side effects. Kept separate so both
validators (which only need to know *if* a value is parseable) and the
row transformer (which needs the *parsed* value) share one implementation
instead of two copies that can drift apart.
"""
from datetime import date, datetime
from typing import List, Optional

DEFAULT_DATE_FORMATS: List[str] = ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y"]


def parse_date(value: str, formats: List[str] = DEFAULT_DATE_FORMATS) -> Optional[date]:
    """Try each format in order; return the first successful parse, else None."""
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def parse_amount(value: str) -> Optional[float]:
    """Return the float value, or None if it isn't a valid number."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
