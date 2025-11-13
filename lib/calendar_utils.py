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
    st.secrets['google_oauth'] with keys: client_id, client_secret, refresh_token.
    Uses the calendar.events scope to match your existing token.
    """
    import streamlit as st
    from googleapiclient.discovery import build
    from google.oauth2.credentials import Credentials as UserCreds
    from google.auth.transport.requests import Request

    if "google_oauth" not in st.secrets:
        raise RuntimeError("Missing st.secrets['google_oauth'].")

    oauth = st.secrets["google_oauth"]
    cid = oauth.get("client_id")
    csec = oauth.get("client_secret")
    rtok = oauth.get("refresh_token")
    if not (cid and csec and rtok):
        raise RuntimeError("google_oauth must include client_id, client_secret, refresh_token.")

    creds = UserCreds(
        token=None,  # will be refreshed
        refresh_token=rtok,
        client_id=cid,
        client_secret=csec,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/calendar.events"],
    )

    try:
        creds.refresh(Request())
    except Exception as e:
        raise RuntimeError(f"OAuth refresh failed (check scope=calendar.events and token validity): {e}")

    # Build the service; ensure we don't return None
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    if service is None:
        raise RuntimeError("Google API build('calendar','v3', ...) returned None")

    # Debug marker in logs so we know this executed successfully
    print("GCAL_SERVICE_OK", {"scopes": creds.scopes})
    return service


def _resolve_calendar_id(calendar_name_or_id: str) -> str:
    """
    Resolve a friendly calendar name via st.secrets['gcal_ids'] mapping,
    or pass through a raw calendarId (e.g., ...@group.calendar.google.com).
    """
    import streamlit as st
    mapping = st.secrets.get("gcal_ids", {}) or {}
    return mapping.get(calendar_name_or_id, calendar_name_or_id)


def _fetch_gig_for_calendar(sb: "Client", gig_id: str) -> dict:
    """
    Load the gig plus its venue fields (joined via venue_id).
    Returns a dict with the keys expected by _compose_event_body().
    Includes title/notes so calendar summary & description are correct.
    """
    if not sb:
        raise RuntimeError("Supabase client (sb) is required to fetch gig")

    # Join venues through the foreign key venue_id -> venues.id
    sel = (
        "id,event_date,start_time,end_time,is_private,"
        "title,notes,"                     # <-- removed event_title
        "venue_id,"
        "venues:venue_id(name,address_line1,city,state)"
    )

    resp = (
        sb.table("gigs")
          .select(sel)
          .eq("id", gig_id)
          .limit(1)
          .execute()
    )
    rows = getattr(resp, "data", None) or []
    if not rows:
        raise RuntimeError(f"gig not found: {gig_id}")

    g = rows[0] or {}
    v = (g.get("venues") or {}) if isinstance(g.get("venues"), dict) else {}

    # Normalize for _compose_event_body()
    return {
        "id": g.get("id"),
        "event_date": g.get("event_date"),
        "start_time": g.get("start_time"),
        "end_time": g.get("end_time"),
        "is_private": g.get("is_private"),

        "title": g.get("title"),
        "notes": g.get("notes"),

        "venue_name": v.get("name"),
        "venue_address": v.get("address_line1"),
        "venue_city": v.get("city"),
        "venue_state": v.get("state"),
    }

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

    Summary:
      - summary prefers gig['title'] then gig['event_title'], else "Gig"
      - description includes Lineup, Notes, and Venue details when available
    """
    import datetime as dt  # local to avoid circulars in some runners

    def _html_escape(s: str) -> str:
        return (
            str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    # --- Title / Summary (fix: include event_title fallback) ---
    title = (gig.get("title") or gig.get("event_title") or "Gig").strip()

    # --- Location (Venue | Address | City State) ---
    venue = (gig.get("venue_name") or "").strip()
    city = (gig.get("venue_city") or "").strip()
    state = (gig.get("venue_state") or "").strip()
    address = (gig.get("venue_address") or "").strip()
    city_state = " ".join([x for x in [city, state] if x]).strip()
    location_parts = [p for p in [venue, address, city_state] if p]
    location = " | ".join(location_parts)

    # --- Datetimes (roll end to next day if needed) ---
    event_date = str(gig["event_date"])  # 'YYYY-MM-DD'
    start = str(gig.get("start_time") or "00:00")
    end   = str(gig.get("end_time") or "00:00")
    y, m, d = [int(x) for x in event_date.split("-")]
    sh, sm = [int(x) for x in start.split(":")[:2]]
    eh, em = [int(x) for x in end.split(":")[:2]]
    starts_at = dt.datetime(y, m, d, sh, sm)
    ends_at   = dt.datetime(y, m, d, eh, em)
    if ends_at <= starts_at:
        ends_at += dt.timedelta(days=1)

    # --- Lineup block ---
    lineup_html = ""
    if gig.get("lineup_html"):
        lineup_html = f"""
        <h4>Lineup</h4>
        <div>{gig["lineup_html"]}</div>
        """
    else:
        items = []
        lineup_list = gig.get("lineup") or []
        if isinstance(lineup_list, list) and lineup_list:
            for item in lineup_list:
                if isinstance(item, dict):
                    stage = (item.get("stage_name") or item.get("name") or "").strip()
                    role = (item.get("role") or "").strip()
                    label = stage if stage else str(item)
                    if role:
                        label = f"{label} ({role})"
                    items.append(_html_escape(label))
                else:
                    items.append(_html_escape(str(item)))
        elif gig.get("lineup_text"):
            items = [x.strip() for x in str(gig["lineup_text"]).splitlines() if x.strip()]
        if items:
            li = "".join([f"<li>{x}</li>" for x in items])
            lineup_html = f"""
            <h4>Lineup</h4>
            <ul>{li}</ul>
            """

    # --- Notes block ---
    notes_html = ""
    if gig.get("notes"):
        notes_html = f"""
        <h4>Notes</h4>
        <div style="white-space:pre-wrap">{_html_escape(str(gig["notes"]))}</div>
        """

    # --- Venue block ---
    venue_lines = []
    if venue:
        venue_lines.append(_html_escape(venue))
    if address:
        venue_lines.append(_html_escape(address))
    if city_state:
        venue_lines.append(_html_escape(city_state))
    venue_block = "<br/>".join(venue_lines)
    venue_html = f"<h4>Venue</h4><div>{venue_block}</div>" if venue_block else ""

    # --- Footer (non-sensitive trace) ---
    footer_html = f"""
    <hr/>
    <div><b>Calendar:</b> {_html_escape(calendar_name)}<br/>
    <b>Gig ID:</b> {_html_escape(gig_id)}</div>
    """

    description_html = f"""
    <div>
      {lineup_html}
      {notes_html}
      {venue_html}
      {footer_html}
    </div>
    """

    return {
        "summary": title,
        "location": location,
        "description": description_html,
        "start": {"dateTime": _mk_rfc3339(starts_at), "timeZone": "America/New_York"},
        "end":   {"dateTime": _mk_rfc3339(ends_at),   "timeZone": "America/New_York"},
        "reminders": {"useDefault": True},
        "extendedProperties": {"private": {"gig_id": str(gig_id)}},
    }

def upsert_band_calendar_event(
    gig_id: str,
    sb=None,
    calendar_name: str = "Philly Rock and Soul",
    **kwargs,
):
    """
    Create or update a Calendar event for the given gig.

    Success:
      {"action": "created"|"updated", "eventId": "...", "calendarId": "...", "summary": "..."}

    Failure (no exception leak):
      {"error": "...", "stage": "auth|cal_get|fetch_gig|compose|search|insert|update|args",
       "calendarId": "..."}  # calendarId may be absent if failure is pre-resolve
    """
    from googleapiclient.errors import HttpError
    # Try to reuse the same lineup HTML builder used by player confirmations.
    # This does NOT change the email path; it only injects lineup_html into the gig dict for calendar use.
    def _inject_lineup_html(gig: dict, sb_client, gid: str) -> None:
        try:
            # Try common helper names from tools/send_player_confirms.py
            build_funcs = []
            try:
                from tools.send_player_confirms import build_lineup_html as _b
                build_funcs.append(_b)
            except Exception:
                pass
            try:
                from tools.send_player_confirms import render_lineup_html as _r
                build_funcs.append(_r)
            except Exception:
                pass

            if not build_funcs:
                return  # nothing to reuse; silently skip

            # Try a few likely signatures without breaking if they don't match
            for fn in build_funcs:
                for args in ((gid,), (sb_client, gid), (gig,), (sb_client, gid, gig)):
                    try:
                        html = fn(*args)
                        if html:
                            gig["lineup_html"] = html
                            return
                    except Exception:
                        continue
        except Exception:
            # Never let calendar posting fail just because lineup HTML couldn't be built
            pass

    # Basic arg check
    if not gig_id:
        msg = "gig_id is required"
        print("GCAL_UPSERT_ERR", {"stage": "args", "error": msg})
        return {"error": msg, "stage": "args"}

    # Auth / service
    try:
        service = _get_gcal_service()
    except Exception as e:
        print("GCAL_UPSERT_ERR", {"stage": "auth", "error": str(e)})
        return {"error": f"auth/build failure: {e}", "stage": "auth"}

    # Resolve calendar + check access
    calendar_id = None
    try:
        calendar_id = _resolve_calendar_id(calendar_name)
        if not calendar_id:
            raise RuntimeError(f"Calendar not found for name: {calendar_name}")

        # Probe permission using events.list (compatible with calendar.events scope)
        service.events().list(
            calendarId=calendar_id,
            maxResults=1,
            singleEvents=True
        ).execute()

    except HttpError as he:
        print("GCAL_UPSERT_ERR", {"stage": "cal_get", "error": str(he), "calendarId": calendar_id})
        return {"error": f"calendar access error: {he}", "stage": "cal_get", "calendarId": calendar_id}
    except Exception as e:
        print("GCAL_UPSERT_ERR", {"stage": "cal_get", "error": str(e), "calendarId": calendar_id})
        return {"error": f"calendar access unexpected: {e}", "stage": "cal_get", "calendarId": calendar_id}

    print(f"[CAL] Using calendar_name='{calendar_name}' (resolved ID: {calendar_id}) for gig_id={gig_id}")

    # Fetch gig row
    try:
        gig = _fetch_gig_for_calendar(sb, gig_id)
    except Exception as e:
        print("GCAL_UPSERT_ERR", {"stage": "fetch_gig", "error": str(e), "calendarId": calendar_id})
        return {"error": f"gig fetch failed: {e}", "stage": "fetch_gig", "calendarId": calendar_id}

    # Inject lineup_html from the same builder used by player confirmations (no effect on ICS code)
    _inject_lineup_html(gig, sb, gig_id)

    # Compose body
    try:
        body = _compose_event_body(gig, calendar_name, gig_id)
        print("CAL-UPsert", {
            "summary": body.get("summary"),
            "hasLineup": "Lineup" in str(body.get("description") or ""),
            "hasNotes": "Notes" in str(body.get("description") or "")
        })
    except Exception as e:
        print("GCAL_UPSERT_ERR", {"stage": "compose", "error": str(e), "calendarId": calendar_id})
        return {"error": f"compose failed: {e}", "stage": "compose", "calendarId": calendar_id}

    # === Deterministic find by private extended property (gig_id) ===
    try:
        search = service.events().list(
            calendarId=calendar_id,
            privateExtendedProperty=f"gig_id={gig_id}",
            maxResults=1,
            singleEvents=True,
            showDeleted=False,
        ).execute()
        items = (search or {}).get("items", []) or []
        found_event_id = items[0]["id"] if items else None
    except HttpError as he:
        print("GCAL_UPSERT_ERR", {"stage": "search", "error": str(he), "calendarId": calendar_id})
        return {"error": f"event search error: {he}", "stage": "search", "calendarId": calendar_id}
    except Exception as e:
        print("GCAL_UPSERT_ERR", {"stage": "search", "error": str(e), "calendarId": calendar_id})
        return {"error": f"event search unexpected: {e}", "stage": "search", "calendarId": calendar_id}

    # === Insert or Update (use update(), not patch, so summary/description are authoritative) ===
    try:
        if found_event_id:
            updated = service.events().update(
                calendarId=calendar_id,
                eventId=found_event_id,
                body=body,
            ).execute()
            res = {
                "action": "updated",
                "eventId": updated["id"],
                "calendarId": calendar_id,
                "summary": updated.get("summary"),
            }
            print("GCAL_UPSERT", res)
            return res
        else:
            created = service.events().insert(
                calendarId=calendar_id,
                body=body,
            ).execute()
            res = {
                "action": "created",
                "eventId": created["id"],
                "calendarId": calendar_id,
                "summary": created.get("summary"),
            }
            print("GCAL_UPSERT", res)
            return res
    except HttpError as he:
        stage = "update" if found_event_id else "insert"
        print("GCAL_UPSERT_ERR", {"stage": stage, "error": str(he), "calendarId": calendar_id})
        return {"error": f"event upsert error: {he}", "stage": stage, "calendarId": calendar_id}
    except Exception as e:
        stage = "update" if found_event_id else "insert"
        print("GCAL_UPSERT_ERR", {"stage": stage, "error": str(e), "calendarId": calendar_id})
        return {"error": f"event upsert unexpected: {e}", "stage": stage, "calendarId": calendar_id}

# --- Diagnostics helpers (no secrets leaked) ---
def debug_auth_config() -> dict:
    """
    Report whether oauth secrets are present and correctly shaped (names only).
    Never returns secret values.
    """
    import streamlit as st
    out = {"has_google_oauth": False, "missing_keys": [], "present_keys": []}
    if "google_oauth" not in st.secrets:
        return out
    oauth = st.secrets["google_oauth"]
    need = {"client_id", "client_secret", "refresh_token"}
    present = {k for k in need if oauth.get(k)}
    out["has_google_oauth"] = present == need
    out["present_keys"] = sorted(list(present))
    out["missing_keys"] = sorted(list(need - present))
    return out

def debug_calendar_access(calendar_name_or_id: str) -> dict:
    """
    Diagnose access using only endpoints allowed by calendar.events scope.
    Returns:
      {
        "target_calendar_id": "<resolved id>",
        "events_list_ok": True|False,
        "error": "...",   # present if events_list_ok is False
      }
    """
    from googleapiclient.errors import HttpError

    service = _get_gcal_service()
    target_id = _resolve_calendar_id(calendar_name_or_id)

    try:
        service.events().list(calendarId=target_id, maxResults=1, singleEvents=True).execute()
        return {
            "target_calendar_id": target_id,
            "events_list_ok": True,
        }
    except HttpError as he:
        return {
            "target_calendar_id": target_id,
            "events_list_ok": False,
            "error": f"{he}",
        }
    except Exception as e:
        return {
            "target_calendar_id": target_id,
            "events_list_ok": False,
            "error": f"{e}",
        }

__all__ = ["make_ics_bytes", "upsert_band_calendar_event", "debug_auth_config", "debug_calendar_access"]

