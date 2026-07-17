
import logging
import smtplib
from abc import ABC, abstractmethod
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List

logger = logging.getLogger(__name__)


class EmailSender(ABC):
    @abstractmethod
    def send(self, to: List[str], subject: str, text_body: str, html_body: str = None) -> None:
        raise NotImplementedError


class SmtpEmailSender(EmailSender):
    def __init__(self, host: str, port: int, username: str, password: str, from_email: str, use_tls: bool = True):
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._from_email = from_email
        self._use_tls = use_tls

    def send(self, to: List[str], subject: str, text_body: str, html_body: str = None) -> None:
        if not to:
            logger.warning("send() called with no recipients — skipping")
            return

        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message["From"] = self._from_email
        message["To"] = ", ".join(to)
        message.attach(MIMEText(text_body, "plain"))
        if html_body:
            message.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(self._host, self._port, timeout=15) as server:
            if self._use_tls:
                server.starttls()
            if self._username:
                server.login(self._username, self._password)
            server.sendmail(self._from_email, to, message.as_string())

        logger.info("Sent email '%s' to %d recipient(s)", subject, len(to))
