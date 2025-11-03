# send_agent_confirm.py
from __future__ import annotations
from datetime import datetime, date, time
from typing import Dict, Any, Optional
import os
import pandas as pd

# Reuse your Gmail sender
from lib.email_utils import gmail_send

# Supabase client (secrets OR env)
def _sb():
    import streamlit as st  # works fine even outside main app
    url = os.environ.get("SUPABASE_URL") or st.secrets.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_ANON_KEY") or st.secrets.get("SUPABASE_ANON_KEY")
    from supabase import create_client
    return create_client(url, key)

def _select_df(table: str, select: str="*", where_eq: Dict[str, Any] | None=None):
    sb = _sb()
    q = sb.table(table).select(select)
    if where_eq:
        for k,v in where_eq.items():
            q = q.eq(k, v)
    data = q.execute().data or []
    return pd.DataFrame(data)

def _fmt_time(hms: str | None) -> str:
    if not hms:
        return ""
    try:
        t = datetime.strptime(hms, "%H:%M:%S").time()
        return t.strftime("%I:%M %p").lstrip("0")
    except Exception:
        return hms

def _safe(v):  # stringify None safely
    return "" if v is None else str(v)

def send_agent_confirm(gig_id: str, cc: Optional[list[str]] = None) -> Dict[str, Any]:
    """
    Sends a confirmation to the agent on a gig.
    Audit row: kind='agent_confirm'
    Dry run if AGENT_EMAIL_DRY_RUN=1
    """
    sb = _sb()

    # ---- Load gig basics
    g = _select_df("gigs", "*", where_eq={"id": gig_id})
    if g.empty:
        raise RuntimeError(f"No gig found: {gig_id}")
    gig = g.iloc[0].to_dict()

    # Agent lookup
    agent_id = gig.get("agent_id")
    if not agent_id or pd.isna(agent_id):
        return {"sent": False, "reason": "no-agent"}

    a = _select_df("agents", "*", where_eq={"id": str(agent_id)})
    agent = a.iloc[0].to_dict() if not a.empty else {}
    to_email = (agent.get("email") or "").strip()
    if not to_email:
        return {"sent": False, "reason": "no-agent-email"}

    # Venue lookup (optional)
    v = _select_df("venues", "id,name,address,city,state,zip,phone,email", where_eq={"id": str(gig.get("venue_id"))})
    venue = v.iloc[0].to_dict() if not v.empty else {}

    event_dt = gig.get("event_date")
    start_hhmm = _fmt_time(gig.get("start_time"))
    end_hhmm = _fmt_time(gig.get("end_time"))

    title = gig.get("title") or "Gig"
    contract_status = gig.get("contract_status") or ""
    fee = gig.get("fee")
    fee_str = f"${float(fee):,.2f}" if isinstance(fee, (int, float)) else ""

    # Simple HTML (minimal, no attachments)
    html = f"""
    <p>Hello { _safe(agent.get('name')) or 'there' },</p>
    <p>This is a confirmation for <b>{_safe(title)}</b>.</p>
    <table border="1" cellpadding="6" cellspacing="0">
      <tr><th align="left">Date</th><td>{_safe(event_dt)}</td></tr>
      <tr><th align="left">Time</th><td>{start_hhmm} – {end_hhmm}</td></tr>
      <tr><th align="left">Contract Status</th><td>{_safe(contract_status)}</td></tr>
      <tr><th align="left">Fee</th><td>{fee_str}</td></tr>
      <tr><th align="left">Venue</th><td>{_safe(venue.get('name'))}</td></tr>
      <tr><th align="left">Address</th><td>{_safe(venue.get('address'))} {_safe(venue.get('city'))} {_safe(venue.get('state'))} {_safe(venue.get('zip'))}</td></tr>
      <tr><th align="left">Venue Contact</th><td>{_safe(venue.get('phone'))} {_safe(venue.get('email'))}</td></tr>
    </table>
    <p>If anything looks off, please reply.</p>
    """

    subject = f"Agent Confirmation: {title} ({_safe(event_dt)})"
    dry = os.environ.get("AGENT_EMAIL_DRY_RUN") == "1"

    # Gmail send (or dry-run)
    if not dry:
        gmail_send(subject, to_email, html, cc=cc or None, attachments=None)

    # Audit
    sb.table("email_audit").insert({
        "kind": "agent_confirm",
        "status": "dry-run" if dry else "sent",
        "gig_id": gig_id,
        "detail": {"to": to_email, "subject": subject}
    }).execute()

    return {"sent": not dry, "to": to_email}
