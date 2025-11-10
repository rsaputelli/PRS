# --tools/send_player_confirms.py

from __future__ import annotations
import os
import uuid
import datetime as dt
from typing import Dict, Any, Iterable, Optional, List

from supabase import create_client, Client
from lib.email_utils import gmail_send
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
# Fetch helpers
# -----------------------------
def _fetch_gig(gig_id: str) -> Dict[str, Any]:
    res = (
        _sb().table("gigs")
        .select("id, title, event_date, start_time, end_time, venue_id")
        .eq("id", gig_id)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    if not rows:
        raise ValueError(f"Gig {gig_id} not found.")
    return rows[0]

def _fetch_venue(venue_id: str | None) -> Dict[str, Any]:
    if not venue_id:
        return {}
    res = (
        _sb().table("venues")
        .select("name, address_line1, address_line2, city, state, postal_code")
        .eq("id", venue_id).limit(1).execute()
    )
    rows = res.data or []
    return rows[0] if rows else {}

def _gig_musicians(gig_id: str) -> List[Dict[str, Any]]:
    # We need both musician_id and role
    res = _sb().table("gig_musicians").select(
        "musician_id, role"
    ).eq("gig_id", gig_id).execute()
    return res.data or []

def _fetch_musicians_map(ids: List[str]) -> Dict[str, Dict[str, Any]]:
    if not ids:
        return {}
    res = (
        _sb().table("musicians")
        .select("id, email, display_name, first_name, last_name, stage_name")
        .in_("id", ids)
        .execute()
    )
    rows = res.data or []
    return {str(r["id"]): r for r in rows if r.get("id") is not None}

# -----------------------------
# Formatting helpers
# -----------------------------
def _nz(v) -> str:
    return "" if v is None else str(v).strip()

def _mus_display_name(r: Dict[str, Any]) -> str:
    # Gentle greeting preference
    for key in ("display_name", "stage_name"):
        v = (r.get(key) or "").strip()
        if v:
            return v
    fn = (r.get("first_name") or "").strip()
    ln = (r.get("last_name") or "").strip()
    combo = (fn + " " + ln).strip()
    return combo or "there"

def _stage_name_pref(r: Dict[str, Any]) -> str:
    v = (r.get("stage_name") or "").strip()
    if v:
        return v
    v = (r.get("display_name") or "").strip()
    if v:
        return v
    fn = (r.get("first_name") or "").strip()
    ln = (r.get("last_name") or "").strip()
    combo = (fn + " " + ln).strip()
    return combo or "Unknown"

def _fmt_time12(t: Optional[str]) -> str:
    from datetime import datetime
    if not t:
        return ""
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(t, fmt).strftime("%I:%M %p").lstrip("0")
        except ValueError:
            continue
    # Try already-am/pm-ish strings as a last resort
    return str(t)

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
# Date/time builder for ICS
# -----------------------------
def _mk_dt(date_str: str | None, time_str: str | None, tzname: str = "America/New_York"):
    """
    Accepts:
      - date: YYYY-MM-DD or MM/DD/YYYY
      - time: 'HH:MM[:SS]' (24h) or 'h:mm AM/PM'
    Returns timezone-aware datetime or None.
    """
    if not date_str:
        return None
    ds = str(date_str).strip()
    try:
        if "/" in ds:  # MM/DD/YYYY
            m, d, y = [int(x) for x in ds.split("/")]
        else:          # YYYY-MM-DD
            y, m, d = [int(x) for x in ds.split("-")]
    except Exception:
        return None

    hh = 0; mm = 0
    ts = (str(time_str) if time_str else "").strip()
    if ts:
        t_upper = ts.upper()
        ampm = None
        if t_upper.endswith("AM") or t_upper.endswith("PM"):
            ampm = "PM" if t_upper.endswith("PM") else "AM"
            ts = t_upper.replace("AM", "").replace("PM", "").strip()
        parts = [p for p in ts.split(":") if p != ""]
        if len(parts) >= 2:
            try:
                hh = int(parts[0]); mm = int(parts[1])
            except Exception:
                hh, mm = 0, 0
        if ampm == "PM" and hh < 12:
            hh += 12
        if ampm == "AM" and hh == 12:
            hh = 0

    try:
        from zoneinfo import ZoneInfo
        return dt.datetime(y, m, d, hh, mm, tzinfo=ZoneInfo(tzname))
    except Exception:
        return None

# -----------------------------
# Public API
# -----------------------------
def send_player_confirms(gig_id: str, musician_ids: Optional[Iterable[str]] = None, cc: Optional[list[str]] = None) -> None:
    """
    Minimal, surgical edits:
      - Stage Name + Role lineup
      - Sound tech detection
      - ICS generation with tolerant parsing
      - Added audit fields
    """
    gig_id = str(gig_id)
    gig = _fetch_gig(gig_id)

    title = _nz(gig.get("title")) or "Gig"
    event_dt = _nz(gig.get("event_date"))
    start_hhmm = _fmt_time12(gig.get("start_time"))
    end_hhmm = _fmt_time12(gig.get("end_time"))

    venue = _fetch_venue(gig.get("venue_id"))
    venue_name = _nz(venue.get("name"))
    venue_addr = _fmt_addr(venue)

    # Build the target list (preserve order, unique)
    gm_rows = _gig_musicians(gig_id)
    ordered_ids = []
    roles_by_mid = {}
    for r in gm_rows:
        mid = r.get("musician_id")
        if mid is None:
            continue
        smid = str(mid)
        if smid not in roles_by_mid:
            ordered_ids.append(smid)
        roles_by_mid[smid] = (r.get("role") or "").strip()

    if musician_ids is None:
        target_ids = ordered_ids[:]
    else:
        # Only send to requested recipients, but we still need full lineup context
        target_ids = [str(x) for x in musician_ids if x]

    # Fetch musician rows for *all* ordered_ids (for lineup), but we’ll send to subset if requested
    mus_map = _fetch_musicians_map(ordered_ids)

    for mid in target_ids:
        token = uuid.uuid4().hex
        mrow = mus_map.get(mid) or {}
        to_email = _nz(mrow.get("email"))

        if not to_email:
            _insert_email_audit(
                token=token, gig_id=gig_id, recipient_email="",
                kind="player_confirm", status="skipped-no-email",
                detail={"musician_id": mid, "errors": "musician-has-no-email"},
            )
            continue

        role_me = roles_by_mid.get(mid, "")
        greet = _mus_display_name(mrow)

        # ---------- Build Other Players (Stage Name + Role), excluding recipient ----------
        other_players_list: List[str] = []
        for oid in ordered_ids:
            if oid == mid:
                continue
            orow = mus_map.get(oid) or {}
            name = _stage_name_pref(orow)
            r = roles_by_mid.get(oid, "")
            label = name if not r else f"{name} ({r})"
            other_players_list.append(label)
        other_players_count = len(other_players_list)

        # ---------- Find Sound Tech (role contains "sound") ----------
        soundtech_name = ""
        try:
            for oid in ordered_ids:
                r = (roles_by_mid.get(oid, "") or "")
                if "sound" in r.lower():
                    soundtech_name = _stage_name_pref(mus_map.get(oid) or {})
                    break
        except Exception:
            soundtech_name = ""

        # ---------- Email body blocks ----------
        lineup_html = "<h4>Lineup</h4><ul>"
        if other_players_list:
            lineup_html += f"<li><b>Other confirmed players:</b> {', '.join(other_players_list)}</li>"
        if soundtech_name:
            lineup_html += f"<li><b>Confirmed sound tech:</b> {soundtech_name}</li>"
        lineup_html += "</ul>"

        details_html = f"""
        <h4>Event Details</h4>
        <table border="1" cellpadding="6" cellspacing="0">
          <tr><th align="left">Date</th><td>{event_dt}</td></tr>
          <tr><th align="left">Time</th><td>{start_hhmm} – {end_hhmm}</td></tr>
          <tr><th align="left">Venue</th><td>{venue_name}</td></tr>
          <tr><th align="left">Address</th><td>{venue_addr}</td></tr>
        </table>
        """

        html = f"""
        <p>Hello {greet},</p>
        <p>You’re confirmed for <b>{title}</b>{f" ({role_me})" if role_me else ""}.</p>
        {lineup_html}
        {details_html}
        <p>Please reply if anything needs attention.</p>
        """

        # ---------- ICS attachment ----------
        has_ics = False
        starts_at_built = None
        ends_at_built = None
        attachments = None

        try:
            starts_at = _mk_dt(event_dt, gig.get("start_time"))
            ends_at   = _mk_dt(event_dt, gig.get("end_time"))

            if starts_at:
                starts_at_built = starts_at.isoformat()
            if ends_at:
                ends_at_built = ends_at.isoformat()

            if starts_at and ends_at:
                # DESCRIPTION content per requirements
                extra_lines = []
                if other_players_list:
                    extra_lines.append("Other confirmed players: " + ", ".join(other_players_list))
                if soundtech_name:
                    extra_lines.append(f"Confirmed sound tech: {soundtech_name}")
                if venue_name:
                    extra_lines.append(f"Venue: {venue_name}")
                if event_dt:
                    extra_lines.append(f"Event date: {event_dt}")
                if _nz(gig.get("start_time")) or _nz(gig.get("end_time")):
                    extra_lines.append(f"Start and end times: {_nz(gig.get('start_time'))} – {_nz(gig.get('end_time'))}")

                description = ("You’re confirmed for " + title).strip()
                if extra_lines:
                    description += "\n\n" + "\n".join(extra_lines)

                location = " | ".join([p for p in [venue_name, venue_addr] if p]).strip()
                ics_bytes = make_ics_bytes(
                    starts_at=starts_at,
                    ends_at=ends_at,
                    summary=title,
                    location=location,
                    description=description,
                )
                attachments = [{
                    "filename": f"{title}-{event_dt}.ics",
                    "mime": "text/calendar; method=REQUEST; charset=UTF-8",
                    "content": ics_bytes,
                }]
                has_ics = True
        except Exception:
            has_ics = False
            attachments = None

        # ---------- Send ----------
        subject = f"Player Confirmation: {title} ({event_dt})"
        try:
            if not _is_dry_run():
                result = gmail_send(subject, to_email, html, cc=(cc or [CC_RAY]), attachments=attachments)
                if not result:
                    raise RuntimeError("gmail_send returned a non-success value")
            _insert_email_audit(
                token=token, gig_id=gig_id, recipient_email=to_email,
                kind="player_confirm", status=("dry-run" if _is_dry_run() else "sent"),
                detail={
                    "to": to_email,
                    "subject": subject,
                    "musician_id": mid,
                    "has_ics": has_ics,
                    "other_players_count": other_players_count,
                    "starts_at_built": starts_at_built,
                    "ends_at_built": ends_at_built,
                },
            )
        except Exception as e:
            _insert_email_audit(
                token=token, gig_id=gig_id, recipient_email=to_email,
                kind="player_confirm", status=f"error: {e}",
                detail={
                    "to": to_email,
                    "subject": subject,
                    "musician_id": mid,
                    "errors": str(e),
                    "has_ics": has_ics,
                    "other_players_count": other_players_count,
                    "starts_at_built": starts_at_built,
                    "ends_at_built": ends_at_built,
                },
            )
            raise
