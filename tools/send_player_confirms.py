# tools/send_player_confirms.py

from __future__ import annotations
import os
import uuid
import datetime as dt
from typing import Dict, Any, Iterable, Optional, List

from supabase import create_client, Client
from lib.email_utils import gmail_send
from lib.calendar_utils import make_ics_bytes  # keep import; we will try it first


# -----------------------------
# Secrets / config
# -----------------------------
def _get_secret(name: str, default: Optional[str] = None):
    """
    Resolve secret from environment with a simple fallback.
    (If you use Streamlit elsewhere, this stays compatible.)
    """
    try:
        import streamlit as st  # noqa: WPS433
        try:
            return st.secrets[name]
        except Exception:
            # allow env fallback
            return os.environ.get(name, default)
    except Exception:
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
# Fetch helpers (match your schema)
# -----------------------------
def _fetch_gig(gig_id: str) -> Dict[str, Any]:
    res = (
        _sb().table("gigs")
        .select("id, title, event_date, start_time, end_time, venue_id, sound_tech_id, notes")
        .eq("id", gig_id).limit(1).execute()
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
        .eq("id", venue_id).limit(1).execute()
    )
    rows = res.data or []
    return rows[0] if rows else {}

def _gig_musicians_rows(gig_id: str) -> List[Dict[str, Any]]:
    # Need both musician_id and role for lineup and detection
    res = (
        _sb().table("gig_musicians")
        .select("musician_id, role")
        .eq("gig_id", gig_id).execute()
    )
    return res.data or []

def _fetch_musicians_map(ids: List[str]) -> Dict[str, Dict[str, Any]]:
    if not ids:
        return {}
    res = (
        _sb().table("musicians")
        .select("id, email, stage_name, display_name, first_name, last_name")
        .in_("id", ids).execute()
    )
    rows = res.data or []
    return {str(r["id"]): r for r in rows if r.get("id") is not None}

def _fetch_soundtech_name(sound_tech_id: Optional[str]) -> str:
    """Fallback only if we didn't find a 'sound' role in lineup."""
    if not sound_tech_id:
        return ""
    try:
        # Be flexible about columns on sound_techs
        res = (
            _sb().table("sound_techs")
            .select("id, stage_name, display_name, first_name, last_name, name")
            .eq("id", sound_tech_id).limit(1).execute()
        )
        rows = res.data or []
        if not rows:
            return ""
        st = rows[0]
        stage = str(st.get("stage_name") or st.get("display_name") or "").strip()
        if stage:
            return stage
        full = " ".join([str(st.get("first_name") or "").strip(), str(st.get("last_name") or "").strip()]).strip()
        if full:
            return full
        return str(st.get("name") or "").strip()
    except Exception:
        return ""

# -----------------------------
# Formatting helpers
# -----------------------------
def _greet_name(m: Dict[str, Any]) -> str:
    s = str(m.get("stage_name") or m.get("display_name") or "").strip()
    if s:
        return s
    first = str(m.get("first_name") or "").strip()
    last = str(m.get("last_name") or "").strip()
    full = " ".join([first, last]).strip()
    return full or str(m.get("email") or "").strip()

def _stage_pref(m: Dict[str, Any]) -> str:
    s = str(m.get("stage_name") or m.get("display_name") or "").strip()
    if s:
        return s
    first = str(m.get("first_name") or "").strip()
    last = str(m.get("last_name") or "").strip()
    full = " ".join([first, last]).strip()
    return full or str(m.get("email") or "").strip()

def _nz(x: Any, alt: str = "") -> str:
    return alt if x is None else str(x)

def _fmt_time12(hhmm: Optional[str]) -> str:
    if not hhmm:
        return ""
    try:
        hh, mm = map(int, str(hhmm).split(":")[:2])
        ampm = "AM" if hh < 12 else "PM"
        hh12 = hh if 1 <= hh <= 12 else (12 if hh == 0 else (hh - 12))
        return f"{hh12}:{mm:02d} {ampm}"
    except Exception:
        return str(hhmm)

def _fmt_addr(v: Dict[str, Any]) -> str:
    parts: List[str] = []
    l1 = str(v.get("address_line1") or "").strip()
    l2 = str(v.get("address_line2") or "").strip()
    city = str(v.get("city") or "").strip()
    state = str(v.get("state") or "").strip()
    pc = str(v.get("postal_code") or "").strip()
    if l1: parts.append(l1)
    if l2: parts.append(l2)
    tail = " ".join(p for p in [city, state, pc] if p).strip()
    if tail: parts.append(tail)
    return " | ".join(parts)

def _html_escape(s: str) -> str:
    s = str(s or "")
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;"))

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
# Date/time for ICS (robust; from your schema)
# -----------------------------
def _mk_dt(event_date: Any, time_value: Any, tzname: str = "America/New_York") -> Optional[dt.datetime]:
    """
    Build a timezone-aware datetime for ICS, accepting:
      - event_date as date/datetime/str(YYYY-MM-DD)
      - time_value as time/datetime/str(HH:MM)
    """
    if event_date is None:
        return None
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tzname)
    except Exception:
        tz = None

    # normalize date
    if isinstance(event_date, dt.datetime):
        y, m, d = event_date.year, event_date.month, event_date.day
    elif isinstance(event_date, dt.date):
        y, m, d = event_date.year, event_date.month, event_date.day
    else:
        y, m, d = [int(x) for x in str(event_date).split("-")]

    # normalize time
    hh, mm = 0, 0
    if time_value:
        if isinstance(time_value, dt.time):
            hh, mm = time_value.hour, time_value.minute
        elif isinstance(time_value, dt.datetime):
            hh, mm = time_value.hour, time_value.minute
        else:
            hh, mm = map(int, str(time_value).split(":")[:2])

    try:
        return dt.datetime(y, m, d, hh, mm, tzinfo=tz)
    except Exception:
        return None

def _utc_naive(d: dt.datetime) -> dt.datetime:
    """Convert aware datetime to UTC naive; if already naive, return as-is."""
    if d.tzinfo:
        return (d.astimezone(dt.timezone.utc)).replace(tzinfo=None)
    return d

def _fallback_ics_bytes(uid: str, starts_at: dt.datetime, ends_at: dt.datetime,
                        summary: str, location: str, description: str) -> bytes:
    """Simple RFC5545-compliant ICS (UTC naive inputs)."""
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
        f"LOCATION:{location}".rstrip(),
        "DESCRIPTION:" + description.replace("\n", "\\n"),
        "END:VEVENT",
        "END:VCALENDAR",
        ""
    ]
    return ("\r\n".join(lines)).encode("utf-8")

# -----------------------------
# Public API (minimal edits elsewhere)
# -----------------------------
def send_player_confirms(gig_id: str, musician_ids: Optional[Iterable[str]] = None, cc: Optional[list[str]] = None) -> None:
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
    # === Sound tech detection (role contains 'sound'; fallback to gigs.sound_tech_id / venue note) ===
    soundtech_name = ""
    for oid in ordered_ids:
        r = (roles_by_mid.get(oid, "") or "")
        if "sound" in r.lower():
            soundtech_name = _stage_pref(mus_map.get(oid) or {})
            break

    if not soundtech_name:
        try:
            soundtech_name = _fetch_soundtech_name(_nz(gig.get("sound_tech_id")))
        except Exception:
            soundtech_name = ""

    if not soundtech_name:
        alt = _nz(gig.get("sound_by_venue_name"))
        if alt:
            soundtech_name = alt  # e.g., "Venue-provided"

    # Who to send to
    if musician_ids is None:
        target_ids = ordered_ids[:]
    else:
        target_ids = [str(x) for x in musician_ids if x]

    # Fetch musician data for the full lineup (so we can render "other players")
    mus_map = _fetch_musicians_map(ordered_ids)

    # Build human-times for email
    start_hhmm = _fmt_time12(start_time)
    end_hhmm = _fmt_time12(end_time)

    # Sound tech via role detection first
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

        # ----- Other players (Stage Name + Role), exclude recipient -----
        other_players_list: List[str] = []
        for oid in ordered_ids:
            if oid == mid:
                continue
            orow = mus_map.get(oid) or {}
            name = _stage_pref(orow)
            r = roles_by_mid.get(oid, "")
            other_players_list.append(f"{name}{f' ({r})' if r else ''}")
        other_players_count = len(other_players_list)

        # ----- Email body -----
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

        # --- Notes block (escaped, preserves newlines) ---
        notes_raw = gig.get("notes")
        notes_html = ""
        if notes_raw:
            notes_html = f"""
            <h4>Notes</h4>
            <div style="white-space:pre-wrap">{_html_escape(notes_raw)}</div>
            """

        html = f"""
        <p>Hello {greet},</p>
        <p>You’re confirmed for <b>{title}</b>{f" ({role_me})" if role_me else ""}.</p>
        {lineup_html}
        {details_html}
        {notes_html}
        <p>Please reply if anything needs attention.</p>
        """

        # ----- ICS attachment (robust) -----
        has_ics = False
        starts_at_built = None
        ends_at_built = None
        attachments = None

        try:
            starts_at_aware = _mk_dt(event_dt, start_time)
            ends_at_aware = _mk_dt(event_dt, end_time)

            if starts_at_aware and not ends_at_aware:
                ends_at_aware = starts_at_aware + dt.timedelta(hours=3)

            # Cross-midnight fix: if end <= start, roll end to next day
            if starts_at_aware and ends_at_aware and ends_at_aware <= starts_at_aware:
                ends_at_aware = ends_at_aware + dt.timedelta(days=1)

            if starts_at_aware and ends_at_aware:
                starts_at_built = starts_at_aware.isoformat()
                ends_at_built = ends_at_aware.isoformat()

                # Try helper first with safe args
                summary = title
                location = " | ".join([p for p in [venue_name, venue_addr] if p]).strip()

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

                try:
                    ics_bytes = make_ics_bytes(
                        starts_at=starts_at_aware,
                        ends_at=ends_at_aware,
                        summary=summary,
                        location=location,
                        description=description,
                    )
                except Exception:
                    # Fallback: generate minimal ICS in UTC naive
                    ics_bytes = _fallback_ics_bytes(
                        uid=f"{uuid.uuid4().hex}@prs",
                        starts_at=_utc_naive(starts_at_aware),
                        ends_at=_utc_naive(ends_at_aware),
                        summary=summary,
                        location=location,
                        description=description,
                    )

                attachments = [{
                    "filename": f"{title}-{_nz(event_dt)}.ics",
                    "mime": "text/calendar; method=REQUEST; charset=UTF-8",
                    "content": ics_bytes,
                }]
                has_ics = True

        except Exception:
            has_ics = False
            attachments = None

        # ----- Send -----
        subject = f"Player Confirmation: {title} ({_nz(event_dt)})"
        try:
            if not _is_dry_run():
                result = gmail_send(subject, to_email, html, cc=(cc or [CC_RAY]), attachments=attachments)
                if not result:
                    raise RuntimeError("gmail_send returned a non-success value")
            _insert_email_audit(
                token=token, gig_id=gig_id, recipient_email=to_email,
                kind="player_confirm", status=("dry_run" if _is_dry_run() else "sent"),
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
