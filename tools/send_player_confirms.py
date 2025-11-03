# send_player_confirms.py
from __future__ import annotations
from typing import Dict, Any, Iterable, Optional, List
import os
import pandas as pd
from lib.email_utils import gmail_send

def _sb():
    import streamlit as st
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
    from datetime import datetime
    if not hms:
        return ""
    try:
        t = datetime.strptime(hms, "%H:%M:%S").time()
        return t.strftime("%I:%M %p").lstrip("0")
    except Exception:
        return hms

def _safe(v):
    return "" if v is None else str(v)

def _resolve_changed_musicians(gig_id: str) -> List[str]:
    """
    If you call this after a save on Edit, you can optionally implement a BEFORE snapshot
    and compare here. For now, return 'all current lineup' so Enter can use it and Edit
    can pass an explicit list when you wire the diff.
    """
    gm = _select_df("gig_musicians", "*", where_eq={"gig_id": gig_id})
    if gm.empty:
        return []
    return [str(x) for x in gm["musician_id"].dropna().astype(str).unique()]

def send_player_confirms(gig_id: str, musician_ids: Optional[Iterable[str]] = None, cc: Optional[list[str]] = None) -> Dict[str, Any]:
    """
    Emails confirmations to the specified musicians for this gig.
    If musician_ids is None, it emails ALL current lineup (useful on create).
    Audit rows: kind='player_confirm'
    Dry run if PLAYER_EMAIL_DRY_RUN=1
    """
    sb = _sb()
    gig = _select_df("gigs", "*", where_eq={"id": gig_id})
    if gig.empty:
        raise RuntimeError(f"No gig found: {gig_id}")
    g = gig.iloc[0].to_dict()

    # Venue (optional)
    v = _select_df("venues", "id,name,address,city,state,zip,phone,email", where_eq={"id": str(g.get("venue_id"))})
    venue = v.iloc[0].to_dict() if not v.empty else {}

    # Build list of musician ids
    if musician_ids is None:
        ids = _resolve_changed_musicians(gig_id)
    else:
        ids = [str(x) for x in musician_ids if x]

    if not ids:
        return {"sent": 0, "reason": "no-musicians"}

    # Resolve musician records
    md = _select_df("musicians", "*")
    md["id"] = md["id"].astype(str)
    mindex = md.set_index("id").to_dict(orient="index")

    event_dt = g.get("event_date")
    start_hhmm = _fmt_time(g.get("start_time"))
    end_hhmm = _fmt_time(g.get("end_time"))
    title = g.get("title") or "Gig"

    dry = os.environ.get("PLAYER_EMAIL_DRY_RUN") == "1"
    sent = 0

    # Load roles map for this gig for role-specific lines
    gm = _select_df("gig_musicians", "role,musician_id", where_eq={"gig_id": gig_id})
    role_map = {}
    if not gm.empty:
        for _, r in gm.iterrows():
            role_map[str(r.get("musician_id"))] = (r.get("role") or "")

    for mid in ids:
        mrow = mindex.get(mid) or {}
        to_email = (mrow.get("email") or "").strip()
        if not to_email:
            # still audit no-email case
            sb.table("email_audit").insert({
                "kind": "player_confirm",
                "status": "skipped-no-email",
                "gig_id": gig_id,
                "detail": {"musician_id": mid}
            }).execute()
            continue

        role = role_map.get(mid, "")
        html = f"""
        <p>Hello { _safe(mrow.get('name')) or 'there' },</p>
        <p>You’re confirmed for <b>{_safe(title)}</b> ({_safe(role)}).</p>
        <table border="1" cellpadding="6" cellspacing="0">
          <tr><th align="left">Date</th><td>{_safe(event_dt)}</td></tr>
          <tr><th align="left">Time</th><td>{start_hhmm} – {end_hhmm}</td></tr>
          <tr><th align="left">Venue</th><td>{_safe(venue.get('name'))}</td></tr>
          <tr><th align="left">Address</th><td>{_safe(venue.get('address'))} {_safe(venue.get('city'))} {_safe(venue.get('state'))} {_safe(venue.get('zip'))}</td></tr>
        </table>
        <p>Please reply if anything needs attention.</p>
        """

        subject = f"Player Confirmation: {title} ({_safe(event_dt)})"
        if not dry:
            gmail_send(subject, to_email, html, cc=cc or None, attachments=None)

        sb.table("email_audit").insert({
            "kind": "player_confirm",
            "status": "dry-run" if dry else "sent",
            "gig_id": gig_id,
            "detail": {"to": to_email, "subject": subject, "musician_id": mid}
        }).execute()
        sent += 1

    return {"sent": sent}
