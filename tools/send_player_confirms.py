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

# Allow a default timezone name (IANA)
DEFAULT_TZ = _get_secret("DEFAULT_TZ", "America/New_York")


def _sb() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


# -----------------------------
# Small utils
# -----------------------------
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


def _html_escape(s: str) -> str:
    """Minimal HTML escape for &, <, > (enough for notes block)."""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _tzinfo():
    try:
        import zoneinfo
        return zoneinfo.ZoneInfo(DEFAULT_TZ)
    except Exception:
        # Fallback: naive (not ideal, but consistent with legacy code paths)
        return None


def _mk_dt(date_str: str, time_str: Optional[str]) -> Optional[dt.datetime]:
    """
    Build an aware (preferred) or naive datetime from date 'YYYY-MM-DD'
    and time 'HH:MM' string. Returns None if invalid.
    """
    try:
        y, m, d = [int(x) for x in str(date_str).split("-")]
        hh, mm = (0, 0)
        if time_str:
            hh, mm = map(int, str(time_str).split(":")[:2])
        tz = _tzinfo()
        return dt.datetime(y, m, d, hh, mm, tzinfo=tz)
    except Exception:
        return None


def _stage_pref(mus_row: Dict[str, Any]) -> str:
    # Prefer stage_name, fallback to full legal name
    stage = _nz(mus_row.get("stage_name")).strip()
    if stage:
        return stage
    full = " ".join([_nz(mus_row.get("first_name")).strip(), _nz(mus_row.get("last_name")).strip()]).strip()
    return full or _nz(mus_row.get("email"))


# -----------------------------
# Data fetch helpers
# -----------------------------
def _fetch_gig(gig_id: str) -> Dict[str, Any]:
    """
    Load only columns that exist in the gigs schema you provided.
    (Avoids selecting non-existent fields like venue_name.)
    """
    sb = _sb()
    res = (
        sb.table("gigs")
        .select(
            "id, venue_id, agent_id, sound_tech_id, title, event_date, "
            "start_time, end_time, overnight, package_name, total_fee, "
            "contract_status, private_flag, notes, created_by, created_at, "
            "updated_at, fee, is_private, sound_by_venue_name, "
            "sound_by_venue_phone, sound_provided, sound_fee, "
            "closeout_status, closeout_notes, closeout_at, "
            "final_venue_gross, final_venue_paid_date"
        )
        .eq("id", gig_id)
        .limit(1)
        .execute()
    )
    rows = (res.data or [])
    if not rows:
        raise RuntimeError(f"Gig not found: {gig_id}")
    return rows[0]

def _fetch_venue_fields_from_id(venue_id: Optional[str]) -> tuple[str, str]:
    """
    Best-effort venue lookup from `venues` by id. Returns (name, address).
    Tries common column names; returns ('','') if not found.
    """
    if not venue_id:
        return "", ""
    try:
        sb = _sb()
        resp = sb.table("venues").select("*").eq("id", venue_id).limit(1).execute()
        rows = resp.data or []
        if not rows:
            return "", ""
        v = rows[0]
        name = (
            str(
                v.get("name")
                or v.get("venue_name")
                or v.get("display_name")
                or ""
            ).strip()
        )
        addr = (
            str(
                v.get("address")
                or v.get("venue_address")
                or v.get("street")
                or v.get("line1")
                or v.get("full_address")
                or ""
            ).strip()
        )
        return name, addr
    except Exception:
        return "", ""


def _gig_musicians_rows(gig_id: str) -> List[Dict[str, Any]]:
    """
    Return all musician assignment rows for lineup/roles.
    Expected columns:
      - musician_id, role, status (e.g., confirmed)
    """
    sb = _sb()
    res = (
        sb.table("gig_musicians")
        .select("musician_id, role, status")
        .eq("gig_id", gig_id)
        .eq("status", "confirmed")
        .order("created_at", desc=False)
        .execute()
    )
    return res.data or []


def _fetch_musicians_map(ids: List[str]) -> Dict[str, Dict[str, Any]]:
    ids = [i for i in ids if i]
    if not ids:
        return {}
    sb = _sb()
    res = (
        sb.table("musicians")
        .select("id, first_name, last_name, stage_name, email")
        .in_("id", ids)
        .execute()
    )
    rows = res.data or []
    return {str(r.get("id")): r for r in rows}


def _fetch_soundtech_name(sound_tech_id: Optional[str]) -> str:
    """
    Resolve a display name for a sound tech from the sound_techs table.
    Tries common columns: name, stage_name, display_name, first/last, contact_name.
    """
    if not sound_tech_id:
        return ""
    try:
        sb = _sb()
        res = (
            sb.table("sound_techs")
            .select("*")
            .eq("id", sound_tech_id)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            return ""
        st = rows[0]
        name = (
            st.get("name")
            or st.get("stage_name")
            or st.get("display_name")
            or (" ".join([str(st.get("first_name") or "").strip(), str(st.get("last_name") or "").strip()]).strip())
            or st.get("contact_name")
            or ""
        )
        return str(name).strip()
    except Exception:
        return ""


# -----------------------------
# ICS helpers
# -----------------------------
def _build_rfc5545_ics(summary: str, description: str, location: str,
                       dtstart: dt.datetime, dtend: dt.datetime) -> bytes:
    """
    Minimal RFC5545 iCalendar with DTSTART/DTEND. We do not set a timezone
    block here; we serialize `YYYYMMDDT%H%M%SZ` only if dt is UTC; otherwise
    we write local-like naive stamp (consistent with legacy behavior).
    """
    def _fmt_dt(x: dt.datetime) -> str:
        if x.tzinfo and getattr(x.tzinfo, "key", None) == "UTC":
            return x.strftime("%Y%m%dT%H%M%SZ")
        # Naive or non-UTC tz: keep as local naive-like (legacy compatible)
        return x.strftime("%Y%m%dT%H%M%S")

    uid = f"{uuid.uuid4()}@prs"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//PRS//Player Confirm//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"SUMMARY:{summary}",
        f"DTSTART:{_fmt_dt(dtstart)}",
        f"DTEND:{_fmt_dt(dtend)}",
        f"LOCATION:{location}",
        # Keep description simple; escape commas and semicolons minimally
        "DESCRIPTION:" + description.replace("\n", "\\n"),
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def _build_ics_bytes(summary: str, description: str, location: str,
                     dtstart: dt.datetime, dtend: dt.datetime) -> Optional[bytes]:
    """
    Try helper first (existing behavior), then fallback to our RFC builder.
    """
    try:
        return make_ics_bytes(summary, description, location, dtstart, dtend)
    except Exception:
        try:
            return _build_rfc5545_ics(summary, description, location, dtstart, dtend)
        except Exception:
            return None


# -----------------------------
# Audit
# -----------------------------
def _audit_email(token: str, gig_id: str, recipient_email: str,
                 status: str, detail: Dict[str, Any]) -> None:
    """
    Insert an audit row. The table and shape should match your existing schema.
    We include the requested fields in `detail`.
    """
    try:
        sb = _sb()
        _ = (
            sb.table("email_audit")
            .insert({
                "token": token,
                "gig_id": gig_id,
                "recipient_email": recipient_email,
                "kind": "player_confirm",
                "status": status,
                "detail": detail,
            })
            .execute()
        )
    except Exception:
        # best-effort; don’t block sending
        pass


# -----------------------------
# Main sender
# -----------------------------
def send_player_confirms(gig_id: str, musician_ids: Optional[Iterable[str]] = None,
                         cc: Optional[List[str]] = None) -> None:
    """
    Send player confirmations for the given gig. Surgical edits:
      1) Add Notes block to email (escaped, preserves newlines).
      2) Include Confirmed Sound Tech in DESCRIPTION if available.
      3) Cross-midnight ICS fix: if DTEND <= DTSTART, roll DTEND +1 day.

    Everything else (autosend, CC plumbing, gmail_send usage) remains unchanged.
    """
    gig = _fetch_gig(gig_id)
    title = _nz(gig.get("title"))
    event_dt = _nz(gig.get("event_date"))
    start_time = _nz(gig.get("start_time"))
    end_time = _nz(gig.get("end_time"))

    # Derive venue fields defensively (handle multiple schema styles)
    venue_name = _nz(
        gig.get("venue_name")
        or gig.get("venue")
        or gig.get("venue_title")
        or ""
    )
    venue_addr = _nz(
        gig.get("venue_address")
        or gig.get("address")
        or gig.get("venue_addr")
        or ""
    )
    if not venue_name or not venue_addr:
        # Optional lookup via venue_id if present
        vname2, vaddr2 = _fetch_venue_fields_from_id(gig.get("venue_id"))
        if not venue_name:
            venue_name = _nz(vname2)
        if not venue_addr:
            venue_addr = _nz(vaddr2)

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

    # Who to send to
    if musician_ids is None:
        target_ids = ordered_ids[:]
    else:
        target_ids = [str(x) for x in musician_ids if x]

    # Fetch musician data for the full lineup (so we can render "other players")
    mus_map = _fetch_musicians_map(ordered_ids)

    # Determine sound tech by role first, then gig.sound_tech_id, then venue text
    soundtech_name = ""
    for oid in ordered_ids:
        role_txt = roles_by_mid.get(oid, "")
        if "sound" in role_txt.lower():
            soundtech_name = _stage_pref(mus_map.get(oid) or {})
            break
    if not soundtech_name:
        soundtech_name = _fetch_soundtech_name(_nz(gig.get("sound_tech_id")))
    if not soundtech_name:
        soundtech_name = _nz(gig.get("sound_by_venue_name"))

    # Human-readable times
    start_hhmm = _fmt_time12(start_time)
    end_hhmm = _fmt_time12(end_time)

    # Build the "other players" list for the email body + ICS DESCRIPTION
    def _list_others(except_id: str) -> List[str]:
        out: List[str] = []
        for oid in ordered_ids:
            if oid == except_id:
                continue
            nm = _stage_pref(mus_map.get(oid) or {})
            role_txt = roles_by_mid.get(oid, "")
            if role_txt:
                out.append(f"{nm} ({role_txt})")
            else:
                out.append(nm)
        return out

    for mid in target_ids:
        mrow = mus_map.get(mid) or {}
        to_email = _nz(mrow.get("email")).strip()
        if not to_email:
            # no email — audit and continue
            _audit_email(
                token=str(uuid.uuid4()),
                gig_id=gig_id,
                recipient_email="(missing)",
                status="skipped: no recipient email",
                detail={
                    "musician_id": mid,
                    "reason": "missing email",
                },
            )
            continue

        greet = _stage_pref(mrow)
        role_me = roles_by_mid.get(mid, "")

        # Lineup block (this musician + other confirmed players)
        other_players_list = _list_others(mid)
        other_players_count = len(other_players_list)
        lineup_html = ""
        if other_players_list:
            lineup_html = (
                "<h4>Other Confirmed Players</h4>"
                "<div>" + ", ".join(other_players_list) + "</div>"
            )

        # Event details table
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

        # Email subject / body
        subject = f"You’re confirmed: {title} ({_nz(event_dt)})"
        html = f"""
        <p>Hello {greet},</p>
        <p>You’re confirmed for <b>{title}</b>{f" ({role_me})" if role_me else ""}.</p>
        {lineup_html}
        {details_html}
        {notes_html}
        <p>Please reply if anything needs attention.</p>
        """

        # Build ICS (helper first, then RFC fallback) — with cross-midnight fix
        has_ics = False
        starts_at_built, ends_at_built = None, None
        ics_bytes = None

        try:
            starts_at_aware = _mk_dt(event_dt, start_time)
            ends_at_aware = _mk_dt(event_dt, end_time)

            if starts_at_aware and not ends_at_aware:
                # Default 3 hours if only a start was provided
                ends_at_aware = starts_at_aware + dt.timedelta(hours=3)

            # Cross-midnight fix: if end <= start, roll end to next day
            if starts_at_aware and ends_at_aware and ends_at_aware <= starts_at_aware:
                ends_at_aware = ends_at_aware + dt.timedelta(days=1)

            if starts_at_aware and ends_at_aware:
                starts_at_built = starts_at_aware.isoformat()
                ends_at_built = ends_at_aware.isoformat()

                summary = title
                location = " | ".join([p for p in [venue_name, venue_addr] if p]).strip()

                # DESCRIPTION content (keep existing style; append sound tech line if present)
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

                ics_bytes = _build_ics_bytes(summary, description, location, starts_at_aware, ends_at_aware)
                has_ics = bool(ics_bytes)

        except Exception:
            # fall through — we’ll simply send without ICS
            has_ics = False

        # Send
        token = str(uuid.uuid4())
        try:
            if not _is_dry_run():
                gmail_send(
                    to_email=to_email,
                    subject=subject,
                    html=html,
                    ics_bytes=ics_bytes if has_ics else None,  # attach only if built
                    cc=cc or [],
                )

            _audit_email(
                token=token, gig_id=gig_id, recipient_email=to_email,
                status="sent" if not _is_dry_run() else "dry_run",
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
            _audit_email(
                token=token, gig_id=gig_id, recipient_email=to_email,
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

