# lib/email_utils.py
# Gmail send utility with Option B creds (client_id/secret/refresh_token) and backoff.
# - Prefers Streamlit secrets when available, else falls back to environment variables
# - Supports legacy GMAIL_TOKEN_JSON (raw JSON or path to JSON)
# - Keeps gentle throttle + exponential backoff
# - Returns the Gmail API response dict from gmail_send(...)

from __future__ import annotations

import base64
import json
import os
import random
import time
from typing import Iterable, List, Optional, Sequence, Tuple, Union

from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders

try:
    import streamlit as st  # type: ignore
except Exception:  # streamlit not always present (unit tests, CLI)
    st = None  # type: ignore

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ----------------------------
# Internal cache + throttle cfg
# ----------------------------
_SERVICE = None  # cached Gmail service

# "Gentle throttle": tiny base pause each attempt + jitter
_THROTTLE_SECONDS = 0.35
_JITTER_MAX = 0.25
_MAX_RETRIES = 5  # exponential backoff attempts on 429/5xx

_TOKEN_URI = "https://oauth2.googleapis.com/token"


# ----------------------------
# Helpers for config retrieval
# ----------------------------
def _cfg(name: str, default: Optional[str] = None) -> Optional[str]:
    """Prefer Streamlit secrets when available; fallback to environment."""
    # st.secrets -> highest precedence
    if st is not None:
        try:
            if hasattr(st, "secrets") and name in st.secrets:
                return st.secrets[name]  # type: ignore[index]
        except Exception:
            pass
    # env fallback
    return os.getenv(name, default)


def _split_scopes(raw: Optional[str]) -> List[str]:
    if not raw:
        return ["https://www.googleapis.com/auth/gmail.send"]
    # allow comma or space separated
    parts = [p.strip() for p in raw.replace(",", " ").split()]
    return [p for p in parts if p]


# ----------------------------
# Gmail service construction
# ----------------------------
def _gmail_service():
    """
    Build (and cache) a Gmail API service using one of:
      1) GMAIL_TOKEN_JSON (raw JSON text or a path to JSON containing refresh_token & client info)
      2) Option B: GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN, optional GMAIL_SCOPES
    """
    global _SERVICE
    if _SERVICE is not None:
        return _SERVICE

    # --- Path 1: Authorized user JSON (raw or path) ---
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

    # --- Path 2: client_id / client_secret / refresh_token ---
    client_id = _cfg("GMAIL_CLIENT_ID")
    client_secret = _cfg("GMAIL_CLIENT_SECRET")
    refresh_token = _cfg("GMAIL_REFRESH_TOKEN")
    scopes = _split_scopes(_cfg("GMAIL_SCOPES"))

    if client_id and client_secret and refresh_token:
        creds = Credentials(
            token=None,  # will be fetched using refresh_token
            refresh_token=refresh_token,
            token_uri=_TOKEN_URI,
            client_id=client_id,
            client_secret=client_secret,
            scopes=scopes,
        )
        if not creds.valid:
            creds.refresh(Request())
        _SERVICE = build("gmail", "v1", credentials=creds)
        return _SERVICE

    # If we got here, creds are missing in both styles
    raise RuntimeError(
        "Missing Gmail credentials. Provide either "
        "[GMAIL_TOKEN_JSON (raw JSON or path)] or "
        "[GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN, (optional) GMAIL_SCOPES] "
        "via Streamlit secrets or environment variables."
    )


# ----------------------------
# Message construction helpers
# ----------------------------
def _as_list(x: Union[str, Sequence[str], None]) -> List[str]:
    if x is None:
        return []
    if isinstance(x, str):
        return [x]
    return [s for s in x if s]


def _guess_plain_from_html(html: str) -> str:
    # Keep this simple on purpose; you already build good HTML.
    # This avoids duplicated effort to maintain a full HTML->text converter here.
    # If you want richer plain text in the future, feel free to swap in a HTML2Text lib.
    return (
        html.replace("<br>", "\n")
        .replace("<br/>", "\n")
        .replace("<br />", "\n")
        .replace("</p>", "\n\n")
        .replace("<p>", "")
        .replace("&nbsp;", " ")
    )


def _attach_file(part: MIMEMultipart, filepath: str, mime_main: str = "application", mime_sub: str = "octet-stream"):
    with open(filepath, "rb") as f:
        payload = f.read()
    maintype = mime_main
    subtype = mime_sub
    attachment = MIMEBase(maintype, subtype)
    attachment.set_payload(payload)
    encoders.encode_base64(attachment)
    filename = os.path.basename(filepath)
    attachment.add_header("Content-Disposition", f'attachment; filename="{filename}"')
    part.attach(attachment)


def _attach_blob(part: MIMEMultipart, filename: str, data: bytes, mime_main: str = "application", mime_sub: str = "octet-stream"):
    attachment = MIMEBase(mime_main, mime_sub)
    attachment.set_payload(data)
    encoders.encode_base64(attachment)
    attachment.add_header("Content-Disposition", f'attachment; filename="{filename}"')
    part.attach(attachment)


# ----------------------------
# Public API
# ----------------------------
def gmail_send(
    subject: str,
    to: Union[str, Sequence[str]],
    html: str,
    cc: Union[str, Sequence[str], None] = None,
    bcc: Union[str, Sequence[str], None] = None,
    attachments: Optional[Sequence[Union[str, Tuple[str, bytes]]]] = None,
    from_name: Optional[str] = None,
    from_email: Optional[str] = None,
    reply_to: Optional[str] = None,
) -> dict:
    """
    Send an email via Gmail API.

    Returns:
        The Gmail API response dict from users.messages.send(..).execute()
    """
    # Gentle throttle on every attempt (success or fail)
    time.sleep(_THROTTLE_SECONDS + random.random() * _JITTER_MAX)

    # Default From from secrets if not provided
    from_name = from_name or _cfg("BAND_FROM_NAME") or "PRS"
    from_email = from_email or _cfg("BAND_FROM_EMAIL") or "no-reply@example.com"
    from_header = f"{from_name} <{from_email}>"

    to_list = _as_list(to)
    cc_list = _as_list(cc)
    bcc_list = _as_list(bcc)

    # Build MIME message
    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = from_header
    msg["To"] = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    if reply_to:
        msg["Reply-To"] = reply_to

    # Attach body (plain + HTML)
    plain = _guess_plain_from_html(html)
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    # Attachments: allow file paths or (filename, bytes)
    if attachments:
        for att in attachments:
            if isinstance(att, tuple) and len(att) == 2:
                fname, blob = att
                _attach_blob(msg, fname, blob)
            elif isinstance(att, str):
                _attach_file(msg, att)
            else:
                # ignore unknown attachment shape to keep behavior forgiving
                continue

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    body = {"raw": raw}

    # Gmail send with retries
    service = _gmail_service()

    # Compose 'to' for API call (headers already set; including Bcc in headers is acceptable for API/raw)
    # If you prefer to not surface Bcc in headers, omit the "Bcc" header above and rely on headers here.
    if bcc_list:
        msg["Bcc"] = ", ".join(bcc_list)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        body = {"raw": raw}

    attempt = 0
    backoff = 1.0
    last_err: Optional[Exception] = None

    while attempt <= _MAX_RETRIES:
        try:
            # small per-attempt delay + jitter (gentle throttle)
            time.sleep(random.random() * _JITTER_MAX)
            resp = service.users().messages().send(userId="me", body=body).execute()
            return resp  # success: return Gmail API response
        except HttpError as e:
            last_err = e
            status = getattr(e, "status_code", None)
            # googleapiclient HttpError carries .resp.status sometimes
            try:
                status = status or (e.resp.status if hasattr(e, "resp") else None)
            except Exception:
                pass

            # Retry on 429 or 5xx
            if status in (429, 500, 502, 503, 504):
                time.sleep(backoff + random.random() * _JITTER_MAX)
                backoff *= 2.0
                attempt += 1
                continue
            # Non-retryable
            raise
        except Exception as e:
            last_err = e
            # Generic retry for transient network hiccups
            time.sleep(backoff + random.random() * _JITTER_MAX)
            backoff *= 2.0
            attempt += 1

    # Exhausted retries
    if last_err:
        raise last_err
    raise RuntimeError("Unknown error: gmail_send retries exhausted without exception detail.")
