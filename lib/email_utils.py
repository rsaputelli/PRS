# lib/email_utils.py
from __future__ import annotations
import os
import json
import time
import random
import base64
from typing import Optional, List, Sequence, Dict, Any

from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders

try:
    import streamlit as st  # optional, for st.secrets
except Exception:
    st = None  # type: ignore

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ----------------------------
# Internal cache + throttle cfg
# ----------------------------
_SERVICE = None                        # cache the Gmail service client per run

# "gentle throttle": keep your original timings
_MIN_SLEEP = 0.075                     # 75ms base delay between sends
_MAX_SLEEP = 0.150                     # up to 150ms to add jitter
_MAX_RETRIES = 5                       # exponential backoff attempts on 429/5xx

_TOKEN_URI = "https://oauth2.googleapis.com/token"


# ----------------------------
# Secrets/env helpers
# ----------------------------
def _cfg(name: str, default: Optional[str] = None) -> Optional[str]:
    """
    Prefer Streamlit secrets (when available) then fall back to environment.
    """
    if st is not None:
        try:
            if hasattr(st, "secrets") and name in st.secrets:
                return st.secrets[name]  # type: ignore[index]
        except Exception:
            pass
    return os.getenv(name, default)


def _sleep_with_jitter():
    time.sleep(_MIN_SLEEP + random.random() * (_MAX_SLEEP - _MIN_SLEEP))


# ----------------------------
# Gmail service
# ----------------------------
def _gmail_service():
    """
    Build (or return cached) Gmail API client.

    Supports two credential styles:

    A) JSON blobs (legacy):
       - GMAIL_TOKEN_JSON: authorized user JSON (raw JSON string or path)

    B) Piecemeal (Option B, recommended):
       - GMAIL_CLIENT_ID
       - GMAIL_CLIENT_SECRET
       - GMAIL_REFRESH_TOKEN
       - (optional) GMAIL_SCOPES (comma-separated or space-separated)
    """
    global _SERVICE
    if _SERVICE is not None:
        return _SERVICE

    # --- Path A: authorized user JSON (raw or path) ---
    token_json = _cfg("GMAIL_TOKEN_JSON")
    if token_json:
        if token_json.strip().startswith("{"):
            user_info = json.loads(token_json)
        else:
            with open(token_json, "r", encoding="utf-8") as f:
                user_info = json.loads(f.read())
        creds = Credentials.from_authorized_user_info(user_info)
        if not creds.valid and creds.refresh_token:
            creds.refresh(Request())
        _SERVICE = build("gmail", "v1", credentials=creds)
        return _SERVICE

    # --- Path B: client_id / client_secret / refresh_token (Option B) ---
    client_id = _cfg("GMAIL_CLIENT_ID")
    client_secret = _cfg("GMAIL_CLIENT_SECRET")
    refresh_token = _cfg("GMAIL_REFRESH_TOKEN")
    scopes_raw = _cfg("GMAIL_SCOPES", "https://www.googleapis.com/auth/gmail.send")
    scopes = [s for s in [p.strip() for p in (scopes_raw or "").replace(",", " ").split()] if s]

    if client_id and client_secret and refresh_token:
        creds = Credentials(
            token=None,  # will be fetched via refresh_token
            refresh_token=refresh_token,
            token_uri=_TOKEN_URI,
            client_id=client_id,
            client_secret=client_secret,
            scopes=scopes,
        )
        if not creds.valid:
            try:
                creds.refresh(Request())
            except Exception:
                # If refresh fails now, the API call will raise and retry logic will handle.
                pass
        _SERVICE = build("gmail", "v1", credentials=creds)
        return _SERVICE

    # If we got here, creds are missing in both styles
    raise RuntimeError(
        "Missing Gmail credentials. Provide either "
        "[GMAIL_TOKEN_JSON (raw JSON or path)] or "
        "[GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN, (optional) GMAIL_SCOPES] "
        "via Streamlit secrets or environment variables."
    )


def _send_with_retry(service, raw_msg_b64: str):
    """
    Send a Gmail message with basic retry on rate limits / server errors.
    Retries: 429, 500, 502, 503, 504
    """
    attempt = 0
    backoff = 0.5  # seconds
    last_err: Optional[Exception] = None

    while True:
        try:
            # per-attempt tiny jitter to reduce burstiness
            time.sleep(random.random() * 0.25)
            resp = service.users().messages().send(
                userId="me", body={"raw": raw_msg_b64}
            ).execute()
            return resp
        except HttpError as e:
            last_err = e
            status = None
            try:
                status = getattr(e, "status_code", None)
            except Exception:
                pass
            try:
                if status is None and hasattr(e, "resp") and hasattr(e.resp, "status"):
                    status = e.resp.status  # type: ignore[attr-defined]
            except Exception:
                pass

            try:
                if status is not None:
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


# ----------------------------
# Public API
# ----------------------------
def gmail_send(
    subject: str,
    to_email: str,
    html_body: str,
    cc: Optional[List[str]] = None,
    attachments: Optional[List[Dict[str, Any]]] = None
):
    """
    Send an HTML email via Gmail API.
    Returns the Gmail API response dict on success (truthy), raises on failure.

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
        part.add_header("Content-Disposition", f'attachment; filename="{att["filename"]}"')
        if att.get("mime"):
            part.add_header("Content-Type", att["mime"])
        msg.attach(part)

    raw_b64 = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service = _gmail_service()
    return _send_with_retry(service, raw_b64)


def build_html_table(rows: List[Dict[str, Any]]) -> str:
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

