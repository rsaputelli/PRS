#--tools/send_agent_confirm.py

from __future__ import annotations
import os
import uuid
import datetime as dt
from typing import Dict, Any, Optional

from supabase import create_client, Client
from lib.email_utils import gmail_send

# -----------------------------
# Secrets / config (mirror sound-tech)
# -----------------------------
def _get_secret(name: str, default: Optional[str] = None):
    try:
        import streamlit as st
        if hasattr(st, "secrets") and name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.environ.get(name, default)

def _is_dry_run() -> bool:
    val = _get_secret("AGENT_EMAIL_DRY_RUN", "0")
    return str(val).lower() in {"1", "true", "yes", "on"}

SUPABASE_URL = _get_secret("SUPABASE_URL")
SUPABASE_KEY = (
    _get_secret("SUPABASE_SERVICE_ROLE")
    or _get_secret("SUPABASE_KEY")
    or _get_secret("SUPABASE_ANON_KEY")
)

CC_RAY = _get_secret("CC_RAY", "ray@lutinemanagement.com")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError(
        "Missing Supabase credentials: set SUPABASE_URL and one of "
        "SUPABASE_SERVICE_ROLE / SUPABASE_KEY / SUPABASE_ANON_KEY."
    )

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
            sb.auth.set_session(access_token=at, refresh_token=rt)
    except Exception:
        pass
    return sb

def _sb_admin() -> Client:
    sr = _get_secret("SUPABASE_SERVICE_ROLE") or SUPABASE_KEY
    return create_client(SUPABASE_URL, sr)

# -----------------------------
# Fetch helpers
# -----------------------------
def _fetch_gig(gig_id: str) -> Dict[str, Any]:
    res = (
        _sb().table("gigs")
        .select("id, title, event_date, start_time, end_time, venue_id, agent_id")
        .eq("id", gig_id)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    if not rows:
        raise ValueError(f"Gig {gig_id} not found or not accessible (RLS).")
    return rows[0]

def _fetch_agent(agent_id: str) -> Optional[Dict[str, Any]]:
    if not agent_id:
        return None
    res = _sb().table("agents").select("id, display_name, first_name, email").eq("id", agent_id).limit(1).execute()
    rows = res.data or []
    return rows[0] if rows else None

def _fetch_venue(venue_id: str | None) -> Dict[str, Any]:
    if not venue_id:
        return {}
    res = _sb().table("venues").select(
        "name, address_line1, address_line2, city, state, postal_code, contact_name, contact_phone, contact_email"
    ).eq("id", venue_id).limit(1).execute()
    rows = res.data or []
    return rows[0] if rows else {}

# -----------------------------
# Formatting helpers
# -----------------------------
def _fmt_time12(t: Optional[str]) -> str:
    from datetime import datetime
    if not t:
        return ""
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(t, fmt).strftime("%I:%M %p").lstrip("0")
        except ValueError:
            continue
    return str(t or "")

def _nz(v) -> str:
    return "" if v is None else str(v).strip()

def _fmt_addr(v: Dict[str, Any]) -> str:
    parts = []
    l1 = _nz(v.get("address_line1")); l2 = _nz(v.get("address_line2"))
    city = _nz(v.get("city")); state = _nz(v.get("state")); pc = _nz(v.get("postal_code"))
    if l1: parts.append(l1)
    if l2: parts.append(l2)
    tail = " ".join(p for p in [city, state, pc] if p).strip()
    if tail: parts.append(tail)
    return " | ".join(parts)

# -----------------------------
# Audit
# -----------------------------
def _insert_email_audit(*, token: str, gig_id: str, recipient_email: str, kind: str, status: str, detail: dict):
    _sb_admin().table("email_audit").insert({
        "token": token,
        "gig_id": gig_id,
        "event_id": None,
        "recipient_email": recipient_email,
        "kind": kind,
        "status": status,
        "ts": dt.datetime.utcnow().isoformat(),
        "detail": detail,
    }).execute()

# -----------------------------
# Public API
# -----------------------------
def send_agent_confirm(gig_id: str, cc: Optional[list[str]] = None) -> None:
    """Send a single agent confirmation email for this gig; audits every outcome."""
    gig = _fetch_gig(str(gig_id))
    agent_id = _nz(gig.get("agent_id"))
    agent = _fetch_agent(agent_id)
    token = uuid.uuid4().hex

    if not agent:
        # No agent assigned or missing record -> skip with audit
        _insert_email_audit(
            token=token,
            gig_id=str(gig["id"]),
            recipient_email="",  # NOT NULL satisfied with empty string
            kind="agent_confirm",
            status="skipped-no-email",
            detail={"agent_id": agent_id, "errors": "no-agent-assigned"},
        )
        return

    to_email = _nz(agent.get("email"))
    if not to_email:
        _insert_email_audit(
            token=token,
            gig_id=str(gig["id"]),
            recipient_email="",
            kind="agent_confirm",
            status="skipped-no-email",
            detail={"agent_id": agent_id, "errors": "agent-has-no-email"},
        )
        return

    title = _nz(gig.get("title")) or "Gig"
    event_dt = _nz(gig.get("event_date"))
    start_hhmm = _fmt_time12(gig.get("start_time"))
    end_hhmm = _fmt_time12(gig.get("end_time"))

    venue = _fetch_venue(gig.get("venue_id"))
    venue_name = _nz(venue.get("name"))
    venue_addr = _fmt_addr(venue)
    venue_contact = _nz(venue.get("contact_name"))
    venue_phone = _nz(venue.get("contact_phone"))
    venue_email = _nz(venue.get("contact_email"))

    greet = _nz(agent.get("display_name")) or _nz(agent.get("first_name")) or "there"
    html = f"""
    <p>Hello {greet},</p>
    <p>This confirms booking details for <b>{title}</b>.</p>
    <table border="1" cellpadding="6" cellspacing="0">
      <tr><th align="left">Date</th><td>{event_dt}</td></tr>
      <tr><th align="left">Time</th><td>{start_hhmm} – {end_hhmm}</td></tr>
      <tr><th align="left">Venue</th><td>{venue_name}</td></tr>
      <tr><th align="left">Address</th><td>{venue_addr}</td></tr>
      <tr><th align="left">Venue Contact</th><td>{venue_contact}</td></tr>
      <tr><th align="left">Venue Phone</th><td>{venue_phone}</td></tr>
      <tr><th align="left">Venue Email</th><td>{venue_email}</td></tr>
    </table>
    <p>Please reply with any updates or questions.</p>
    """

    subject = f"Agent Confirmation: {title} ({event_dt})"
    try:
        if not _is_dry_run():
            result = gmail_send(subject, to_email, html, cc=(cc or [CC_RAY]), attachments=None)
            if not result:
                raise RuntimeError("gmail_send returned a non-success value (None/False)")
        _insert_email_audit(
            token=token,
            gig_id=str(gig["id"]),
            recipient_email=to_email,
            kind="agent_confirm",
            status=("dry-run" if _is_dry_run() else "sent"),
            detail={"to": to_email, "subject": subject, "agent_id": agent_id},
        )
    except Exception as e:
        _insert_email_audit(
            token=token,
            gig_id=str(gig["id"]),
            recipient_email=to_email,
            kind="agent_confirm",
            status=f"error: {e}",
            detail={"to": to_email, "subject": subject, "agent_id": agent_id, "errors": str(e)},
        )
        raise
