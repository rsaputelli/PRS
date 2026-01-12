##--- tools/send_venue_confirm.py

from __future__ import annotations
import os
import uuid
import datetime as dt
from typing import Dict, Any, Optional

import pytz
from supabase import create_client, Client

from lib.email_utils import gmail_send, build_html_table
from lib.calendar_utils import make_ics_bytes


# -----------------------------
# Secrets / config
# -----------------------------
def _get_secret(name: str, default: Optional[str] = None):
    try:
        import streamlit as st
        if hasattr(st, "secrets") and name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.environ.get(name, default)


TZ = _get_secret("APP_TZ", "America/New_York")
SUPABASE_URL = _get_secret("SUPABASE_URL")
SUPABASE_KEY = (
    _get_secret("SUPABASE_SERVICE_ROLE")
    or _get_secret("SUPABASE_KEY")
    or _get_secret("SUPABASE_ANON_KEY")
)

FROM_NAME = _get_secret("BAND_FROM_NAME", "PRS Scheduling")
FROM_EMAIL = _get_secret("BAND_FROM_EMAIL", "no-reply@prs.local")
CC_RAY = _get_secret("CC_RAY", "ray@lutinemanagement.com")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing Supabase credentials.")


# -----------------------------
# Supabase clients
# -----------------------------
def _sb() -> Client:
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    try:
        import streamlit as st
        at = st.session_state.get("sb_access_token")
        rt = st.session_state.get("sb_refresh_token")
        if at and rt:
            sb.auth.set_session(at, rt)
    except Exception:
        pass
    return sb


def _sb_admin() -> Client:
    return create_client(SUPABASE_URL, _get_secret("SUPABASE_SERVICE_ROLE"))


# -----------------------------
# Data fetch
# -----------------------------
def _fetch_gig_and_venue(sb: Client, gig_id: str) -> Dict[str, Any]:
    res = (
        sb.table("gigs")
        .select(
            "id, title, event_date, start_time, end_time, fee, "
            "is_private, agent_id, venue_id, notes"
        )
        .eq("id", gig_id)
        .limit(1)
        .execute()
    )

    rows = res.data or []
    if not rows:
        raise ValueError("Gig not found or RLS blocked.")

    g = rows[0]

    if g.get("is_private"):
        raise ValueError("Venue confirmation not allowed for private gigs.")

    if g.get("agent_id"):
        raise ValueError("Agent-managed gig; venue confirmation suppressed.")

    if not g.get("venue_id"):
        raise ValueError("No venue assigned.")

    v_res = (
        sb.table("venues")
        .select("id, name, contact_email, address_line1, city, state")
        .eq("id", g["venue_id"])
        .limit(1)
        .execute()
    )

    v_rows = v_res.data or []
    if not v_rows:
        raise ValueError("Venue not found or RLS blocked.")

    venue = v_rows[0]
    if not venue.get("contact_email"):
        raise ValueError("Venue has no contact email on file.")

    return {"gig": g, "venue": venue}


# -----------------------------
# Audit
# -----------------------------
def _insert_email_audit(*, token, gig_id, recipient_email, kind, status):
    admin = _sb_admin()
    admin.table("email_audit").insert(
        {
            "token": token,
            "gig_id": gig_id,
            "recipient_email": recipient_email,
            "kind": kind,
            "status": status,
            "ts": dt.datetime.utcnow().isoformat(),
        }
    ).execute()

def send_venue_confirm(gig_id: str) -> None:
    sb = _sb()
    payload = _fetch_gig_and_venue(sb, gig_id)

    token = uuid.uuid4().hex
    content = _build_venue_confirmation_content(payload, token=token)

    gig = payload["gig"]
    venue = payload["venue"]

    try:
        gmail_send(
            content["subject"],
            venue["contact_email"],
            content["html"],
            cc=[CC_RAY],
        )

        _insert_email_audit(
            token=token,
            gig_id=gig["id"],
            recipient_email=venue["contact_email"],
            kind="venue_confirm",
            status="sent",
        )

    except Exception as e:
        _insert_email_audit(
            token=token,
            gig_id=gig["id"],
            recipient_email=venue["contact_email"],
            kind="venue_confirm",
            status=f"error: {e}",
        )
        raise


def build_venue_confirmation_email(gig_id: str) -> Dict[str, Any]:
    """
    Build (but do not send) the venue confirmation email.
    Safe for UI preview.
    """
    sb = _sb()
    payload = _fetch_gig_and_venue(sb, gig_id)

    # Preview-only token
    token = "PREVIEW-TOKEN"

    content = _build_venue_confirmation_content(payload, token=token)

    venue = payload["venue"]

    return {
        "to": venue.get("contact_email"),
        "cc": [CC_RAY],
        **content,
    }


# -----------------------------
# Main sender
# -----------------------------
def send_venue_confirm(gig_id: str) -> None:
    sb = _sb()
    payload = _fetch_gig_and_venue(sb, gig_id)

    token = uuid.uuid4().hex
    content = _build_venue_confirmation_content(payload, token=token)

    gig = payload["gig"]
    venue = payload["venue"]

    try:
        gmail_send(
            content["subject"],
            venue["contact_email"],
            content["html"],
            cc=[CC_RAY],
        )

        _insert_email_audit(
            token=token,
            gig_id=gig["id"],
            recipient_email=venue["contact_email"],
            kind="venue_confirm",
            status="sent",
        )

    except Exception as e:
        _insert_email_audit(
            token=token,
            gig_id=gig["id"],
            recipient_email=venue["contact_email"],
            kind="venue_confirm",
            status=f"error: {e}",
        )
        raise
