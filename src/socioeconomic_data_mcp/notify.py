"""Email notifications (e.g. a new connector registration).

Configured entirely via env so no secrets live in code; a no-op if unconfigured.
Sending happens on a background thread so it never blocks the async request handlers.

Two transports, in order of preference:
  1. Brevo transactional API  — set BREVO_API_KEY (+ NEWSLETTER_SENDER_EMAIL / NAME).
  2. Plain SMTP relay         — set MCP_SMTP_HOST/PORT/USER/PASS (+ MCP_SMTP_FROM).

Recipient is MCP_ALERT_EMAIL.
"""

from __future__ import annotations

import logging
import os
import smtplib
import threading
from email.message import EmailMessage

from . import common

logger = logging.getLogger("socioeconomic_data_mcp.notify")

_BREVO_URL = "https://api.brevo.com/v3/smtp/email"


def _send_brevo(api_key: str, subject: str, body: str, recipient: str) -> None:
    sender_email = (os.environ.get("MCP_SMTP_FROM") or os.environ.get("NEWSLETTER_SENDER_EMAIL") or "").strip()
    sender_name = (os.environ.get("NEWSLETTER_SENDER_NAME") or "Socio-Economic Data MCP").strip()
    payload = {
        "sender": {"email": sender_email, "name": sender_name},
        "to": [{"email": recipient}],
        "subject": subject,
        "textContent": body,
    }
    resp = common.get_client().post(
        _BREVO_URL, json=payload, headers={"api-key": api_key, "accept": "application/json"}
    )
    resp.raise_for_status()


def _send_smtp(subject: str, body: str, recipient: str) -> None:
    host = os.environ.get("MCP_SMTP_HOST", "").strip()
    port = int(os.environ.get("MCP_SMTP_PORT", "587") or 587)
    user = os.environ.get("MCP_SMTP_USER", "").strip() or None
    password = os.environ.get("MCP_SMTP_PASS", "")
    sender = os.environ.get("MCP_SMTP_FROM", "").strip() or user or "socioeconomic-data-mcp@localhost"
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, sender, recipient
    msg.set_content(body)
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=15) as s:
            if user:
                s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=15) as s:
            s.starttls()
            if user:
                s.login(user, password)
            s.send_message(msg)


def _send(subject: str, body: str) -> None:
    recipient = os.environ.get("MCP_ALERT_EMAIL", "").strip()
    if not recipient:
        return  # notifications not configured
    brevo_key = os.environ.get("BREVO_API_KEY", "").strip()
    try:
        if brevo_key:
            _send_brevo(brevo_key, subject, body, recipient)
        elif os.environ.get("MCP_SMTP_HOST", "").strip():
            _send_smtp(subject, body, recipient)
        else:
            return
        logger.info("notification email sent to %s", recipient)
    except Exception as exc:  # noqa: BLE001 - notifications must never crash the request
        logger.warning("notification email failed: %s", exc)


def send_async(subject: str, body: str) -> None:
    """Fire-and-forget email on a daemon thread (never blocks, never raises)."""
    threading.Thread(target=_send, args=(subject, body), daemon=True).start()
