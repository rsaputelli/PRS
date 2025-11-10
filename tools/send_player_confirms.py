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
        .select("id, title, event_date, start_time, end_time, venue_id")
        .eq("id", gig_id)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    if not rows:
        raise ValueError(f"Gig {gig_id} not found or not accessible (RLS).")
    return rows[0]

def _fetch_venue(venue_id: str | None) -> Dict[str, Any]:
    if not venue_id:
        return {}
    res = _sb().table("venues").select(
        "name, address_line1, address_line2, city, state, postal_code"
    ).eq("id", venue_id).limit(1).execute()
    rows = res.data or []
    return rows[0] if rows else {}

def _gig_musician_ids(gig_id: str) -> List[str]:
    res = _sb().table("gig_musicians").select("musician_id, role").eq("gig_id", gig_id).execute()
    rows = res.data or []
    ids = []
    for r in rows:
        mid = r.get("musician_id")
        if mid:
            ids.append(str(mid))
    # unique, preserve order
    seen = set(); ordered = []
    for m in ids:
        if m not in seen:
            seen.add(m); ordered.append(m)
    return ordered

def _fetch_musicians_map(ids: List[str]) -> Dict[str, Dict[str, Any]]:
    if not ids:
        return {}
    # Select only columns that exist in your schema
    res = _sb().table("musicians").select(
        "id, email, display_name, first_name, last_name, stage_name"
    ).in_("id", ids).execute()
    rows = res.data or []
    out = {}
    for r in rows:
        mid = r.get("id")
        if mid is not None:
            out[str(mid)] = r
    return out

def _gig_roles_for(gig_id: str) -> Dict[str, str]:
    res = _sb().table("gig_musicians").select("musician_id, role").eq("gig_id", gig_id).execute()
    rows = res.data or []
    m = {}
    for r in rows:
        mid = r.get("musician_id")
        if mid is not None:
            m[str(mid)] = (r.get("role") or "").strip()
    return m

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

def _mus_display_name(r: Dict[str, Any]) -> str:
    # Prefer display/stage; fall back to first + last; then "there"
    for key in ("display_name", "stage_name"):
        v = (r.get(key) or "").strip()
        if v:
            return v
    fn = (r.get("first_name") or "").strip()
    ln = (r.get("last_name") or "").strip()
    combo = (fn + " " + ln).strip()
    return combo or "there"

def _mus_stage_display(r: Dict[str, Any]) -> str:
    """Prefer Stage Name (as in UI), fall back to display_name, then first+last."""
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
def send_player_confirms(gig_id: str, musician_ids: Optional[Iterable[str]] = None, cc: Optional[list[str]] = None) -> None:
    """
    Send confirmations to the specified musicians. If musician_ids is None,
    sends to the current lineup for the gig. Audits every outcome.
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

    # Build target list
    if musician_ids is None:
        target_ids = _gig_musician_ids(gig_id)
    else:
        target_ids = [str(x) for x in musician_ids if x]

    if not target_ids:
        # Nothing to do; no exception (queue runner should just proceed)
        return

    roles = _gig_roles_for(gig_id)
    mus_map = _fetch_musicians_map(target_ids)

    for mid in target_ids:
        token = uuid.uuid4().hex
        mrow = mus_map.get(mid) or {}
        to_email = _nz(mrow.get("email"))

        if not to_email:
            _insert_email_audit(
                token=token,
                gig_id=gig_id,
                recipient_email="",
                kind="player_confirm",
                status="skipped-no-email",
                detail={"musician_id": mid, "errors": "musician-has-no-email"},
            )
            continue

        role = roles.get(mid, "")
        greet = _mus_display_name(mrow)

        # ---------- Build lineup (Stage Name + Role), sound tech, and counts ----------
        other_players_list = []
        for _id in target_ids:
            if _id == mid:
                continue
            _row = mus_map.get(_id) or {}
            name = _mus_stage_display(_row)  # Stage Name preferred
            r = (roles.get(_id, "") or "").strip()
            if name:
                other_players_list.append(f"{name}{f' ({r})' if r else ''}")

        other_players_count = len(other_players_list)

        soundtech_name = ""
        try:
            for _id in target_ids:
                r = (roles.get(_id, "") or "")
                if "sound" in r.lower():
                    _row = mus_map.get(_id) or {}
                    soundtech_name = _mus_stage_display(_row)
                    break
        except Exception:
            soundtech_name = ""

        # ---------- Build Lineup + Event Details HTML ----------
        extra_html = ""
        if other_players_list or soundtech_name:
            extra_html = "<h4>Lineup</h4><ul>"
            if other_players_list:
                extra_html += f"<li><b>Other confirmed players:</b> {', '.join(other_players_list)}</li>"
            if soundtech_name:
                extra_html += f"<li><b>Confirmed sound tech:</b> {soundtech_name}</li>"
            extra_html += "</ul>"

        extra_html += f"""
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
        <p>You’re confirmed for <b>{title}</b>{f" ({role})" if role else ""}.</p>
        {extra_html}
        <p>Please reply if anything needs attention.</p>
        """

        # ---------- Build .ics attachment ----------
        attachments = None
        has_ics = False
        starts_at_built: Optional[str] = None
        ends_at_built: Optional[str] = None

        try:
            summary = title
            location = " | ".join([p for p in [venue_name, venue_addr] if p]).strip()
            base_description = f"You’re confirmed for {title}."

            # DESCRIPTION content with Stage Name + Role list
            extra_lines = []
            if other_players_list:
                extra_lines.append("Other confirmed players: " + ", ".join(other_players_list))
            if soundtech_name:
                extra_lines.append(f"Confirmed sound tech: {soundtech_name}")
            if venue_name:
                extra_lines.append(f"Venue: {venue_name}")
            if event_dt:
                extra_lines.append(f"Event date: {event_dt}")
            if start_hhmm or end_hhmm:
                extra_lines.append(f"Start and end times: {start_hhmm or ''} – {end_hhmm or ''}")

            description = base_description
            if extra_lines:
                description = (description.rstrip() + "\n\n" + "\n".join(extra_lines)).strip()

            # Build timezone-aware datetimes
            from zoneinfo import ZoneInfo
            _tz = ZoneInfo("America/New_York")

            def _mk_dt(date_str, time_str):
                try:
                    ds = str(date_str).strip()
                    # Date: MM/DD/YYYY or YYYY-MM-DD
                    if "/" in ds:
                        m, d, y = [int(x) for x in ds.split("/")]
                    else:
                        y, m, d = [int(x) for x in ds.split("-")]

                    # Time: supports "h:mm AM/PM" or "HH:MM[:SS]"
                    hh, mm = 0, 0
                    ts = (str(time_str) if time_str else "").strip()
                    if ts:
                        t_upper = ts.upper()
                        ampm = None
                        if t_upper.endswith("AM") or t_upper.endswith("PM"):
                            ampm = "PM" if t_upper.endswith("PM") else "AM"
                            ts = t_upper.replace("AM", "").replace("PM", "").strip()

                        parts = [p for p in ts.split(":") if p != ""]
                        if len(parts) >= 2:
                            hh = int(parts[0]); mm = int(parts[1])

                        if ampm == "PM" and hh < 12:
                            hh += 12
                        if ampm == "AM" and hh == 12:
                            hh = 0

                    return dt.datetime(int(y), int(m), int(d), int(hh), int(mm), tzinfo=_tz)
                except Exception:
                    return None

            starts_at = _mk_dt(event_dt, gig.get("start_time"))
            ends_at   = _mk_dt(event_dt, gig.get("end_time"))

            if starts_at:
                starts_at_built = starts_at.isoformat()
            if ends_at:
                ends_at_built = ends_at.isoformat()

            # Only attach if both datetimes are valid
            if starts_at and ends_at:
                # Pass only safe, commonly supported args
                ics_bytes = make_ics_bytes(
                    starts_at=starts_at,
                    ends_at=ends_at,
                    summary=summary,
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
            attachments = None
            has_ics = False

        subject = f"Player Confirmation: {title} ({event_dt})"
        try:
            if not _is_dry_run():
                result = gmail_send(subject, to_email, html, cc=(cc or [CC_RAY]), attachments=attachments)
                if not result:
                    raise RuntimeError("gmail_send returned a non-success value (None/False)")
            _insert_email_audit(
                token=token,
                gig_id=gig_id,
                recipient_email=to_email,
                kind="player_confirm",
                status=("dry-run" if _is_dry_run() else "sent"),
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
                token=token,
                gig_id=gig_id,
                recipient_email=to_email,
                kind="player_confirm",
                status=f"error: {e}",
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
