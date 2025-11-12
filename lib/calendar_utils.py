# lib/calendar_utils.py
from __future__ import annotations
import datetime as dt

def make_ics_bytes(
    *, uid: str, title: str, starts_at: dt.datetime, ends_at: dt.datetime,
    location: str = "", description: str = ""
) -> bytes:
    def fmt(zdt: dt.datetime) -> str:
        return zdt.strftime("%Y%m%dT%H%M%SZ")

    ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PRS//Gig Calendar//EN
BEGIN:VEVENT
UID:{uid}
DTSTAMP:{fmt(dt.datetime.utcnow())}
DTSTART:{fmt(starts_at.astimezone(dt.timezone.utc))}
DTEND:{fmt(ends_at.astimezone(dt.timezone.utc))}
SUMMARY:{title}
LOCATION:{location}
DESCRIPTION:{description}
END:VEVENT
END:VCALENDAR
"""
    return ics.encode("utf-8")
# ----------------------------------------------------------------------
# Stable public calendar upsert API
# ----------------------------------------------------------------------

def _canonical_upsert(gig_id: str, calendar_name: str, sb=None, **kwargs):
    """
    Internal helper that would call the actual implementation.
    For now, this is a placeholder until the real calendar sync logic
    (Google/Outlook API) is integrated.
    """
    # TODO: implement real calendar sync here
    # For now, just print/log so the call path exists
    print(f"[calendar_utils] upsert_calendar_event placeholder: "
          f"gig_id={gig_id}, calendar_name={calendar_name}")
    return True

def upsert_band_calendar_event(
    gig_id: str,
    sb=None,
    calendar_name: str = "Philly Rock and Soul",
    **kwargs,
):
    """
    Public stable API for all app pages.
    Calls the canonical calendar upsert implementation.
    """
    return _canonical_upsert(
        gig_id=gig_id,
        calendar_name=calendar_name,
        sb=sb,
        **kwargs,
    )


__all__ = ["make_ics_bytes", "upsert_band_calendar_event"]
