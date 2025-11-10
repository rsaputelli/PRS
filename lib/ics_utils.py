# lib/ics_utils.py
from datetime import datetime, date, time, timezone
from zoneinfo import ZoneInfo
import uuid
import re

NY_TZ = ZoneInfo("America/New_York")

def _safe_str(v):
    return "" if v is None else str(v)

def _combine_date_time(d, t, tz=NY_TZ) -> datetime:
    """
    Combine a date-like and time-like into a timezone-aware datetime.
    Accepts strings in common formats (e.g., '2025-11-18', '19:30', '7:30 PM').
    """
    # date
    if isinstance(d, datetime):
        d = d.date()
    elif isinstance(d, str):
        # try ISO
        try:
            d = date.fromisoformat(d.strip()[:10])
        except Exception:
            # very defensive; last resort: digits only yyyymmdd?
            m = re.match(r"(\d{4})[-/]?(\d{2})[-/]?(\d{2})", d.strip())
            if not m:
                raise ValueError(f"Unrecognized date: {d!r}")
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    if not isinstance(d, date):
        raise ValueError(f"Unsupported date value: {d!r}")

    # time
    if isinstance(t, datetime):
        t = t.timetz()
    if isinstance(t, str):
        s = t.strip().upper().replace(".", "")
        for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M:%S %p", "%H%M"):
            try:
                tt = datetime.strptime(s, fmt).time()
                break
            except Exception:
                tt = None
        if tt is None:
            # fallback: just midnight
            tt = time(hour=0, minute=0)
        t = tt
    if not isinstance(t, time):
        # fallback to midnight
        t = time(hour=0, minute=0)

    return datetime(d.year, d.month, d.day, t.hour, t.minute, t.second, tzinfo=tz)

def _fmt_dt_ics(dt: datetime) -> str:
    """
    Format to DTSTART/DTEND friendly string with TZID label.
    We keep TZID=America/New_York (most clients will map without VTIMEZONE block).
    """
    # local wall time as YYYYMMDDTHHMMSS
    return dt.strftime("%Y%m%dT%H%M%S")

def _clean_multiline(text: str) -> str:
    # ICS requires CRLF and escaping commas/semicolons where needed.
    # For safety we keep description simple; calendar clients handle plain text well.
    return _safe_str(text).replace("\r\n", "\\n").replace("\n", "\\n")

def build_player_ics(
    *,
    gig: dict,
    recipient_email: str,
    summary: str,
    venue_name: str,
    venue_address: str,
    event_date,
    start_time,
    end_time,
    confirmed_players: list[str],
    confirmed_sound: str | None,
    organizer_email: str | None = None,
    uid_suffix: str | None = None,
) -> tuple[str, bytes]:
    """
    Returns (filename, ics_bytes).
    - Includes METHOD:REQUEST (no RSVP) per spec.
    - Lists other confirmed players and sound in DESCRIPTION.
    """
    # datetimes
    dt_start = _combine_date_time(event_date, start_time, tz=NY_TZ)
    dt_end = _combine_date_time(event_date, end_time, tz=NY_TZ)

    # fields
    lineups = []
    if confirmed_players:
        lineups.append("Players:\n  - " + "\n  - ".join(confirmed_players))
    if confirmed_sound:
        lineups.append(f"Sound: {confirmed_sound}")
    lineup_block = "\n\n".join(lineups)

    desc_lines = []
    if venue_name or venue_address:
        desc_lines.append(f"Venue: {venue_name or ''}")
        if venue_address:
            desc_lines.append(f"Address: {venue_address}")
    desc_lines.append(f"Call/Start: {_safe_str(start_time)}")
    desc_lines.append(f"End: {_safe_str(end_time)}")
    if lineup_block:
        desc_lines.append("")
        desc_lines.append(lineup_block)

    description = _clean_multiline("\n".join(desc_lines))
    location = _clean_multiline(" — ".join([s for s in [venue_name, venue_address] if s]))
    uid = f"prs-{gig.get('id','gig')}-{uid_suffix or uuid.uuid4().hex}@prs"
    dtstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    # minimal ICS (no VALARM, no attendees writes; only the recipient receives this)
    ics = []
    ics.append("BEGIN:VCALENDAR")
    ics.append("PRODID:-//PRS//Band Manager//EN")
    ics.append("VERSION:2.0")
    ics.append("CALSCALE:GREGORIAN")
    ics.append("METHOD:REQUEST")
    ics.append("BEGIN:VEVENT")
    ics.append(f"UID:{uid}")
    ics.append(f"DTSTAMP:{dtstamp}")
    ics.append(f"DTSTART;TZID=America/New_York:{_fmt_dt_ics(dt_start)}")
    ics.append(f"DTEND;TZID=America/New_York:{_fmt_dt_ics(dt_end)}")
    ics.append(f"SUMMARY:{_clean_multiline(summary)}")
    if location:
        ics.append(f"LOCATION:{location}")
    if description:
        ics.append(f"DESCRIPTION:{description}")
    if organizer_email:
        ics.append(f"ORGANIZER:mailto:{organizer_email}")
    ics.append("STATUS:CONFIRMED")
    ics.append("END:VEVENT")
    ics.append("END:VCALENDAR")
    ics_bytes = ("\r\n".join(ics) + "\r\n").encode("utf-8")

    filename = f"{gig.get('title','Gig')}-{dt_start.strftime('%Y%m%d')}.ics".replace(" ", "_")
    return filename, ics_bytes
