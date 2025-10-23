"""
Utility: calendar_utils.py
Purpose: Generate ICS calendar attachments for gig confirmations/reminders.
"""

from datetime import datetime, timedelta

def create_ics(event_title, start_time, end_time, location, description=""):
    """Return a basic ICS file string."""
    return f"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
SUMMARY:{event_title}
DTSTART:{start_time.strftime("%Y%m%dT%H%M%S")}
DTEND:{end_time.strftime("%Y%m%dT%H%M%S")}
LOCATION:{location}
DESCRIPTION:{description}
END:VEVENT
END:VCALENDAR
"""
