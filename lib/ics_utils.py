# lib/ics_utils.py
from __future__ import annotations
from datetime import datetime, date, time, timezone
from zoneinfo import ZoneInfo
from typing import List, Optional
import uuid
import re

NY_TZ = ZoneInfo("America/New_York")

def _safe_str(v) -> str:
    return "" if v is None else str(v)

def _combine_date_time(d, t, tz: ZoneInfo = NY_TZ) -> datetime:
    """Combine date-like and time-like into a timezone-aware datetime."""
    # ---- date ----
    if isinstance(d, datetime):
        d = d.date()
    elif isinstance(d, str):
        s = d.strip()
        # ISO first
        try:
            d = date.fromisoformat(s[:10])
        except Exception:
            m = re.match(r"(\d{4})[-/]?(\d{2})[-/]?(\d{2})", s)
            if not m:
                raise ValueError(f"Unrecognized date: {d!r}")
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    if not isinstance(d, date):
        raise ValueError(f"Unsupported date value: {d!r}")

    # ---- time ----
    if isinstance(t, datetime):
        t = t.timetz()
    if isinstance(t, str):
        s = t.strip().upper().replace(".", "")
        parsed: Optional[time] = None
        for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M:%S %p", "%H%M"):
            try:
                parsed = datetime.strptime(s, fmt).time()
                break
            except Exception:
                continue
        if parsed is None:
            parsed = time(0, 0, 0)
        t = parsed
    if not isinstance(t, time):
        t = time(0, 0, 0)

    return datetime(d.year, d.month, d.day, t.hour, t.minute, t.second, tzinfo=tz)

def _fmt_dt_local(dt: datetime) -> str:
    """Format local time to ICS-friendly YYYYMMDDTHHMMSS (with TZID on the property)."""
    return dt.strftime("%Y%m%dT%H%M%S")

def _clean_multiline(text: str) -> str:
    return _safe_str(text).replace("\r\n", "\\n").replace("\n", "\\n")

def build_player_ics(
    *,
    gig: dict,
    summary: str,
    venue_name: str,
    venue_address: str,
    event_date,
    start_time,
    end_time,
    confirmed_players: Optional[List[str]] = None,
    confirmed_sound: Optional[str] = None,
    organizer_email: Optional[str] = None,
    uid_suffix: Optional[str] = None,
    # kept optional to avoid call-site breakage in either direction
    recipient_email: Optional[str] = None,
) -> tuple[str, bytes]:
    """
    Return (filename, ics_bytes).
    Only includes: venue + address, event date, start/end times, other confirmed players, confirmed sound.
    """
    dt_start = _combine_date_time(event_date, start_time, tz=NY_TZ)
    dt_end   = _combine_date_time(event_date, end_time,   tz=NY_TZ)

    # Description lines (only requested fields)
    desc_lines = []
    if venue_name or venue_address:
        if venue_name:
            desc_lines.append(f"Venue: {venue_name}")
        if venue_address:
            desc_lines.append(f"Address: {venue_address}")
    desc_lines.append(f"Start: {_safe_str(start_time)}")
    desc_lines.append(f"End: {_safe_str(end_time)}")

    lines = []
    if confirmed_players:
        lines.append("Players:\n  - " + "\n  - ".join(confirmed_players))
    if confirmed_sound:
        lines.append(f"Sound: {confirmed_sound}")
    if lines:
        desc_lines.append("")
        desc_lines.extend(lines)

    description = _clean_multiline("\n".join(desc_lines))
    location = _clean_multiline(" — ".join([s for s in [venue_name, venue_address] if s]))

    uid = f"prs-{gig.get('id','gig')}-{uid_suffix or uuid.uuid4().hex}@prs"
    dtstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    ics = []
    ics.append("BEGIN:VCALENDAR")
    ics.append("PRODID:-//PRS//Band Manager//EN")
    ics.append("VERSION:2.0")
    ics.append("CALSCALE:GREGORIAN")
    ics.append("METHOD:REQUEST")
    ics.append("BEGIN:VEVENT")
    ics.append(f"UID:{uid}")
    ics.append(f"DTSTAMP:{dtstamp}")
    ics.append(f"DTSTART;TZID=America/New_York:{_fmt_dt_local(dt_start)}")
    ics.append(f"DTEND;TZID=America/New_York:{_fmt_dt_local(dt_end)}")
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

    data = ("\r\n".join(ics) + "\r\n").encode("utf-8")
    fname = f"{(gig.get('title') or 'Gig').replace(' ', '_')}-{dt_start.strftime('%Y%m%d')}.ics"
    return fname, data
