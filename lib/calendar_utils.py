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
    Includes title/notes and also venue_id/sound_tech_id needed for lineup/details.
    """
    if not sb:
        raise RuntimeError("Supabase client (sb) is required to fetch gig")

    # Join venues through the foreign key venue_id -> venues.id
    sel = (
        "id,event_date,start_time,end_time,is_private,"
        "title,notes,venue_id,sound_tech_id,"
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

    return {
        "id": g.get("id"),
        "event_date": g.get("event_date"),
        "start_time": g.get("start_time"),
        "end_time": g.get("end_time"),
        "is_private": g.get("is_private"),
        "title": g.get("title"),
        "notes": g.get("notes"),
        "venue_id": g.get("venue_id"),
        "sound_tech_id": g.get("sound_tech_id"),
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

    Rules:
      - summary uses gig['title'] (fallback "Gig")
      - if gig provides lineup_html / details_html / notes_html, use them verbatim (no re-wrapping)
      - otherwise, build minimal fallback sections
      - avoid duplicating Notes/Venue that are already provided
    """
    import datetime as dt

    def _html_escape(s: str) -> str:
        return (
            str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    title = (gig.get("title") or "Gig").strip()

    # Location: "Venue | Address | City State"
    venue = (gig.get("venue_name") or "").strip()
    city = (gig.get("venue_city") or "").strip()
    state = (gig.get("venue_state") or "").strip()
    address = (gig.get("venue_address") or "").strip()
    city_state = " ".join([x for x in [city, state] if x]).strip()
    location_parts = [p for p in [venue, address, city_state] if p]
    location = " | ".join(location_parts)

    # Datetimes (roll end to next day if needed)
    event_date = str(gig["event_date"])
    start = str(gig.get("start_time") or "00:00")
    end   = str(gig.get("end_time") or "00:00")
    y, m, d = [int(x) for x in event_date.split("-")]
    sh, sm = [int(x) for x in start.split(":")[:2]]
    eh, em = [int(x) for x in end.split(":")[:2]]
    starts_at = dt.datetime(y, m, d, sh, sm)
    ends_at   = dt.datetime(y, m, d, eh, em)
    if ends_at <= starts_at:
        ends_at += dt.timedelta(days=1)

    # --------------- description blocks ---------------
    parts: list[str] = []

    # Prefer prebuilt HTML blocks (identical to email path)
    if gig.get("lineup_html"):
        parts.append(str(gig["lineup_html"]))  # use as-is (no extra <h4>)
    else:
        # Minimal fallback lineup from any plain data present
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
            parts.append(f"<h4>Lineup</h4><ul>{li}</ul>")

    if gig.get("notes_html"):
        parts.append(str(gig["notes_html"]))    # use as-is
    elif gig.get("notes"):
        parts.append(f"<h4>Notes</h4><div style='white-space:pre-wrap'>{_html_escape(gig['notes'])}</div>")

    if gig.get("details_html"):
        parts.append(str(gig["details_html"]))  # use as-is
    else:
        # Minimal Venue block only if details_html not provided
        venue_lines = []
        if venue:
            venue_lines.append(_html_escape(venue))
        if address:
            venue_lines.append(_html_escape(address))
        if city_state:
            venue_lines.append(_html_escape(city_state))
        if venue_lines:
            parts.append(f"<h4>Venue</h4><div>{'<br/>'.join(venue_lines)}</div>")

    # Footer
    parts.append(f"<hr/><div><b>Calendar:</b> {_html_escape(calendar_name)}<br/><b>Gig ID:</b> {_html_escape(gig_id)}</div>")

    description_html = "\n\n".join(parts)

    return {
        "summary": title,
        "location": location,
        "description": description_html,
        "start": {"dateTime": _mk_rfc3339(starts_at), "timeZone": "America/New_York"},
        "end":   {"dateTime": _mk_rfc3339(ends_at),   "timeZone": "America/New_York"},
        "reminders": {"useDefault": True},
        "extendedProperties": {"private": {"gig_id": str(gig_id)}},
    }

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
    
    # ---- TEMP DEBUG: secrets visibility ---
    import streamlit as st
    st.warning(f"DEBUG gcal_ids = {dict(st.secrets.get('gcal_ids', {}))}")
    st.warning(f"DEBUG TEST_KEY = {st.secrets.get('TEST_KEY')}")
    
    """
    Create or update a Calendar event for the given gig.

    Success:
      {"action": "created"|"updated", "eventId": "...", "calendarId": "...", "summary": "..."}

    Failure:
      {"error": "...", "stage": "auth|cal_get|fetch_gig|compose|search|insert|update|args",
       "calendarId": "..."}
    """
    from googleapiclient.errors import HttpError

    # Helper: reuse player-confirm builders; adds lineup_html/details_html/notes_html if available
    def _inject_lineup_and_details_html(gig: dict, sb_client, gid: str) -> None:
        try:
            from tools.send_player_confirms import (
                _fetch_musicians_map,
                _stage_pref, _nz, _fmt_time12, _fmt_addr, _html_escape
            )
            try:
                from tools.send_player_confirms import _fetch_soundtech_name
            except Exception:
                _fetch_soundtech_name = None

            gm_rows = _gig_musicians_rows_from_sb(sb_client, gid)
            ordered_ids, roles_by_mid = [], {}
            for r in gm_rows:
                mid = r.get("musician_id")
                if mid is None:
                    continue
                smid = str(mid)
                if smid not in roles_by_mid:
                    ordered_ids.append(smid)
                roles_by_mid[smid] = _nz(r.get("role"))
            mus_map = _fetch_musicians_map(ordered_ids)
            other_players = []
            for oid in ordered_ids:
                orow = mus_map.get(oid) or {}
                name = _stage_pref(orow)
                role = roles_by_mid.get(oid, "")
                other_players.append(f"{name}{f' ({role})' if role else ''}")

            lineup_html = "<h4>Lineup</h4><ul>"
            if other_players:
                lineup_html += f"<li><b>Other confirmed players:</b> {', '.join(other_players)}</li>"
            if _fetch_soundtech_name and gig.get("sound_tech_id"):
                sname = _fetch_soundtech_name(gig.get("sound_tech_id"))
                if sname:
                    lineup_html += f"<li><b>Confirmed sound tech:</b> {sname}</li>"
            lineup_html += "</ul>"

            venue = {}
            try:
                if sb_client and gig.get("venue_id"):
                    vresp = sb_client.table("venues").select("*").eq("id", gig.get("venue_id")).limit(1).execute()
                    vrows = getattr(vresp, "data", None) or []
                    venue = vrows[0] if vrows else {}
            except Exception:
                venue = {}
            venue_name = _nz((venue or {}).get("name"))
            venue_addr = _fmt_addr(venue or {})
            start_hhmm = _fmt_time12(gig.get("start_time"))
            end_hhmm = _fmt_time12(gig.get("end_time"))

            details_html = f"""
            <h4>Event Details</h4>
            <table border="1" cellpadding="6" cellspacing="0">
              <tr><th align="left">Date</th><td>{_nz(gig.get("event_date"))}</td></tr>
              <tr><th align="left">Time</th><td>{start_hhmm} – {end_hhmm}</td></tr>
              <tr><th align="left">Venue</th><td>{_html_escape(venue_name)}</td></tr>
              <tr><th align="left">Address</th><td>{_html_escape(venue_addr)}</td></tr>
            </table>
            """

            notes_raw = gig.get("notes") or gig.get("closeout_notes")
            notes_html = ""
            if notes_raw and str(notes_raw).strip():
                notes_html = f"""
                <h4>Notes</h4>
                <div style="white-space:pre-wrap">{_html_escape(notes_raw)}</div>
                """

            gig["lineup_html"] = lineup_html
            gig["details_html"] = details_html
            gig["notes_html"] = notes_html
        except Exception:
            # Fallback: minimal confirmed-players list
            try:
                if not sb_client:
                    return
                q = (
                    sb_client.table("gig_players")
                    .select("is_confirmed, role, musicians:musician_id(stage_name,name)")
                    .eq("gig_id", gid)
                    .eq("is_confirmed", True)
                    .order("role", desc=False)
                    .execute()
                )
                rows = getattr(q, "data", None) or []
                items = []
                for r in rows:
                    m = r.get("musicians") or {}
                    stage = (m.get("stage_name") or m.get("name") or "").strip()
                    role = (r.get("role") or "").strip()
                    if stage and role:
                        items.append(f"<li>{stage} ({role})</li>")
                    elif stage:
                        items.append(f"<li>{stage}</li>")
                if items:
                    gig["lineup_html"] = "<h4>Lineup</h4><ul>" + "".join(items) + "</ul>"
            except Exception:
                pass

    # Args
    if not gig_id:
        msg = "gig_id is required"
        print("GCAL_UPSERT_ERR", {"stage": "args", "error": msg})
        return {"error": msg, "stage": "args"}

    # Auth
    try:
        service = _get_gcal_service()
    except Exception as e:
        print("GCAL_UPSERT_ERR", {"stage": "auth", "error": str(e)})
        return {"error": f"auth/build failure: {e}", "stage": "auth"}

    # Resolve calendar + probe
    calendar_id = None
    try:
        calendar_id = _resolve_calendar_id(calendar_name)
        if not calendar_id:
            raise RuntimeError(f"Calendar not found for name: {calendar_name}")
        service.events().list(calendarId=calendar_id, maxResults=1, singleEvents=True).execute()
    except HttpError as he:
        print("GCAL_UPSERT_ERR", {"stage": "cal_get", "error": str(he), "calendarId": calendar_id})
        return {"error": f"calendar access error: {he}", "stage": "cal_get", "calendarId": calendar_id}
    except Exception as e:
        print("GCAL_UPSERT_ERR", {"stage": "cal_get", "error": str(e), "calendarId": calendar_id})
        return {"error": f"calendar access unexpected: {e}", "stage": "cal_get", "calendarId": calendar_id}

    # Fetch gig
    try:
        gig = _fetch_gig_for_calendar(sb, gig_id)
    except Exception as e:
        print("GCAL_UPSERT_ERR", {"stage": "fetch_gig", "error": str(e), "calendarId": calendar_id})
        return {"error": f"gig fetch failed: {e}", "stage": "fetch_gig", "calendarId": calendar_id}

    # Enrich with lineup/details/notes (uses player-confirm logic)
    _inject_lineup_and_details_html(gig, sb, gig_id)

    # Compose (now uses prebuilt blocks as-is; no later appends)
    try:
        body = _compose_event_body(gig, calendar_name, gig_id)
    except Exception as e:
        print("GCAL_UPSERT_ERR", {"stage": "compose", "error": str(e), "calendarId": calendar_id})
        return {"error": f"compose failed: {e}", "stage": "compose", "calendarId": calendar_id}

    # Upsert deterministically by gig_id
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

    try:
        if found_event_id:
            updated = service.events().update(calendarId=calendar_id, eventId=found_event_id, body=body).execute()
            res = {"action": "updated", "eventId": updated["id"], "calendarId": calendar_id, "summary": updated.get("summary")}
            print("GCAL_UPSERT", res)
            return res
        else:
            created = service.events().insert(calendarId=calendar_id, body=body).execute()
            res = {"action": "created", "eventId": created["id"], "calendarId": calendar_id, "summary": created.get("summary")}
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


    print(f"[CAL] Using calendar_name='{calendar_name}' (resolved ID: {calendar_id}) for gig_id={gig_id}")

    # ------------------ fetch gig ------------------
    try:
        gig = _fetch_gig_for_calendar(sb, gig_id)
    except Exception as e:
        print("GCAL_UPSERT_ERR", {"stage": "fetch_gig", "error": str(e), "calendarId": calendar_id})
        return {"error": f"gig fetch failed: {e}", "stage": "fetch_gig", "calendarId": calendar_id}

    # Inject lineup/details/notes HTML (reused from player-confirm logic)
    _inject_lineup_and_details_html(gig, sb, gig_id)

    # ------------------ compose body ------------------
    try:
        body = _compose_event_body(gig, calendar_name, gig_id)
        # If details_html exists and your composer doesn't include it already, append it.
        if gig.get("details_html"):
            desc = (body.get("description") or "").strip()
            body["description"] = (desc + "\n\n" + gig["details_html"]).strip() if desc else gig["details_html"]
        # Optional: ensure Notes HTML makes it through if your composer only uses plain notes
        if gig.get("notes_html"):
            desc = (body.get("description") or "").strip()
            body["description"] = (desc + "\n\n" + gig["notes_html"]).strip() if desc else gig["notes_html"]
    except Exception as e:
        print("GCAL_UPSERT_ERR", {"stage": "compose", "error": str(e), "calendarId": calendar_id})
        return {"error": f"compose failed: {e}", "stage": "compose", "calendarId": calendar_id}

    # ------------------ deterministic find by gig_id ------------------
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

    # ------------------ update or insert (use update so summary/description are authoritative) ------------------
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

