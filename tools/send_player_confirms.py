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
    or _get_secret("SUPABASE_SERVICE_KEY")
    or _get_secret("supabase_service_key")
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

def _admin_key() -> Optional[str]:
    # Prefer service-role style keys; fall back to whatever we have
    return (
        _get_secret("SUPABASE_SERVICE_ROLE")
        or _get_secret("SUPABASE_SERVICE_KEY")
        or _get_secret("supabase_service_key")
        or SUPABASE_KEY
    )

def _sb_admin() -> Client:
    sr = _admin_key()   # uses SUPABASE_SERVICE_ROLE, SUPABASE_SERVICE_KEY, or supabase_service_key
    return create_client(SUPABASE_URL, sr)

# -----------------------------
# Fetch helpers (match your schema)
# -----------------------------
def _fetch_gig(gig_id: str) -> Dict[str, Any]:
    res = (
        _sb().table("gigs")
        .select("id, title, event_date, start_time, end_time, venue_id, sound_tech_id, notes, sound_by_venue_name, closeout_notes")
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
            .select("id, display_name, first_name, last_name, company, name_for_1099, email")
            .eq("id", sound_tech_id).limit(1).execute()
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
    # Friendly greeting; allow display_name as first option
    for key in ("display_name", "stage_name"):
        v = (mrow.get(key) or "").strip()
        if v:
            return v
    fn = (mrow.get("first_name") or "").strip()
    ln = (mrow.get("last_name") or "").strip()
    combo = (fn + " " + ln).strip()
    return combo or "there"

def _fmt_time12(t: Optional[str]) -> str:
    # Show "9:00 AM" etc. for email body
    if not t:
        return ""
    s = str(t).strip()
    # Supabase 'time' often looks like HH:MM:SS
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return dt.datetime.strptime(s, fmt).strftime("%I:%M %p").lstrip("0")
        except ValueError:
            continue
    # Already human? just echo
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
    event_date: a date or string ('YYYY-MM-DD' or 'MM/DD/YYYY')
    time_value: a time object or string ('HH:MM[:SS]' or 'h:mm AM/PM')
    -> timezone-aware datetime or None
    """
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
            parts = [p for p in ts.split(":") if p != ""]
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

def _ics_escape(text: str) -> str:
    if not text:
        return ""
    # RFC5545 escaping: backslash, comma, semicolon, and newlines
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )

def _fallback_ics_bytes(uid: str, starts_at: dt.datetime, ends_at: dt.datetime,
                        summary: str, location: str, description: str,
                        tzname: str = "America/New_York") -> bytes:
    """
    Outlook-friendly fallback ICS:
    - Includes a VTIMEZONE block for America/New_York
    - Uses DTSTART;TZID=America/New_York / DTEND;TZID=America/New_York
    - Escapes text per RFC5545
    """
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(tzname)
    starts_local = starts_at.astimezone(tz) if starts_at.tzinfo else starts_at.replace(tzinfo=tz)
    ends_local = ends_at.astimezone(tz) if ends_at.tzinfo else ends_at.replace(tzinfo=tz)

    def _fmt_local(d: dt.datetime) -> str:
        return d.strftime("%Y%m%dT%H%M%S")

    dtstamp = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//PRS//Player Confirm//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:REQUEST",
        "BEGIN:VTIMEZONE",
        f"TZID:{tzname}",
        f"X-LIC-LOCATION:{tzname}",
        "BEGIN:DAYLIGHT",
        "TZOFFSETFROM:-0500",
        "TZOFFSETTO:-0400",
        "TZNAME:EDT",
        "DTSTART:19700308T020000",
        "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU",
        "END:DAYLIGHT",
        "BEGIN:STANDARD",
        "TZOFFSETFROM:-0400",
        "TZOFFSETTO:-0500",
        "TZNAME:EST",
        "DTSTART:19701101T020000",
        "RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU",
        "END:STANDARD",
        "END:VTIMEZONE",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART;TZID={tzname}:{_fmt_local(starts_local)}",
        f"DTEND;TZID={tzname}:{_fmt_local(ends_local)}",
        f"SUMMARY:{_ics_escape(summary)}",
        f"LOCATION:{_ics_escape(location)}".rstrip(),
        "DESCRIPTION:" + _ics_escape(description),
        "END:VEVENT",
        "END:VCALENDAR",
        "",
    ]
    return ("\r\n".join(lines)).encode("utf-8")

# -----------------------------
# Public API (minimal edits elsewhere)
# -----------------------------
def send_player_confirms(
    gig_id: str,
    only_players: Optional[Iterable[str]] = None,
    cc: Optional[list[str]] = None
) -> None:
    """
    Send player confirmation emails.

    If only_players is provided, email ONLY those musicians.
    If omitted/None, email ALL players assigned to the gig.
    ICS/calendar logic remains unchanged for all players.
    """
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

    # --- PATCH: only email selected players, but maintain full ICS context ---
    if only_players is None:
        # Default behavior: email everyone
        target_ids = ordered_ids[:]
    else:
        # Filter to the explicitly-specified set
        only_players_set = {str(x) for x in only_players if x}
        target_ids = [mid for mid in ordered_ids if mid in only_players_set]

    # Fetch musician data for the full lineup (so we can render "other players")
    mus_map = _fetch_musicians_map(ordered_ids)

    # Build human-times for email
    start_hhmm = _fmt_time12(start_time)
    end_hhmm = _fmt_time12(end_time)

    # --- Resolve confirmed sound tech (priority: gig.sound_tech_id → lineup role → venue-provided) ---
    soundtech_name = ""
    stid_str = str(gig.get("sound_tech_id") or "").strip()

    # 1) By explicit sound_tech_id on the gig (admin client to bypass RLS)
    soundtech_name = ""
    stid_str = str(gig.get("sound_tech_id") or "").strip()
    soundtech_lookup_rows = 0
    soundtech_admin_key_present = bool(_admin_key())
    soundtech_row_keys = []
    cand_dn = cand_fl = cand_co = cand_nf = cand_em = ""

    if stid_str:
        try:
            st_rows = (
                _sb_admin().table("sound_techs")
                .select("id, display_name, first_name, last_name, company, name_for_1099, email")
                .eq("id", stid_str).limit(1).execute().data or []
            )
            soundtech_lookup_rows = len(st_rows)
            if st_rows:
                st = st_rows[0]
                soundtech_row_keys = list(st.keys())

                dn = (st.get("display_name") or "").strip();       cand_dn = dn
                fn = (st.get("first_name") or "").strip()
                ln = (st.get("last_name") or "").strip()
                fl = (f"{fn} {ln}").strip();                        cand_fl = fl
                co = (st.get("company") or "").strip();             cand_co = co
                nf = (st.get("name_for_1099") or "").strip();       cand_nf = nf
                em = (st.get("email") or "").strip();               cand_em = em

                # Preference: display_name → "First Last" → company → name_for_1099 → email
                if dn:
                    soundtech_name = dn
                elif fl:
                    soundtech_name = fl
                elif co:
                    soundtech_name = co
                elif nf:
                    soundtech_name = nf
                elif em:
                    soundtech_name = em
        except Exception:
            # swallow and continue to fallbacks below
            pass

    # 2) If still blank, detect by lineup role keywords
    if not soundtech_name:
        ROLE_KEYS = ("sound", "audio", "engineer", "tech")
        for oid in ordered_ids:
            rtxt = (roles_by_mid.get(oid, "") or "").lower()
            if any(k in rtxt for k in ROLE_KEYS):
                soundtech_name = _stage_pref(mus_map.get(oid) or {})
                break

    # 3) Final fallback: venue-provided text (rare)
    if not soundtech_name:
        alt = _nz(gig.get("sound_by_venue_name"))
        if alt:
            soundtech_name = alt

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

        # show a sound-tech line if we have either a name OR an ID (so you see something even if lookup fails)
        if soundtech_name or stid_str:
            label = soundtech_name or f"(ID: {stid_str[:8]}…)"  # short, doesn’t leak full UUID
            lineup_html += f"<li><b>Confirmed sound tech:</b> {label}</li>"

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
        # Optional fallback (safe if column doesn't exist / is NULL)
        if not (notes_raw and str(notes_raw).strip()):
            notes_raw = gig.get("closeout_notes")

        notes_present = bool(notes_raw and str(notes_raw).strip())
        notes_html = ""
        if notes_present:
            notes_html = f"""
            <!-- notes_len:{len(str(notes_raw))} preview:{_html_escape(str(notes_raw)[:40])} -->
            <h4>Notes</h4>
            <div style="white-space:pre-wrap">{_html_escape(notes_raw)}</div>
            """
        else:
            # add a hidden breadcrumb so we can view-source and confirm what happened
            notes_html = "<!-- notes_present:false -->"

        stage_name = str(mrow.get("stage_name") or "").strip()
        html = f"""
        <p>Hello {stage_name or greet},</p>
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
                # === Add gig Notes into ICS description ===
                notes_ics = ""
                if notes_present:
                    notes_ics = _ics_escape(notes_raw)
                    description += "\n\nNotes:\n" + notes_ics

                try:
                    ics_bytes = make_ics_bytes(
                        starts_at=starts_at_aware,
                        ends_at=ends_at_aware,
                        summary=summary,
                        location=location,
                        description=description,
                    )
                except Exception:
                    # Fallback: generate Outlook-friendly ICS with timezone
                    ics_bytes = _fallback_ics_bytes(
                        uid=f"{uuid.uuid4().hex}@prs",
                        starts_at=starts_at_aware,
                        ends_at=ends_at_aware,
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

            # ---- GMAIL DEBUG ----
            try:
                import streamlit as st
                gmail_cfg = st.secrets.get("gmail", {})
            except Exception:
                gmail_cfg = {}

            import os
            debug_gmail = {
                "has_gmail_block": bool(gmail_cfg),
                "gmail.client_id": bool(gmail_cfg.get("client_id")),
                "gmail.client_secret": bool(gmail_cfg.get("client_secret")),
                "gmail.refresh_token": bool(gmail_cfg.get("refresh_token")),
                "env.GMAIL_CLIENT_ID": bool(os.environ.get("GMAIL_CLIENT_ID")),
                "env.GMAIL_CLIENT_SECRET": bool(os.environ.get("GMAIL_CLIENT_SECRET")),
                "env.GMAIL_REFRESH_TOKEN": bool(os.environ.get("GMAIL_REFRESH_TOKEN")),
                "env.GMAIL_TOKEN_JSON": bool(os.environ.get("GMAIL_TOKEN_JSON")),
            }
            print("GMAIL_DEBUG(player)", debug_gmail)

            # ---- actual send ----
            if not _is_dry_run():
                result = gmail_send(
                    subject,
                    to_email,
                    html,
                    cc=(cc or [CC_RAY]),
                    attachments=attachments,
                )
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
                    "notes_present": notes_present,
                    "notes_len": len(str(notes_raw or "")),
                    "gig_sound_tech_id": gig.get("sound_tech_id"),
                    "soundtech_present": bool(soundtech_name),
                    "soundtech_lookup_rows": soundtech_lookup_rows,
                    "soundtech_admin_key_present": soundtech_admin_key_present,
                    "soundtech_row_keys": soundtech_row_keys,
                    "soundtech_candidates": {
                        "display_name": cand_dn,
                        "first_last": cand_fl,
                        "company": cand_co,
                        "name_for_1099": cand_nf,
                        "email": cand_em,
                    },
                    "soundtech_label_used": soundtech_name or (f"(ID: {stid_str[:8]}…)" if stid_str else ""),
                },
            )
