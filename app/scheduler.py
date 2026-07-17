import logging
import os
from typing import List, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .analytics_service import AnalyticsService
from .daily_summary_service import DailySummaryService
from .database import SessionLocal
from .email_sender import EmailSender, SmtpEmailSender
from .repositories import SqlAlchemyBookingRepository

logger = logging.getLogger(__name__)

_scheduler: Optional[BackgroundScheduler] = None


def _get_recipients() -> List[str]:
    raw = os.environ.get("SUMMARY_EMAIL_RECIPIENTS", "")
    return [addr.strip() for addr in raw.split(",") if addr.strip()]


def build_email_sender() -> Optional[EmailSender]:
    host = os.environ.get("SMTP_HOST")
    if not host:
        return None
    return SmtpEmailSender(
        host=host,
        port=int(os.environ.get("SMTP_PORT", "587")),
        username=os.environ.get("SMTP_USERNAME", ""),
        password=os.environ.get("SMTP_PASSWORD", ""),
        from_email=os.environ.get("SMTP_FROM_EMAIL", "booking-manifest@example.com"),
        use_tls=os.environ.get("SMTP_USE_TLS", "true").lower() == "true",
    )


def run_daily_summary_job() -> None:
    """The actual job body — builds fresh services with their own DB session."""
    email_sender = build_email_sender()
    recipients = _get_recipients()
    if email_sender is None or not recipients:
        logger.warning("Daily summary email skipped — SMTP_HOST and/or SUMMARY_EMAIL_RECIPIENTS not configured")
        return

    db = SessionLocal()
    try:
        repository = SqlAlchemyBookingRepository(db)
        analytics_service = AnalyticsService(repository)
        DailySummaryService(analytics_service, email_sender, recipients).send_daily_summary()
    except Exception:
        # Must never crash the scheduler — log and retry tomorrow. The
        # manual /send endpoint doesn't go through this, so it still
        # surfaces errors to the caller.
        logger.exception("Scheduled daily summary email failed")
    finally:
        db.close()


def start_scheduler() -> None:
    global _scheduler

    if build_email_sender() is None or not _get_recipients():
        logger.warning(
            "Daily summary email is not configured (need SMTP_HOST and SUMMARY_EMAIL_RECIPIENTS) — scheduler not started."
        )
        return

    hour = int(os.environ.get("SUMMARY_EMAIL_HOUR", "7"))
    minute = int(os.environ.get("SUMMARY_EMAIL_MINUTE", "0"))

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        run_daily_summary_job,
        trigger=CronTrigger(hour=hour, minute=minute),
        id="daily_summary_email",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("Daily summary email scheduled for %02d:%02d UTC", hour, minute)


def stop_scheduler() -> None:
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
