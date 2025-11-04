from __future__ import annotations
from typing import Dict, Any, Optional
import os, time
from supabase import create_client, Client
from lib.email_utils import gmail_send

# ----------------------------
# Supabase helpers (mirror sound-tech)
# ----------------------------
def _get_secret(name: str, default: Optional[str] = None) -> Optional[str]:
    try:
        import streamlit as st
        if hasattr(st, "secrets") and name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.environ.get(name, default)

SUPABASE_URL = _get_secret("SUPABASE_URL")
# Prefer service role for admin client; fall back to anon for user client
SUPABASE_ANON = _get_secret("SUPABASE_ANON_KEY") or _get_secret("SUPABASE_KEY")
SUPABASE_SERVICE_ROLE = _get_secret("SUPABASE_SERVICE_ROLE") or SUPABASE_ANON

def _sb() -> Client:
    """User client with attached session so RLS allows reads."""
    sb = create_client(SUPABASE_URL, SUPABASE_ANON)
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
    """Privileged client for audit inserts (bypass RLS on audit table)."""
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE)

# ----------------------------
# Fetch helpers
# ----------------------------
def _fetch_gig_with_retry(gig_id: str, tries: int = 8, delay: float = 0.2) -> Dict[str, Any]:
    last_err = None
    for _ in range(tries):
        try:
            row = (_sb().table("gigs").select("*").eq("id", gig_id).limit(1).execute().data or [None])[0]
            if row:
                return row
        except Exception as e:
            last_err = e
        time.sleep(delay)
    if last_err:
        raise RuntimeError(f"Failed to read gig {gig_id}: {last_err}")
    raise RuntimeError(f"No gig found: {gig_id}")

def _fetch_agent(agent_id: str) -> Optional[Dict[str, Any]]:
    if not agent_id:
        return None
    res = _sb().table("agents").select("*").eq("id", agent_id).limit(1).execute().data or []
    return res[0] if res else None

# ----------------------------
# Public API
# ----------------------------
def send_agent_confirm(gig_id: str, cc: Optional[list[str]] = None) -> Dict[str, Any]:
    """
    Send a single agent confirmation for the given gig.
    Creates an email_audit row with kind='agent_confirm'.
    """
    gig = _fetch_gig_with_retry(str(gig_id))
    agent_id = str(gig.get("agent_id") or "") if gig.get("agent_id") is not None else ""

    agent = _fetch_agent(agent_id)
    if not agent:
        raise RuntimeError("Gig has no agent assigned or agent record not found.")

    to_email = (agent.get("email") or "").strip()
    if not to_email:
        # audit skipped: no email
        _sb_admin().table("email_audit").insert({
            "kind": "agent_confirm",
            "status": "skipped-no-email",
            "gig_id": gig_id,
            "detail": {"agent_id": agent_id}
        }).execute()
        return {"sent": 0, "reason": "no-agent-email"}

    title = gig.get("title") or "Gig"
    event_dt = gig.get("event_date") or ""
    start_hhmm = _fmt_time(gig.get("start_time"))
    end_hhmm   = _fmt_time(gig.get("end_time"))

    # Venue (optional)
    venue = None
    if gig.get("venue_id"):
        v = _sb().table("venues").select("name,address,city,state,zip,phone,email").eq("id", gig["venue_id"]).limit(1).execute().data or []
        venue = v[0] if v else None

    html = f"""
    <p>Hello { (agent.get('display_name') or agent.get('first_name') or 'there') },</p>
    <p>This confirms booking details for <b>{title}</b>.</p>
    <table border="1" cellpadding="6" cellspacing="0">
      <tr><th align="left">Date</th><td>{event_dt}</td></tr>
      <tr><th align="left">Time</th><td>{start_hhmm} – {end_hhmm}</td></tr>
      <tr><th align="left">Venue</th><td>{(venue or {}).get('name','')}</td></tr>
      <tr><th align="left">Address</th><td>{(venue or {}).get('address','')} {(venue or {}).get('city','')} {(venue or {}).get('state','')} {(venue or {}).get('zip','')}</td></tr>
    </table>
    <p>Please reply with any updates or questions.</p>
    """

    subject = f"Agent Confirmation: {title} ({event_dt})"
    gmail_send(subject, to_email, html, cc=cc or None, attachments=None)

    _sb_admin().table("email_audit").insert({
        "kind": "agent_confirm",
        "status": "sent",
        "gig_id": gig_id,
        "detail": {"to": to_email, "subject": subject, "agent_id": agent_id}
    }).execute()

    return {"sent": 1}

# ----------------------------
# Utils
# ----------------------------
def _fmt_time(hms: str | None) -> str:
    from datetime import datetime
    if not hms:
        return ""
    try:
        t = datetime.strptime(hms, "%H:%M:%S").time()
        return t.strftime("%I:%M %p").lstrip("0")
    except Exception:
        return hms
