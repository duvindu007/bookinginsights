import logging
import math
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Dict, List, Set

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .lookup_repository import LookupRepository
from .models import Agent, Booking, Country, Status, TourType

logger = logging.getLogger(__name__)


def _safe_float(value, fallback: float = 0.0) -> float:
  
    if value is None or not math.isfinite(value):
        if value is not None:
            logger.warning("Non-finite value (%s) encountered in aggregate; using %s", value, fallback)
        return fallback
    return value


class BookingRepository(ABC):
    @abstractmethod
    def get_existing_booking_numbers(self) -> Set[str]:
        raise NotImplementedError

    @abstractmethod
    def bulk_insert(self, rows: List[Dict]) -> int:

        raise NotImplementedError

    @abstractmethod
    def count(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def get_summary(self) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def get_revenue_by_country(self) -> List[Dict]:
        raise NotImplementedError

    @abstractmethod
    def get_bookings_by_agent(self) -> List[Dict]:
        raise NotImplementedError

    @abstractmethod
    def get_monthly_revenue_trend(self) -> List[Dict]:
        raise NotImplementedError


class SqlAlchemyBookingRepository(BookingRepository):
    def __init__(self, db: Session, batch_size: int = 5000):
        self._db = db
        self._batch_size = batch_size
        self._agents = LookupRepository(db, Agent)
        self._countries = LookupRepository(db, Country)
        self._tour_types = LookupRepository(db, TourType)
        self._statuses = LookupRepository(db, Status)

    def get_existing_booking_numbers(self) -> Set[str]:
        return {row[0] for row in self._db.execute(select(Booking.booking_no)).all()}

    def bulk_insert(self, rows: List[Dict]) -> int:
        if not rows:
            return 0

        # One batched get-or-create per entity type, not one query per row.
        # Creates any agent/country/tour type/status not already present.
        agent_ids = self._agents.get_or_create_many({r["agent"] for r in rows})
        country_ids = self._countries.get_or_create_many({r["country"] for r in rows})
        tour_type_ids = self._tour_types.get_or_create_many({r["tour_type"] for r in rows})
        status_ids = self._statuses.get_or_create_many({r["status"] for r in rows})

        resolved_rows = [
            {
                "booking_no": r["booking_no"],
                "agent_id": agent_ids[r["agent"]],
                "country_id": country_ids[r["country"]],
                "tour_type_id": tour_type_ids[r["tour_type"]],
                "booking_date": r["booking_date"],
                "amount": r["amount"],
                "status_id": status_ids[r["status"]],
            }
            for r in rows
        ]

        inserted = 0
        for start in range(0, len(resolved_rows), self._batch_size):
            batch = resolved_rows[start:start + self._batch_size]
            self._db.bulk_insert_mappings(Booking, batch)
            self._db.commit()
            inserted += len(batch)
            logger.debug("Inserted batch: %d/%d rows committed", inserted, len(resolved_rows))
        logger.info("Bulk insert complete: %d row(s) inserted", inserted)
        return inserted

    def count(self) -> int:
        return self._db.query(Booking).count()

    def get_summary(self) -> Dict:
        total_bookings = self._db.query(func.count(Booking.id)).scalar() or 0
        total_revenue = _safe_float(self._db.query(func.sum(Booking.amount)).scalar())
        average_booking_value = (total_revenue / total_bookings) if total_bookings else 0.0

        status_rows = (
            self._db.query(Status.name, func.count(Booking.id))
            .join(Booking, Booking.status_id == Status.id)
            .group_by(Status.name)
            .all()
        )
        date_range = self._db.query(func.min(Booking.booking_date), func.max(Booking.booking_date)).first()
        earliest, latest = date_range if date_range else (None, None)

        return {
            "total_bookings": total_bookings,
            "total_revenue": round(total_revenue, 2),
            "average_booking_value": round(average_booking_value, 2),
            "status_breakdown": {status: count for status, count in status_rows},
            "earliest_booking_date": earliest.isoformat() if earliest else None,
            "latest_booking_date": latest.isoformat() if latest else None,
        }

    def get_revenue_by_country(self) -> List[Dict]:
        rows = (
            self._db.query(Country.name, func.count(Booking.id), func.sum(Booking.amount))
            .join(Booking, Booking.country_id == Country.id)
            .group_by(Country.name)
            .order_by(func.sum(Booking.amount).desc())
            .all()
        )
        return [
            {"country": country, "bookings": bookings, "revenue": round(_safe_float(revenue), 2)}
            for country, bookings, revenue in rows
        ]

    def get_bookings_by_agent(self) -> List[Dict]:
        rows = (
            self._db.query(Agent.name, func.count(Booking.id), func.sum(Booking.amount))
            .join(Booking, Booking.agent_id == Agent.id)
            .group_by(Agent.name)
            .order_by(func.count(Booking.id).desc())
            .all()
        )
        return [
            {"agent": agent, "bookings": bookings, "revenue": round(_safe_float(revenue), 2)}
            for agent, bookings, revenue in rows
        ]

    def get_monthly_revenue_trend(self) -> List[Dict]:
        # Grouped in Python rather than a DB-specific date_trunc/strftime.
        rows = self._db.query(Booking.booking_date, Booking.amount).all()

        aggregated: Dict[str, Dict[str, float]] = defaultdict(lambda: {"bookings": 0, "revenue": 0.0})
        for booking_date, amount in rows:
            month_key = booking_date.strftime("%Y-%m")
            aggregated[month_key]["bookings"] += 1
            aggregated[month_key]["revenue"] += _safe_float(amount)

        return [
            {"month": month, "bookings": data["bookings"], "revenue": round(data["revenue"], 2)}
            for month, data in sorted(aggregated.items())
        ]
