from __future__ import annotations

import mimetypes
import os
import queue
import smtplib
import threading
from email.message import EmailMessage
from pathlib import Path

import requests


class NotificationService:
    """Asynchronous Telegram and SMTP delivery with secrets kept out of logs."""

    def __init__(self):
        self.telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com").strip()
        self.smtp_port = int(os.getenv("SMTP_PORT") or "587")
        self.smtp_user = os.getenv("SMTP_USER", "").strip()
        self.smtp_password = os.getenv("SMTP_APP_PASSWORD", "").strip()
        self.email_to = [
            value.strip()
            for value in os.getenv("REPORT_EMAIL_TO", "").split(",")
            if value.strip()
        ]
        self.items: queue.Queue[tuple | None] = queue.Queue()
        self.last_error = ""
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_token and self.telegram_chat_id)

    @property
    def email_enabled(self) -> bool:
        return bool(
            self.smtp_host
            and self.smtp_user
            and self.smtp_password
            and self.email_to
        )

    def send_text(self, message: str) -> None:
        self.items.put(("TEXT", str(message)))

    def send_report(
        self,
        subject: str,
        message: str,
        attachments: list[Path],
    ) -> None:
        existing = [Path(path) for path in attachments if Path(path).exists()]
        self.items.put(("REPORT", subject, message, existing))

    def close(self) -> None:
        self.items.put(None)
        self.worker.join(60)

    def _run(self) -> None:
        while True:
            item = self.items.get()
            if item is None:
                return
            if item[0] == "TEXT":
                self._attempt(self._send_telegram_text, item[1])
            else:
                _, subject, message, attachments = item
                self._attempt(self._send_telegram_text, message)
                for attachment in attachments:
                    self._attempt(self._send_telegram_document, attachment, subject)
                self._attempt(self._send_email, subject, message, attachments)

    def _attempt(self, function, *args) -> None:
        try:
            function(*args)
        except Exception as error:
            self.last_error = f"{type(error).__name__}: {error}"
            print("Notification delivery error:", self.last_error)

    def _send_telegram_text(self, message: str) -> None:
        if not self.telegram_enabled:
            return
        response = requests.post(
            f"https://api.telegram.org/bot{self.telegram_token}/sendMessage",
            data={"chat_id": self.telegram_chat_id, "text": message[:4000]},
            timeout=30,
        )
        response.raise_for_status()

    def _send_telegram_document(self, path: Path, caption: str) -> None:
        if not self.telegram_enabled:
            return
        with path.open("rb") as handle:
            response = requests.post(
                f"https://api.telegram.org/bot{self.telegram_token}/sendDocument",
                data={"chat_id": self.telegram_chat_id, "caption": caption[:900]},
                files={"document": (path.name, handle)},
                timeout=90,
            )
        response.raise_for_status()

    def _send_email(
        self,
        subject: str,
        message: str,
        attachments: list[Path],
    ) -> None:
        if not self.email_enabled:
            return
        email = EmailMessage()
        email["Subject"] = subject
        email["From"] = self.smtp_user
        email["To"] = ", ".join(self.email_to)
        email.set_content(message)
        for path in attachments:
            mime, _ = mimetypes.guess_type(path.name)
            major, minor = (mime or "application/octet-stream").split("/", 1)
            email.add_attachment(
                path.read_bytes(),
                maintype=major,
                subtype=minor,
                filename=path.name,
            )
        with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30) as server:
            server.starttls()
            server.login(self.smtp_user, self.smtp_password)
            server.send_message(email)
