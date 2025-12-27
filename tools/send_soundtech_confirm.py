# tools/send_soundtech_confirm.py
from __future__ import annotations
import os
import uuid as uuid_mod
import datetime as dt
from typing import Dict, Any, Optional

import pytz
from supabase import create_client, Client

from lib.email_utils import gmail_send, build_html_table
from lib.calendar_utils import make_ics_bytes


# -----------------------------
# Secrets / config helpers
# -----------------------------
def _get_secret(name: str, default: Optional[str] = None):
    # Prefer Streamlit secrets when running inside the app
    try:
        import streamlit as st
        if hasattr(st, "secrets") and name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    # Fallback to environment variables (e.g., GitHub Actions)
    return os.environ.get(name, default)


def _is_dry_run() -> bool:
    val = _get_secret("SOUNDT_EMAIL_DRY_RUN", "0")
    return str(val).lower() in {"1", "true", "yes", "on"}


TZ = _get_secret("APP_TZ", "America/New_York")
INCLUDE_ICS = str(_get_secret("INCLUDE_ICS", "true")).lower() in {"1", "true", "yes"}

SUPABASE_URL = _get_secret("SUPABASE_URL")
SUPABASE_KEY = (
    _get_secret("SUPABASE_SERVICE_ROLE")
    or _get_secret("SUPABASE_KEY")
    or _get_secret("SUPABASE_ANON_KEY")
)
CC_RAY = _get_secret("CC_RAY", "ray@lutinemanagement.com")
FROM_NAME = _get_secret("BAND_FROM_NAME", "PRS Scheduling")
FROM_EMAIL = _get_secret("BAND_FROM_EMAIL", "no-reply@prs.local")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError(
        "Missing Supabase credentials: set SUPABASE_URL and one of "
        "SUPABASE_SERVICE_ROLE / SUPABASE_KEY / SUPABASE_ANON_KEY."
    )


# -----------------------------
# Supabase clients
# -----------------------------
def _sb() -> Client:
    """User/session-scoped client (respects RLS in-app)."""
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    # If running inside Streamlit, attach the logged-in user session so RLS allows the row
    try:
        import streamlit as st
        at = st.session_state.get("sb_access_token")
        rt = st.session_state.get("sb_refresh_token")
        if at and rt:
            sb.auth.set_session(access_token=at, refresh_token=rt)
    except Exception:
        # Not running in Streamlit, or no session — fine (CLI/Actions path)
        pass
    return sb


def _sb_admin() -> Client:
    """Service-role client for audit writes (bypass RLS)."""
    sr = _get_secret("SUPABASE_SERVICE_ROLE") or SUPABASE_KEY
    return create_client(SUPABASE_URL, sr)


# -----------------------------
# Utilities
# -----------------------------
def _parse_time_flex(t: Optional[str]) -> dt.time:
    """Accept 'HH:MM' or 'HH:MM:SS'; default to 17:00 if missing/invalid."""
    if not t:
        return dt.time(17, 0)
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return dt.datetime.strptime(t, fmt).time()
        except ValueError:
            continue
    return dt.time(17, 0)


def _localize(event_date_str: str, time_str: Optional[str]) -> tuple[dt.datetime, dt.datetime]:
    tz = pytz.timezone(TZ)
    day = dt.datetime.strptime(event_date_str, "%Y-%m-%d")
    st_time = _parse_time_flex(time_str)
    starts = tz.localize(dt.datetime.combine(day.date(), st_time))
    ends = starts + dt.timedelta(hours=4)
    return starts, ends


# -----------------------------
# Data fetch + audit
# -----------------------------
def _fetch_event_and_tech(sb: Client, gig_id: str) -> Dict[str, Any]:
    # Fetch gig safely (avoid .single() so 0 rows doesn't raise PGRST116)
    ev_res = (
        sb.table("gigs")
        .select(
            "id, title, event_date, start_time, end_time, "
            "sound_provided, sound_fee, sound_tech_id, venue_id, notes, "
            "sound_by_venue_name, sound_by_venue_phone"
        )
        .eq("id", gig_id)
        .limit(1)
        .execute()
    )

    ev_rows = (ev_res.data or []) if ev_res else []
    if not ev_rows:
        # Most common causes: wrong gig_id or RLS preventing visibility
        raise ValueError(f"Gig {gig_id} not found or not accessible (RLS)")

    ev = ev_rows[0]
    # Add venue fields for ICS LOCATION (safe enrichment; does not affect Google)
    venue_id = ev.get("venue_id")
    if venue_id:
        v_res = (
            sb.table("venues")
            .select("name, address_line1, city, state")
            .eq("id", venue_id)
            .limit(1)
            .execute()
        )
        v_rows = (v_res.data or []) if v_res else []
        if v_rows:
            v = v_rows[0]
            # Map into fields the ICS mutate block already checks
            if v.get("name"):
                ev["venue_name"] = v["name"]
            if v.get("address_line1"):
                ev["venue_address_line1"] = v["address_line1"]
            if v.get("city"):
                ev["city"] = v["city"]
            if v.get("state"):
                ev["state"] = v["state"]

    # Ensure there is a sound tech assigned
    stid = ev.get("sound_tech_id")
    if not stid:
        raise ValueError("No sound tech is assigned to this gig.")

    # Fetch sound tech safely (matches your schema)
    tech_res = (
        sb.table("sound_techs")
        .select("id, display_name, first_name, last_name, company, email")
        .eq("id", stid)
        .limit(1)
        .execute()
    )
    tech_rows = (tech_res.data or []) if tech_res else []
    if not tech_rows:
        raise ValueError("Assigned sound tech not found or not accessible (RLS).")

    tech = tech_rows[0]
    if not (tech.get("email") and str(tech["email"]).strip()):
        raise ValueError("Assigned sound tech has no email on file.")

    return {"event": ev, "tech": tech}


def _insert_email_audit(
    *, token: str, gig_id: str, recipient_email: str, kind: str, status: str
):
    # Write with service-role so RLS never blocks audit
    admin = _sb_admin()
    admin.table("email_audit").insert(
        {
            "token": token,
            "gig_id": gig_id,
            "event_id": None,
            "recipient_email": recipient_email,
            "kind": kind,
            "status": status,
            "ts": dt.datetime.utcnow().isoformat(),
        }
    ).execute()


# -----------------------------
# Main sender
# -----------------------------
def send_soundtech_confirm(gig_id: str) -> None:
    sb = _sb()
    payload = _fetch_event_and_tech(sb, gig_id)
    ev, tech = payload["event"], payload["tech"]

    starts_at, ends_at = _localize(ev["event_date"], ev.get("start_time"))

    # Optional fee line when venue does NOT provide sound and a fee exists
    fee_str = None
    if not ev.get("sound_provided") and (ev.get("sound_fee") is not None):
        try:
            fee_str = f"${float(ev['sound_fee']):,.2f}"
        except Exception:
            fee_str = str(ev["sound_fee"])

    token = uuid_mod.uuid4().hex
    title = ev.get("title") or "Gig"

    # --- 12-hour time helper (safe for time|datetime|string) ---
    from datetime import datetime, time as _time
    def _fmt_time12(t) -> str:
        """Return 12-hour time like '7:30 PM' from time|datetime|string; fallback to raw."""
        try:
            if isinstance(t, (_time, datetime)):
                return t.strftime("%I:%M %p").lstrip("0")
            s = str(t or "").strip()
            for fmt in ("%H:%M:%S", "%H:%M"):
                try:
                    return datetime.strptime(s, fmt).strftime("%I:%M %p").lstrip("0")
                except ValueError:
                    pass
        except Exception:
            pass
        return str(t or "")

    # Build the small info table for the email
    rows = [
        {
            "Gig": title,
            "Date": ev["event_date"],
            "Call Time": _fmt_time12(ev.get("start_time")),
            "Fee (if applicable)": fee_str or "—",
        }
    ]
    html_table = build_html_table(rows)

    # Greeting using your schema
    name_parts = [(tech.get("first_name") or "").strip(), (tech.get("last_name") or "").strip()]
    name_join = " ".join(p for p in name_parts if p)
    greet_name = (tech.get("display_name") or name_join or tech.get("company") or "there")

    # Send confirmation back to our band inbox (not to the tech)
    mailto = (
        f"mailto:{FROM_EMAIL}?subject="
        f"Confirm%20received%20-%20{title}%20({ev['event_date']})%20[{token}]&body=Reply%20to%20confirm.%20Token%3A%20{token}"
    )

    html = f"""
    <p>Hi {greet_name},</p>
    <p>You're booked as <b>Sound Tech</b> for the Philly Rock and Soul gig below.</p>
    {html_table}
    <p>
      Please <a href="{mailto}"><b>confirm received</b></a>.
      This helps Ray to not lose his mind.
    </p>
    <p>— {FROM_NAME}</p>
    """

    # Nice touch: include 12-hr time in subject
    subject = f"[Sound Tech] {title} — {ev['event_date']} @ {_fmt_time12(ev.get('start_time'))}"

    # Attach ICS if enabled
    atts = []
    if INCLUDE_ICS:
        ics_bytes = make_ics_bytes(
            uid=token + "@prs",
            title=f"{title} — Sound Tech",
            starts_at=starts_at,
            ends_at=ends_at,
            location="",  # add venue address later if you want
            description="Sound tech call. Brought to you by PRS Scheduling.",
        )

    # Upgrade to a real invite: add METHOD + ORGANIZER/ATTENDEE + Outlook-safe fields
    try:
        import re
        from datetime import datetime, timezone

        _txt = ics_bytes.decode("utf-8", "ignore")

        # Normalize to LF internally; we'll restore CRLF at the end
        t = _txt.replace("\r\n", "\n").replace("\r", "\n")

        # ------------------------------------------------------------------
        # Helpers: RFC5545 escaping, HTML escaping, folding, VEVENT insert
        # ------------------------------------------------------------------
        def _escape_ics_text(val: str) -> str:
            """
            RFC5545 TEXT escape:
              - backslash, semicolon, comma
              - CR/LF as literal '\n'
            """
            if not val:
                return ""
            s = str(val)
            s = s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            s = s.replace("\r\n", "\n").replace("\r", "\n")
            s = s.replace("\n", "\\n")
            return s

        def _html_escape(val: str) -> str:
            s = str(val or "")
            return (
                s.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )

        def _fold_ics_line(line: str) -> str:
            """
            Fold a long ICS line at ~73 characters (continuation lines start with a space).
            """
            out = []
            cur = line
            while len(cur) > 73:
                out.append(cur[:73])
                cur = " " + cur[73:]
            out.append(cur)
            return "\n".join(out)

        def _ensure_in_vevent(s: str, line: str, key_present: str) -> str:
            """
            Insert a (possibly folded) property just before END:VEVENT if not already present.
            """
            if key_present in s:
                return s
            return s.replace(
                "END:VEVENT",
                f"{_fold_ics_line(line)}\nEND:VEVENT",
                1,
            )

        def _remove_prop_block(s: str, prefix: str) -> str:
            """
            Remove a property and any folded continuation lines:
              PREFIX:...
               continued...
            """
            pattern = rf"^{prefix}[^\n]*\n(?:[ \t].*\n)*"
            return re.sub(pattern, "", s, flags=re.M)

        # ------------------------------------------------------------------
        # METHOD:REQUEST at VCALENDAR level (keep existing if present)
        # ------------------------------------------------------------------
        if "METHOD:" not in t:
            t = t.replace(
                "BEGIN:VCALENDAR\nVERSION:2.0\n",
                "BEGIN:VCALENDAR\nVERSION:2.0\nMETHOD:REQUEST\n",
                1,
            )

        # ------------------------------------------------------------------
        # UID + DTSTAMP inside VEVENT
        # ------------------------------------------------------------------
        if "UID:" not in t:
            uid = f"{uuid_mod.uuid4()}@prs"
            t = t.replace("BEGIN:VEVENT\n", f"BEGIN:VEVENT\nUID:{uid}\n", 1)

        if "DTSTAMP:" not in t:
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            t = t.replace("BEGIN:VEVENT\n", f"BEGIN:VEVENT\nDTSTAMP:{ts}\n", 1)

        # ------------------------------------------------------------------
        # VTIMEZONE: keep existing; add America/New_York if missing
        # (we do NOT touch DTSTART/DTEND with 'Z' so we preserve UTC times)
        # ------------------------------------------------------------------
        if "TZID:America/New_York" not in t:
            tz_block = (
                "BEGIN:VTIMEZONE\n"
                "TZID:America/New_York\n"
                "X-LIC-LOCATION:America/New_York\n"
                "BEGIN:DAYLIGHT\n"
                "TZOFFSETFROM:-0500\n"
                "TZOFFSETTO:-0400\n"
                "TZNAME:EDT\n"
                "DTSTART:19700308T020000\n"
                "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU\n"
                "END:DAYLIGHT\n"
                "BEGIN:STANDARD\n"
                "TZOFFSETFROM:-0400\n"
                "TZOFFSETTO:-0500\n"
                "TZNAME:EST\n"
                "DTSTART:19701101T020000\n"
                "RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU\n"
                "END:STANDARD\n"
                "END:VTIMEZONE\n"
            )
            t = t.replace("METHOD:REQUEST\n", f"METHOD:REQUEST\n{tz_block}", 1)

        # ------------------------------------------------------------------
        # ORGANIZER / ATTENDEE (explicit MAILTO + CN/ROLE)
        # ------------------------------------------------------------------
        cn_org = "Philly Rock & Soul"
        org_line = f"ORGANIZER;CN={cn_org}:MAILTO:{FROM_EMAIL}"

        tech_name = (
            tech.get("display_name")
            or tech.get("name")
            or tech.get("full_name")
            or tech.get("stage_name")
            or tech.get("email")
            or "Sound Tech"
        )
        att_line = (
            f"ATTENDEE;CN={tech_name};ROLE=REQ-PARTICIPANT:"
            f"MAILTO:{tech.get('email')}"
        )

        t = _ensure_in_vevent(t, org_line, "ORGANIZER:")
        t = _ensure_in_vevent(t, att_line, "ATTENDEE:")

        # LOCATION (if we have it) and rich DESCRIPTION + X-ALT-DESC

        # --- Build a human-readable location string from the event dict ---
        # We flatten venue fields onto `ev` in _fetch_event_and_tech:
        #   ev["venue_name"]  <- venues.name
        #   ev["city"]        <- venues.city
        #   ev["state"]       <- venues.state
        # Plus a manual fallback from gigs.sound_by_venue_name.
        venue_name = ev.get("venue_name") or ev.get("sound_by_venue_name")
        city = ev.get("city")
        state = ev.get("state")


        loc_parts = []
        if venue_name:
            loc_parts.append(str(venue_name).strip())

        city_state = ", ".join(
            part for part in [city, state] if part
        )
        if city_state:
            loc_parts.append(city_state)

        loc_str = " | ".join(loc_parts)

        # Only modify LOCATION if we actually have a non-empty value
        if loc_str:
            # Collapse weird whitespace
            loc_clean = " ".join(str(loc_str).split())

            # RFC 5545 escaping for text values
            loc_escaped = (
                loc_clean
                .replace("\\", "\\\\")
                .replace(",", r"\,")
                .replace(";", r"\;")
                .replace("\n", " ")
                .replace("\r", " ")
            )

            loc_line = f"LOCATION:{loc_escaped}"

            # 🔑 Use your existing helpers here
            t = _remove_prop_block(t, "LOCATION:")
            t = _ensure_in_vevent(t, loc_line, "\nLOCATION:")
        # If loc_str is empty, do nothing to LOCATION.



        # DESCRIPTION: top line + gig info + optional notes + optional sound fee
        top_line = "Sound tech call (v2). Brought to you by PRS Scheduling."
        desc_lines = [top_line, ""]

        title = ev.get("title")
        if title:
            desc_lines.append(f"Gig: {title}")

        event_date = ev.get("event_date")
        if event_date:
            desc_lines.append(f"Event date: {event_date}")

        start_time = ev.get("start_time")
        end_time = ev.get("end_time")
        if start_time or end_time:
            desc_lines.append(
                "Start and end times: "
                f"{_fmt_time12(start_time) if start_time else ''} – "
                f"{_fmt_time12(end_time) if end_time else ''}"
            )

        notes_text = ev.get("notes") or ev.get("sound_notes")
        if notes_text:
            desc_lines.append("")
            desc_lines.append(str(notes_text))

        if fee_str:
            desc_lines.append("")
            desc_lines.append(f"Sound fee: {fee_str}")

        # Filter out any trailing empty elements
        while desc_lines and desc_lines[-1] == "":
            desc_lines.pop()

        description_text = "\n".join(desc_lines) if desc_lines else top_line

        # HTML equivalent for X-ALT-DESC
        html_parts = [f"<p>{_html_escape(top_line)}</p>"]
        if title:
            html_parts.append(f"<p>Gig: {_html_escape(title)}</p>")
        if event_date:
            html_parts.append(f"<p>Event date: {_html_escape(event_date)}</p>")
        if start_time or end_time:
            html_parts.append(
                "<p>Start and end times: "
                f"{_html_escape(_fmt_time12(start_time) if start_time else '')} – "
                f"{_html_escape(_fmt_time12(end_time) if end_time else '')}"
                "</p>"
            )
        if notes_text:
            html_parts.append(
                f"<p style='white-space:pre-wrap'>"
                f"{_html_escape(notes_text)}</p>"
            )
        if fee_str:
            html_parts.append(
                f"<p>Sound fee: {_html_escape(fee_str)}</p>"
            )

        html_text = "".join(html_parts)

        # Strip any existing DESCRIPTION / X-ALT-DESC (including folded)
        t = _remove_prop_block(t, "DESCRIPTION")
        t = _remove_prop_block(t, "X-ALT-DESC")

        # Insert DESCRIPTION
        if description_text:
            desc_line = f"DESCRIPTION:{_escape_ics_text(description_text)}"
            t = _ensure_in_vevent(t, desc_line, "DESCRIPTION:")

        # Insert X-ALT-DESC HTML
        if html_text:
            alt_line = (
                "X-ALT-DESC;FMTTYPE=text/html:"
                f"{_escape_ics_text(html_text)}"
            )
            t = _ensure_in_vevent(
                t, alt_line, "X-ALT-DESC;FMTTYPE=text/html"
            )

        # ------------------------------------------------------------------
        # Outlook niceties (status + sequence)
        # ------------------------------------------------------------------
        t = _ensure_in_vevent(t, "STATUS:CONFIRMED", "STATUS:")
        t = _ensure_in_vevent(t, "SEQUENCE:0", "SEQUENCE:")

        # ------------------------------------------------------------------
        # Restore CRLF for Outlook strictness
        # ------------------------------------------------------------------
        t = t.replace("\n", "\r\n")

        ics_bytes = t.encode("utf-8")

    except Exception:
        # If anything odd happens, fall back to original bytes
        pass

    atts.append(
        {
            "filename": f"{title}-{ev['event_date']}.ics",
            "mime": "text/calendar; method=REQUEST; charset=UTF-8",
            "content": ics_bytes,
        }
    )

    # ---- SEND + AUDIT with strict checks ----
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
        print("GMAIL_DEBUG(soundtech)", debug_gmail)

        # ---- SEND ----
        if _is_dry_run():
            result = True  # pretend success in diagnostics
        else:
            result = gmail_send(
                subject,
                tech["email"],
                html,
                cc=[CC_RAY],
                attachments=atts,
            )

        if not result:
            raise RuntimeError("gmail_send returned a non-success value (None/False)")

        _insert_email_audit(
            token=token,
            gig_id=ev["id"],
            recipient_email=tech["email"],
            kind="soundtech_confirm",
            status=("dry-run" if _is_dry_run() else "sent"),
        )
        print(f"[soundtech_confirm] {'DRY-RUN' if _is_dry_run() else 'SENT'} token={token} to={tech['email']} subject={subject}")

    except Exception as e:
        _insert_email_audit(
            token=token,
            gig_id=ev["id"],
            recipient_email=tech["email"],
            kind="soundtech_confirm",
            status=f"error: {e}",
        )
        print(f"[soundtech_confirm] ERROR token={token} to={tech.get('email')} err={e}")
        raise



# -----------------------------
# Auto T-7 sender (for scheduler)
# -----------------------------
def run_auto_t7(today: Optional[dt.date] = None) -> None:
    """Send confirmations for gigs that occur in exactly 7 days and have a sound tech assigned."""
    sb = _sb()
    if today is None:
        today = dt.date.today()
    target = today + dt.timedelta(days=7)

    gigs = (
        sb.table("gigs")
        .select("id, event_date, sound_tech_id")
        .eq("event_date", target.isoformat())
        .not_.is_("sound_tech_id", None)
        .execute()
    ).data or []

    for g in gigs:
        gid = g.get("id")
        if not gid:
            continue
        try:
            send_soundtech_confirm(str(gid))
        except Exception as e:
            # Already audited within send_soundtech_confirm; keep console note for Actions logs
            print(f"⚠️ Failed T-7 send for gig {gid}: {e}")


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Send sound tech confirmation email(s)")
    p.add_argument("gig_id", nargs="?", help="Gig ID (UUID) for single send")
    p.add_argument("--auto_t7", action="store_true", help="Send for gigs happening in 7 days")
    args = p.parse_args()

    if args.auto_t7:
        run_auto_t7()
    elif args.gig_id:
        send_soundtech_confirm(args.gig_id)
    else:
        p.print_help()

