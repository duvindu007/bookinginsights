
import logging
from datetime import date
from typing import List, Tuple

from .analytics_service import AnalyticsService
from .email_sender import EmailSender

logger = logging.getLogger(__name__)


class DailySummaryService:
    def __init__(
        self,
        analytics_service: AnalyticsService,
        email_sender: EmailSender,
        recipients: List[str],
        top_n: int = 5,
    ):
        self._analytics_service = analytics_service
        self._email_sender = email_sender
        self._recipients = recipients
        self._top_n = top_n

    def build_report(self) -> Tuple[str, str, str]:
        """Returns (subject, text_body, html_body)."""
        summary = self._analytics_service.get_summary()
        by_country = self._analytics_service.get_revenue_by_country()[: self._top_n]
        by_agent = self._analytics_service.get_bookings_by_agent()[: self._top_n]

        today = date.today().isoformat()
        subject = f"Booking Manifest \u2014 Daily Summary ({today})"

        text_lines = [
            f"Booking Manifest \u2014 Daily Summary ({today})",
            "=" * 44,
            "",
            f"Total bookings:        {summary['total_bookings']:,}",
            f"Total revenue:         {summary['total_revenue']:,.2f}",
            f"Average booking value: {summary['average_booking_value']:,.2f}",
            f"Date range:            {summary['earliest_booking_date']} \u2192 {summary['latest_booking_date']}",
            "",
            "Status breakdown:",
        ]
        for status, count in summary["status_breakdown"].items():
            text_lines.append(f"  {status:<12} {count:,}")

        text_lines += ["", f"Top {self._top_n} countries by revenue:"]
        for row in by_country:
            text_lines.append(f"  {row['country']:<20} {row['revenue']:>12,.2f}  ({row['bookings']:,} bookings)")

        text_lines += ["", f"Top {self._top_n} agents by bookings:"]
        for row in by_agent:
            text_lines.append(f"  {row['agent']:<20} {row['bookings']:>6,} bookings  ({row['revenue']:,.2f} revenue)")

        text_body = "\n".join(text_lines)

        status_rows_html = "".join(
            f"<tr><td>{status}</td><td style='text-align:right'>{count:,}</td></tr>"
            for status, count in summary["status_breakdown"].items()
        )
        country_rows_html = "".join(
            f"<tr><td>{row['country']}</td><td style='text-align:right'>{row['revenue']:,.2f}</td>"
            f"<td style='text-align:right'>{row['bookings']:,}</td></tr>"
            for row in by_country
        )
        agent_rows_html = "".join(
            f"<tr><td>{row['agent']}</td><td style='text-align:right'>{row['bookings']:,}</td>"
            f"<td style='text-align:right'>{row['revenue']:,.2f}</td></tr>"
            for row in by_agent
        )
        html_body = f"""
        <html><body style="font-family: -apple-system, Arial, sans-serif; color: #14213D;">
          <h2>Booking Manifest &mdash; Daily Summary ({today})</h2>
          <table cellpadding="6" style="border-collapse: collapse;">
            <tr><td><strong>Total bookings</strong></td><td>{summary['total_bookings']:,}</td></tr>
            <tr><td><strong>Total revenue</strong></td><td>{summary['total_revenue']:,.2f}</td></tr>
            <tr><td><strong>Average booking value</strong></td><td>{summary['average_booking_value']:,.2f}</td></tr>
            <tr><td><strong>Date range</strong></td><td>{summary['earliest_booking_date']} &rarr; {summary['latest_booking_date']}</td></tr>
          </table>

          <h3>Status breakdown</h3>
          <table cellpadding="6" style="border-collapse: collapse; border: 1px solid #ddd;">{status_rows_html}</table>

          <h3>Top {self._top_n} countries by revenue</h3>
          <table cellpadding="6" style="border-collapse: collapse; border: 1px solid #ddd;">
            <tr><th align="left">Country</th><th align="right">Revenue</th><th align="right">Bookings</th></tr>
            {country_rows_html}
          </table>

          <h3>Top {self._top_n} agents by bookings</h3>
          <table cellpadding="6" style="border-collapse: collapse; border: 1px solid #ddd;">
            <tr><th align="left">Agent</th><th align="right">Bookings</th><th align="right">Revenue</th></tr>
            {agent_rows_html}
          </table>
        </body></html>
        """

        return subject, text_body, html_body

    def send_daily_summary(self) -> None:
        if not self._recipients:
            logger.warning("No recipients configured — skipping daily summary email")
            return

        subject, text_body, html_body = self.build_report()
        self._email_sender.send(self._recipients, subject, text_body, html_body)
