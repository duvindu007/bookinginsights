"""Depends only on BookingRepository, same as BookingUploadService. Thin
pass-through today; the seam for caching/permissions later if needed."""
import logging
from typing import Dict, List

from .repositories import BookingRepository

logger = logging.getLogger(__name__)


class AnalyticsService:
    def __init__(self, repository: BookingRepository):
        self._repository = repository

    def get_summary(self) -> Dict:
        logger.info("Computing booking summary")
        return self._repository.get_summary()

    def get_revenue_by_country(self) -> List[Dict]:
        logger.info("Computing revenue by country")
        return self._repository.get_revenue_by_country()

    def get_bookings_by_agent(self) -> List[Dict]:
        logger.info("Computing bookings by agent")
        return self._repository.get_bookings_by_agent()

    def get_monthly_revenue_trend(self) -> List[Dict]:
        logger.info("Computing monthly revenue trend")
        return self._repository.get_monthly_revenue_trend()
