# lib/calendar_utils.py
from __future__ import annotations
import datetime as dt
from zoneinfo import ZoneInfo
from typing import Dict, Any

# Keep existing ICS helper unchanged
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
# Google Calendar upsert via OAuth refresh token (Streamlit secrets)
# ----------------------------------------------------------------------
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials as UserCreds
from google.auth.transport.requests import Request


def _get_gcal_service():
    """
    Build a Google Calendar service using OAuth refresh token stored in
    st.secrets['google_oauth'] with keys:
      - client_id
      - client_secret
      - refresh_token
    """
    import streamlit as st

    if "google_oauth" not in st.secrets:
        raise RuntimeError(
            "Missing st.secrets['google_oauth'] (client_id, client_secret, refresh_token)."
        )

    oauth = st.secrets["google_oauth"]
    creds = UserCreds(
        token=None,  # will be refreshed
        refresh_token=oauth.get("refresh_token"),
        client_id=oauth.get("client_id"),
        client_secret=oauth.get("client_secret"),
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/calendar"],
    )
    creds.refresh(Request())
    # cache_discovery=False to avoid file writes on Streamlit Cloud
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _resolve_calendar_id(calendar_name_or_id: str) -> str:
    """
    Resolve a friendly calendar name via st.secrets['gcal_ids'] mapping,
    or pass through a raw calendarId (e.g., ...@group.calendar.google.com).
    """
    import streamlit as st
    mapping = st.secrets.get("gcal_ids", {}) or {}
    return mapping.get(calendar_name_or_id, calendar_name_or_id)


def _fetch_gig_for_calendar(sb, gig_id: str) -> Dict[str, Any]:
    """
    Pull minimal gig fields needed to build the calendar event.
    Adjust select(...) fields here if your schema differs.
    """
    if sb is None:
        raise ValueError("Supabase client (sb) is required to fetch gig details.")

    res = (
        sb.table("gigs")
        .select(
            "id, title, event_date, start_time, end_time, "
            "venue_name, venue_city, venue_state, venue_address"
        )
        .eq("id", gig_id)
        .limit(1)
        .execute()
    )
    rows = (getattr(res, "data", None) or []) if hasattr(res, "data") else (res.data or [])
    if not rows:
        raise ValueError(f"No gig found for id={gig_id}")
    return rows[0]


def _mk_rfc3339(local_dt: dt.datetime, tz: str = "America/New_York") -> str:
    """
    Convert naive local date/time to RFC3339 in the given timezone.
    Assumes naive input is local wall time for that tz.
    """
    if local_dt.tzinfo is None:
        local_dt = local_dt.replace(tzinfo=ZoneInfo(tz))
    return local_dt.isoformat()


def _compose_event_body(gig: Dict[str, Any], calendar_name: str, gig_id: str) -> Dict[str, Any]:
    """
    Build the Google Calendar event body from a gig row.
    """
    title = gig.get("title") or "Gig"
    venue = gig.get("venue_name") or ""
    city = gig.get("venue_city") or ""
    state = gig.get("venue_state") or ""
    address = gig.get("venue_address") or ""
    location_parts = [p for p in [venue, address, f"{city} {state}".strip()] if p]
    location = " | ".join(location_parts)

    # Combine date + times → datetimes
    event_date = gig["event_date"]  # 'YYYY-MM-DD'
    start = str(gig.get("start_time") or "00:00")
    end   = str(gig.get("end_time") or "00:00")
    y, m, d = [int(x) for x in str(event_date).split("-")]
    sh, sm = [int(x) for x in start.split(":")[:2]]
    eh, em = [int(x) for x in end.split(":")[:2]]

    starts_at = dt.datetime(y, m, d, sh, sm)
    ends_at   = dt.datetime(y, m, d, eh, em)
    # If end is past midnight (i.e., <= start), roll to next day
    if ends_at <= starts_at:
        ends_at = ends_at + dt.timedelta(days=1)

    description = f"Calendar: {calendar_name}\nGig ID: {gig_id}"

    return {
        "summary": title,
        "location": location,
        "description": description,
        "start": {"dateTime": _mk_rfc3339(starts_at), "timeZone": "America/New_York"},
        "end":   {"dateTime": _mk_rfc3339(ends_at),   "timeZone": "America/New_York"},
        "reminders": {"useDefault": True},
        # Use private extended property to find/update the same event
        "extendedProperties": {"private": {"gig_id": gig_id}},
    }


def upsert_band_calendar_event(
    gig_id: str,
    sb=None,
    calendar_name: str = "Philly Rock and Soul",
    **kwargs,
):
    """
    Create or update a Calendar event for the given gig.
    Returns on success:
      {"action": "created"|"updated", "eventId": "...", "calendarId": "...", "summary": "..."}
    Returns on failure (no exception leak):
      {"error": "...", "stage": "cal_get|fetch_gig|compose|search|update|insert", "calendarId": "..."}
    """
    from googleapiclient.errors import HttpError

    if not gig_id:
        return {"error": "gig_id is required", "stage": "args"}

    try:
        service = _get_gcal_service()
    except Exception as e:
        return {"error": f"auth/build failure: {e}", "stage": "auth"}

    try:
        calendar_id = _resolve_calendar_id(calendar_name)
        # Validate the calendar id & our permissions explicitly
        _ = service.calendars().get(calendarId=calendar_id).execute()
    except HttpError as he:
        return {"error": f"calendar access error: {he}", "stage": "cal_get", "calendarId": calendar_id}
    except Exception as e:
        return {"error": f"calendar access unexpected: {e}", "stage": "cal_get", "calendarId": calendar_id}

    try:
        gig = _fetch_gig_for_calendar(sb, gig_id)
    except Exception as e:
        return {"error": f"gig fetch failed: {e}", "stage": "fetch_gig", "calendarId": calendar_id}

    try:
        body = _compose_event_body(gig, calendar_name, gig_id)
    except Exception as e:
        return {"error": f"compose failed: {e}", "stage": "compose", "calendarId": calendar_id}

    try:
        events = (
            service.events()
            .list(
                calendarId=calendar_id,
                privateExtendedProperty=f"gig_id={gig_id}",
                maxResults=1,
                singleEvents=True,
                showDeleted=False,
            )
            .execute()
        )
        items = events.get("items", []) or []
    except HttpError as he:
        return {"error": f"event search error: {he}", "stage": "search", "calendarId": calendar_id}
    except Exception as e:
        return {"error": f"event search unexpected: {e}", "stage": "search", "calendarId": calendar_id}

    try:
        if items:
            ev_id = items[0]["id"]
            updated = service.events().patch(calendarId=calendar_id, eventId=ev_id, body=body).execute()
            return {
                "action": "updated",
                "eventId": updated["id"],
                "calendarId": calendar_id,
                "summary": updated.get("summary"),
            }
        else:
            created = service.events().insert(calendarId=calendar_id, body=body).execute()
            return {
                "action": "created",
                "eventId": created["id"],
                "calendarId": calendar_id,
                "summary": created.get("summary"),
            }
    except HttpError as he:
        return {"error": f"event upsert error: {he}", "stage": "update" if items else "insert", "calendarId": calendar_id}
    except Exception as e:
        return {"error": f"event upsert unexpected: {e}", "stage": "update" if items else "insert", "calendarId": calendar_id}

__all__ = ["make_ics_bytes", "upsert_band_calendar_event"]
