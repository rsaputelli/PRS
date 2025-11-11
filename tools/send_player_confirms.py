# tools/send_player_confirms.py

from __future__ import annotations
import os
import uuid
import datetime as dt
from typing import Dict, Any, Iterable, Optional, List

from supabase import create_client, Client
from lib.email_utils import gmail_send
from lib.calendar_utils import make_ics_bytes  # primary path; we’ll fallback if it raises

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

def _is_dry_run() -> bool:
    val = _get_secret("PLAYER_EMAIL_DRY_RUN", "0")
    return str(val).lower() in {"1", "true", "yes", "on"}

SUPABASE_URL = _get_secret("SUPABASE_URL")
SUPABASE_KEY = (
    _get_secret("SUPABASE_SERVICE_ROLE")
    or _get_secret("SUPABASE_KEY")
    or _get_secret("SUPABASE_ANON_KEY")
)
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
            sb.auth.set_session(access_token=at, refresh_token=rt)
    except Exception:
        pass
    return sb

def _sb_admin() -> Client:
    sr = _get_secret("SUPABASE_SERVICE_ROLE") or SUPABASE_KEY
    return create_client(SUPABASE_URL, sr)

# -----------------------------
# Fetch helpers (schema-accurate)
# -----------------------------
def _fetch_gig(gig_id: str) -> Dict[str, Any]:
    res = (
        _sb().table("gigs")
        .select("id, title, event_date, start_time, end_time, venue_id, sound_tech_id")
        .eq("id", gig_id)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    if not rows:
        raise ValueError(f"Gig {gig_id} not found.")
    return rows[0]

def _fetch_venue(venue_id: Optional[str]) -> Dict[str, Any]:
    if not venue_id:
        return {}
    res = (
        _sb().table("venues")
        .select("name, address_line1, address_line2, city, state, postal_code")
        .eq("id", venue_id)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else {}

def _gig_musicians_rows(gig_id: str) -> List[Dict[str, Any]]:
    res = (
        _sb().table("gig_musicians")
        .select("musician_id, role")
        .eq("gig_id", gig_id)
        .execute()
    )
    return res.data or []

def _fetch_musicians_map(ids: List[str]) -> Dict[str, Dict[str, Any]]:
    if not ids:
        return {}
    res = (
        _sb().table("musicians")
        .select("id, email, stage_name, display_name, first_name, last_name")
        .in_("id", ids)
        .execute()
    )
    rows = res.data or []
    return {str(r["id"]): r for r in rows if r.get("id") is not None}

def _fetch_soundtech_name(sound_tech_id: Optional[str]) -> str:
    """Fallback only if we didn’t find a 'sound' role in lineup."""
    if not sound_tech_id:
        return ""
    try:
        res = (
            _sb().table("sound_techs")
            .select("id, stage_name, display_name, first_name, last_name, name")
            .eq("id", sound_tech_id)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            return ""
        r = rows[0]
        for k in ("stage_name", "display_name", "name"):
            v = (r.get(k) or "").strip()
            if v:
                return v
        fn = (r.get("first_name") or "").strip()
        ln = (r.get("last_name") or "").strip()
        combo = (fn + " " + ln).strip()
        return combo
    except Exception:
        return ""

# -----------------------------
# Formatting helpers
# -----------------------------
def _nz(v) -> str:
    return "" if v is None else str(v).strip()

def _stage_pref(mrow: Dict[str, Any]) -> str:
    v = (mrow.get("stage_name") or "").strip()
    if v:
        return v
    v = (mrow.get("display_name") or "").strip()
    if v:
        return v
    fn = (mrow.get("first_name") or "").strip()
    ln = (mrow.get("last_name") or "").strip()
    combo = (fn + " " + ln).strip()
    return combo or "Unknown"

def _greet_name(mrow: Dict[str, Any]) -> str:
    for key in ("display_name", "stage_name"):
        v = (mrow.get(key) or "").strip()
        if v:
            return v
    fn = (mrow.get("first_name") or "").strip()
    ln = (mrow.get("last_name") or "").strip()
    combo = (fn + " " + ln).strip()
    return combo or "there"

def _fmt_time12(t: Optional[str]) -> str:
    if not t:
        return ""
    s = str(t).strip()
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return dt.datetime.strptime(s, fmt).strftime("%I:%M %p").lstrip("0")
        except ValueError:
            continue
    return s

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
# Audit helper
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
# Date/time for ICS (robust; DATE/TIME from your schema)
# -----------------------------
def _mk_dt(event_date: Any, time_value: Any, tzname: str = "America/New_York") -> Optional[dt.datetime]:
    if not event_date:
        return None

    # Parse date
    try:
        if isinstance(event_date, dt.date) and not isinstance(event_date, dt.datetime):
            y, m, d = event_date.year, event_date.month, event_date.day
        else:
            ds = str(event_date).strip()
            if "/" in ds:  # MM/DD/YYYY
                m, d, y = [int(x) for x in ds.split("/")]
            else:          # YYYY-MM-DD
                y, m, d = [int(x) for x in ds.split("-")]
    except Exception:
        return None

    # Parse time
    hh, mm = 0, 0
    try:
        if isinstance(time_value, dt.time):
            hh, mm = time_value.hour, time_value.minute
        else:
            ts = _nz(time_value).upper()
            ampm = None
            if ts.endswith("AM") or ts.endswith("PM"):
                ampm = "PM" if ts.endswith("PM") else "AM"
                ts = ts.replace("AM", "").replace("PM", "").strip()
            parts = [p for p in ts.split(":") if p]
            if len(parts) >= 2:
                hh = int(parts[0]); mm = int(parts[1])
            if ampm == "PM" and hh < 12:
                hh += 12
            if ampm == "AM" and hh == 12:
                hh = 0
    except Exception:
        hh, mm = 0, 0

    try:
        from zoneinfo import ZoneInfo
        return dt.datetime(int(y), int(m), int(d), int(hh), int(mm), tzinfo=ZoneInfo(tzname))
    except Exception:
        return None

def _utc_naive(dt_aware: dt.datetime) -> dt.datetime:
    """Convert aware -> UTC -> naive (for helpers that expect naive UTC)."""
    return dt_aware.astimezone(dt.timezone.utc).replace(tzinfo=None)

def _fallback_ics_bytes(uid: str, starts_at: dt.datetime, ends_at: dt.datetime,
                        summary: str, location: str, description: str) -> bytes:
    """Simple ICS (UTC naive inputs)."""
    def _fmt(d: dt.datetime) -> str:
        return d.strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//PRS//Player Confirm//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:REQUEST",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{_fmt(dt.datetime.utcnow())}",
        f"DTSTART:{_fmt(starts_at)}",
        f"DTEND:{_fmt(ends_at)}",
        f"SUMMARY:{summary}",
        f"LOCATION:{location}",
        "DESCRIPTION:" + description.replace("\n", "\\n"),
        "END:VEVENT",
        "END:VCALENDAR",
        ""
    ]
    return ("\r\n".join(lines)).encode("utf-8")

# -----------------------------
# Public API
# -----------------------------
def send_player_confirms(gig_id: str, musician_ids: Optional[Iterable[str]] = None, cc: Optional[list[str]] = None) -> None:
    """Send confirmations to selected musicians (or full lineup if None)."""
    gig_id = str(gig_id)
    gig = _fetch_gig(gig_id)

    title = _nz(gig.get("title")) or "Gig"
    event_dt = gig.get("event_date")
    start_time = gig.get("start_time")
    end_time = gig.get("end_time")

    venue = _fetch_venue(gig.get("venue_id"))
    venue_name = _nz(venue.get("name"))
    venue_addr = _fmt_addr(venue)

    # Build full lineup order and roles
    gm_rows = _gig_musicians_rows(gig_id)
    ordered_ids: List[str] = []
    roles_by_mid: Dict[str, str] = {}
    for r in gm_rows:
        mid = r.get("musician_id")
        if mid is None:
            continue
        smid = str(mid)
        if smid not in roles_by_mid:
            ordered_ids.append(smid)
        roles_by_mid[smid] = _nz(r.get("role"))

    # Determine send targets (subset
