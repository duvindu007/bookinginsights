"""
Small, pure parsing helpers with no side effects. Kept separate so both
validators (which only need to know *if* a value is parseable) and the
row transformer (which needs the *parsed* value) share one implementation
instead of two copies that can drift apart.
"""
import math
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
    """
    Return the float value, or None if it isn't a valid finite number.
    Python's float() happily parses "nan"/"inf"/"-inf"/"infinity" (any
    case) into real NaN/Infinity values instead of raising — those are
    never valid monetary amounts, and a NaN in the database corrupts any
    SUM()/AVG() it touches (and can't be JSON-serialized at all), so they
    must be rejected here rather than silently accepted as "valid".
    """
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed
