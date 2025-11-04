# --tools/send_player_confirms.py

from __future__ import annotations
from typing import Dict, Any, Iterable, Optional, List
import os, time, pandas as pd
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

def _select_df(table: str, select: str="*", where_eq: Dict[str, Any] | None=None) -> pd.DataFrame:
    q = _sb().table(table).select(select)
    if where_eq:
        for k, v in where_eq.items():
            q = q.eq(k, v)
    data = q.execute().data or []
    return pd.DataFrame(data)

def _fetch_venue_any(venue_id: str | None) -> Dict[str, Any]:
    if not venue_id:
        return {}
    v = _sb().table("venues").select("*").eq("id", venue_id).limit(1).execute().data or []
    return v[0] if v else {}

# ----------------------------
# Formatting helpers
# ----------------------------
def _fmt_time(hms: str | None) -> str:
    from datetime import datetime
    if not hms:
        return ""
    try:
        t = datetime.strptime(hms, "%H:%M:%S").time()
        return t.strftime("%I:%M %p").lstrip("0")
    except Exception:
        return hms or ""

def _safe(v):
    return "" if v is None else str(v).strip()

def _fmt_address(v: Dict[str, Any]) -> str:
    line1 = _safe(v.get("address_line1"))
    line2 = _safe(v.get("address_line2"))
    city  = _safe(v.get("city"))
    state = _safe(v.get("state"))
    zipc  = _safe(v.get("postal_code"))
    parts = []
    if line1: parts.append(line1)
    if line2: parts.append(line2)
    tail = " ".join([p for p in [city, state, zipc] if p]).strip()
    if tail: parts.append(tail)
    return " | ".join(parts)

def _resolve_changed_musicians(gig_id: str) -> List[str]:
    """For Enter: send to all lineup rows for the gig."""
    gm = _select_df("gig_musicians", "*", where_eq={"gig_id": gig_id})
    if gm.empty:
        return []
    return [str(x) for x in gm["musician_id"].dropna().astype(str).unique()]

# ----------------------------
# Public API
# ----------------------------
def send_player_confirms(gig_id: str, musician_ids: Optional[Iterable[str]] = None, cc: Optional[list[str]] = None) -> Dict[str, Any]:
    """
    Emails confirmations to the specified musicians for this gig.
    If musician_ids is None, it emails ALL current lineup (useful on create).
    Audit rows: kind='player_confirm'
    """
    gig = _fetch_gig_with_retry(str(gig_id))
    venue = _fetch_venue_any(gig.get("venue_id"))
    venue_name = _safe(venue.get("name"))
    venue_addr = _fmt_address(venue)

    # Build list of musician ids
    if musician_ids is None:
        ids = _resolve_changed_musicians(str(gig_id))
    else:
        ids = [str(x) for x in musician_ids if x]

    if not ids:
        return {"sent": 0, "reason": "no-musicians"}

    # Resolve musician records
    md = _select_df("musicians", "*")
    if md.empty or "id" not in md.columns:
        raise RuntimeError("Musicians table unreadable (RLS or schema).")
    md["id"] = md["id"].astype(str)
    mindex = md.set_index("id").to_dict(orient="index")

    event_dt = _safe(gig.get("event_date"))
    start_hhmm = _fmt_time(gig.get("start_time"))
    end_hhmm = _fmt_time(gig.get("end_time"))
    title = _safe(gig.get("title")) or "Gig"

    # Load roles map for this gig for role-specific lines
    gm = _select_df("gig_musicians", "role,musician_id", where_eq={"gig_id": str(gig_id)})
    role_map = {}
    if not gm.empty:
        for _, r in gm.iterrows():
            role_map[str(r.get("musician_id"))] = (_safe(r.get("role")))

    sent = 0
    for mid in ids:
        mrow = mindex.get(mid) or {}
        to_email = _safe(mrow.get("email"))
        if not to_email:
            _sb_admin().table("email_audit").insert({
                "kind": "player_confirm",
                "status": "skipped-no-email",
                "gig_id": gig_id,
                "detail": {"musician_id": mid}
            }).execute()
            continue

        role = role_map.get(mid, "")
        html = f"""
        <p>Hello { _safe(mrow.get('name')) or 'there' },</p>
        <p>You’re confirmed for <b>{title}</b> ({role}).</p>
        <table border="1" cellpadding="6" cellspacing="0">
          <tr><th align="left">Date</th><td>{event_dt}</td></tr>
          <tr><th align="left">Time</th><td>{start_hhmm} – {end_hhmm}</td></tr>
          <tr><th align="left">Venue</th><td>{venue_name}</td></tr>
          <tr><th align="left">Address</th><td>{venue_addr}</td></tr>
        </table>
        <p>Please reply if anything needs attention.</p>
        """

        subject = f"Player Confirmation: {title} ({event_dt})"
        gmail_send(subject, to_email, html, cc=cc or None, attachments=None)

        _sb_admin().table("email_audit").insert({
            "kind": "player_confirm",
            "status": "sent",
            "gig_id": gig_id,
            "detail": {"to": to_email, "subject": subject, "musician_id": mid}
        }).execute()
        sent += 1

    return {"sent": sent}
