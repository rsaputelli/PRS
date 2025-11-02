# lib/email_utils.py
from __future__ import annotations
import os
import json
import time
import random
import base64
from typing import Optional, List
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ----------------------------
# Internal cache + throttle cfg
# ----------------------------
_SERVICE = None                        # cache the Gmail service client per run
_MIN_SLEEP = 0.075                     # 75ms base delay between sends
_MAX_SLEEP = 0.150                     # up to 150ms to add jitter
_MAX_RETRIES = 5                       # exponential backoff attempts on 429/5xx

def _gmail_service():
    """
    Build (or return cached) Gmail API client.
    Accepts GMAIL_TOKEN_JSON as raw JSON or as a filesystem path.
    """
    global _SERVICE
    if _SERVICE is not None:
        return _SERVICE

    token_json = os.getenv("GMAIL_TOKEN_JSON")
    # Retained for parity if other modules use it (not strictly required here).
    _ = os.getenv("GMAIL_CLIENT_SECRET_JSON")

    if not token_json:
        raise RuntimeError("GMAIL_TOKEN_JSON env var is required")

    if token_json.strip().startswith("{"):
        creds = Credentials.from_authorized_user_info(json.loads(token_json))
    else:
        with open(token_json, "r", encoding="utf-8") as f:
            creds = Credentials.from_authorized_user_info(json.loads(f.read()))

    _SERVICE = build("gmail", "v1", credentials=creds)
    return _SERVICE


def _sleep_with_jitter():
    time.sleep(_MIN_SLEEP + random.random() * (_MAX_SLEEP - _MIN_SLEEP))


def _send_with_retry(service, raw_msg_b64: str):
    """
    Send a Gmail message with basic retry on rate limits / server errors.
    Retries: 429, 500, 502, 503, 504
    """
    attempt = 0
    backoff = 0.5  # seconds
    while True:
        try:
            return service.users().messages().send(
                userId="me", body={"raw": raw_msg_b64}
            ).execute()
        except HttpError as e:
            status = getattr(e, "status_code", None) or getattr(e, "resp", {}).get("status")
            try:
                status = int(status)
            except Exception:
                status = None

            if status in {429, 500, 502, 503, 504} and attempt < _MAX_RETRIES:
                time.sleep(backoff + random.random() * 0.25)
                backoff *= 2
                attempt += 1
                continue
            # Not retriable or maxed out
            raise
        finally:
            # Gentle throttle on every attempt (success or fail)
            _sleep_with_jitter()


def gmail_send(
    subject: str,
    to_email: str,
    html_body: str,
    cc: Optional[List[str]] = None,
    attachments: Optional[list] = None
):
    """
    Send an HTML email via Gmail API.
    attachments: list of dicts with keys:
        - filename: str
        - content: bytes
        - mime: optional str (e.g., "text/calendar")
    """
    msg = MIMEMultipart()
    msg["to"] = to_email
    if cc:
        msg["cc"] = ", ".join(cc)
    msg["subject"] = subject
    msg.attach(MIMEText(html_body, "html"))

    for att in attachments or []:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(att["content"])
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f"attachment; filename=\"{att['filename']}\"")
        if att.get("mime"):
            part.add_header("Content-Type", att["mime"])
        msg.attach(part)

    raw_b64 = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service = _gmail_service()
    _send_with_retry(service, raw_b64)


def build_html_table(rows: list[dict]) -> str:
    """
    Render a simple HTML table from a list of dicts.
    Keys in the first row determine column order.
    """
    if not rows:
        return ""
    cols = list(rows[0].keys())
    th = "".join(
        f"<th style='text-align:left;padding:6px;border-bottom:1px solid #ddd'>{c}</th>"
        for c in cols
    )
    trs = []
    for r in rows:
        tds = "".join(
            f"<td style='padding:6px;border-bottom:1px solid #eee'>{r.get(c, '')}</td>"
            for c in cols
        )
        trs.append(f"<tr>{tds}</tr>")
    return (
        "<table cellspacing=0 cellpadding=0 style='border-collapse:collapse'>"
        f"<thead><tr>{th}</tr></thead><tbody>{''.join(trs)}</tbody></table>"
    )
