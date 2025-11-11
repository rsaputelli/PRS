# tools/send_player_confirms.py

from __future__ import annotations
import os
import uuid
import datetime as dt
from typing import Dict, Any, Iterable, Optional, List

from supabase import create_client, Client
from lib.email_utils import gmail_send
from lib.calendar_utils import make_ics_bytes  # primary; we fallback if needed

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
# Fetch helpers (schema-accurate; DO NOT select gigs.email)
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
    """Fallback only if no lineup role contains 'sound'."""
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
# Date/time for ICS (robust for DATE/TIME types)
# -----------------------------
def _mk_dt(event_date: Any, time_value: Any, tzname: str = "America/New_York") -> Optional[dt.datetime]:
    if not event_date:
        return None

    # Date
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

    # Time
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
    return dt_aware.astimezone(dt.timezone.utc).replace(tzinfo=None)

def _fallback_ics_bytes(uid: str, starts_at: dt.datetime, ends_at: dt.datetime,
                        summary: str, location: str, description: str) -> bytes:
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

    # Full lineup order + roles
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

    # Recipients
    target_ids = ordered_ids[:] if musician_ids is None else [str(x) for x in musician_ids if x]

    # Data for full lineup (to render "other players")
    mus_map = _fetch_musicians_map(ordered_ids)

    # Human times for body
    start_hhmm = _fmt_time12(start_time)
    end_hhmm = _fmt_time12(end_time)

    # Sound tech via role; fallback to gigs.sound_tech_id
    soundtech_name = ""
    for oid in ordered_ids:
        r = roles_by_mid.get(oid, "")
        if "sound" in r.lower():
            soundtech_name = _stage_pref(mus_map.get(oid) or {})
            break
    if not soundtech_name:
        soundtech_name = _fetch_soundtech_name(_nz(gig.get("sound_tech_id")))

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
        greet = _greet_name(mrow)

        # Other confirmed players (Stage Name + Role), excluding recipient
        other_players_list: List[str] = []
        for oid in ordered_ids:
            if oid == mid:
                continue
            orow = mus_map.get(oid) or {}
            name = _stage_pref(orow)
            r = roles_by_mid.get(oid, "")
            other_players_list.append(f"{name}{f' ({r})' if r else ''}")
        other_players_count = len(other_players_list)

        # Email body
        lineup_html = "<h4>Lineup</h4><ul>"
        if other_players_list:
            lineup_html += f"<li><b>Other confirmed players:</b> {', '.join(other_players_list)}</li>"
        if soundtech_name:
            lineup_html += f"<li><b>Confirmed sound tech:</b> {soundtech_name}</li>"
        lineup_html += "</ul>"

        details_html = f"""
        <h4>Event Details</h4>
        <table border="1" cellpadding="6" cellspacing="0">
          <tr><th align="left">Date</th><td>{_nz(event_dt)}</td></tr>
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

        # ICS attachment
        has_ics = False
        starts_at_built = None
        ends_at_built = None
        attachments = None

        try:
            starts_at_aware = _mk_dt(event_dt, start_time)
            ends_at_aware = _mk_dt(event_dt, end_time)
            if starts_at_aware and not ends_at_aware:
                ends_at_aware = starts_at_aware + dt.timedelta(hours=3)

            if starts_at_aware and ends_at_aware:
                starts_at_built = starts_at_aware.isoformat()
                ends_at_built = ends_at_aware.isoformat()

                desc_lines = []
                if other_players_list:
                    desc_lines.append("Other confirmed players: " + ", ".join(other_players_list))
                if soundtech_name:
                    desc_lines.append(f"Confirmed sound tech: {soundtech_name}")
                if venue_name:
                    desc_lines.append(f"Venue: {venue_name}")
                if event_dt:
                    desc_lines.append(f"Event date: {_nz(event_dt)}")
                if _nz(start_time) or _nz(end_time):
                    desc_lines.append(f"Start and end times: {_nz(start_time)} – {_nz(end_time)}")
                description = ("You’re confirmed for " + title).strip()
                if desc_lines:
                    description += "\n\n" + "\n".join(desc_lines)

                location = " | ".join([p for p in [venue_name, venue_addr] if p]).strip()
                try:
                    ics_bytes = make_ics_bytes(
                        starts_at=starts_at_aware,
                        ends_at=ends_at_aware,
                        summary=title,
                        location=location,
                        description=description,
                    )
                except Exception:
                    ics_bytes = _fallback_ics_bytes(
                        uid=f"{uuid.uuid4().hex}@prs",
                        starts_at=_utc_naive(starts_at_aware),
                        ends_at=_utc_naive(ends_at_aware),
                        summary=title,
                        location=location,
                        description=description,
                    )

                attachments = [{
                    "filename": f"{title}-{_nz(event_dt)}.ics",
                    "mime_type": "text/calendar; method=REQUEST; charset=UTF-8",
                    "data": ics_bytes,
                }]
                has_ics = True

        except Exception:
            has_ics = False
            attachments = None

        subject = f"Player Confirmation: {title} ({_nz(event_dt)})"
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
