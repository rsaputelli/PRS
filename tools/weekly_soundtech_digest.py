# tools/weekly_soundtech_digest.py
from __future__ import annotations
import os
import uuid
import datetime as dt
from collections import defaultdict
from typing import Optional, List, Dict, Any

from zoneinfo import ZoneInfo
from supabase import create_client

from lib.email_utils import gmail_send, build_html_table
from lib.calendar_utils import make_ics_bytes

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

TZ = _get_secret("APP_TZ", "America/New_York")
INCLUDE_ICS = str(_get_secret("INCLUDE_ICS", "true")).lower() in {"1", "true", "yes"}

SUPABASE_URL = _get_secret("SUPABASE_URL")
SUPABASE_KEY = (
    _get_secret("SUPABASE_SERVICE_ROLE")
    or _get_secret("SUPABASE_SERVICE_KEY")
    or _get_secret("SUPABASE_KEY")
    or _get_secret("SUPABASE_ANON_KEY")
)
CC_RAY = _get_secret("CC_RAY", "ray@lutinemanagement.com")
FROM_NAME = _get_secret("BAND_FROM_NAME", "PRS Scheduling")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing Supabase credentials.")

def _parse_time_flex(t: Optional[str]) -> dt.time:
    """Accept 'HH:MM' or 'HH:MM:SS'; default to 17:00 if missing/invalid."""
    if not t:
        return dt.time(17, 0)
    s = str(t).strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return dt.datetime.strptime(s, fmt).time()
        except ValueError:
            pass
    return dt.time(17, 0)

def _sb():
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def _week_window(now: dt.datetime) -> tuple[dt.datetime, dt.datetime]:
    tz = ZoneInfo(TZ)
    start = dt.datetime(now.year, now.month, now.day, tzinfo=tz)
    end = start + dt.timedelta(days=7)
    return start, end


def _fetch_soundtechs(sb) -> List[dict]:
    resp = sb.table("sound_techs").select("id, display_name, first_name, last_name, email").execute()
    return [r for r in (resp.data or []) if r.get("email")]

def _fetch_events_for_range(sb, start: dt.datetime, end: dt.datetime) -> List[dict]:
    start_s = start.strftime("%Y-%m-%d")
    end_s = end.strftime("%Y-%m-%d")

    return (
        sb.table("gigs")
        .select(
            "id, title, event_date, start_time, end_time, "
            "sound_provided, sound_fee, sound_tech_id"
        )
        .gte("event_date", start_s)
        .lt("event_date", end_s)
        .execute()
    ).data or []

def _insert_email_audit(sb, *, token: str, recipient_email: str, gig_id: str | None = None):
    sb.table("email_audit").insert(
        {
            "token": token,
            "gig_id": gig_id,     # None for digest emails covering multiple gigs
            "event_id": None,
            "recipient_email": recipient_email,
            "kind": "soundtech_weekly_digest",
            "status": "sent",
            "ts": dt.datetime.utcnow().isoformat(),
        }
    ).execute()


def run_weekly_digest(now: Optional[dt.datetime] = None):
    if now is None:
        now = dt.datetime.now()
    sb = _sb()
    start, end = _week_window(now)
    tz = ZoneInfo(TZ)

    techs = _fetch_soundtechs(sb)
    events = _fetch_events_for_range(sb, start, end)

    by_tech: Dict[str, List[dict]] = defaultdict(list)  # sound_tech_id is UUID
    for ev in events:
        tid = ev.get("sound_tech_id")
        if tid:
            by_tech[tid].append(ev)

    for tech in techs:
        gigs = by_tech.get(tech["id"], [])
        if not gigs:
            continue

        rows: List[Dict[str, Any]] = []
        attachments: List[Dict[str, Any]] = []

        for ev in sorted(gigs, key=lambda r: (r["event_date"], r.get("start_time") or "")):
            fee_str = None
            if not ev.get("sound_provided") and ev.get("sound_fee") is not None:
                try:
                    fee_str = f"${float(ev['sound_fee']):,.2f}"
                except Exception:
                    fee_str = str(ev["sound_fee"])

            title = ev.get("title") or ev.get("gig_name") or "Gig"

            rows.append(
                {
                    "Gig": title,
                    "Date": ev["event_date"],
                    "Call Time": ev.get("start_time", ""),
                    "Venue": "",  # intentionally blank (schema-safe for now)
                    "Fee": fee_str or "—",
                }
            )

            if INCLUDE_ICS:
                uid = uuid.uuid4().hex + "@prs"
                day = dt.datetime.strptime(ev["event_date"], "%Y-%m-%d").date()
                st = dt.datetime.combine(
                    day,
                    _parse_time_flex(ev.get("start_time")),
                    tzinfo=tz,
                )
                et = st + dt.timedelta(hours=4)
                stz, etz = st, et

                ics = make_ics_bytes(
                    uid=uid,
                    title=f"{title} — Sound Tech",
                    starts_at=stz,
                    ends_at=etz,
                    location="",  # keep empty until we wire venue lookup
                    description="Sound tech call. PRS Scheduling.",
                )
                attachments.append(
                    {
                        "filename": f"{title}-{ev['event_date']}.ics",
                        "mime": "text/calendar",
                        "content": ics,
                    }
                )

        html = (
            f"<p>Hi {tech['first_name']},</p>"
            f"<p>Here are your sound gigs for the coming week ({start.date()} → {end.date()}).</p>"
            + build_html_table(rows)
            + f"<p>— {FROM_NAME}</p>"
        )
        subject = f"[Sound Tech] Weekly Digest — {start.date()}"

        token = uuid.uuid4().hex
        try:
            gmail_send(subject, tech["email"], html, cc=[CC_RAY], attachments=attachments)
            _insert_email_audit(sb, token=token, recipient_email=tech["email"], gig_id=None)
        except Exception as e:
            sb.table("email_audit").insert(
                {
                    "token": token,
                    "gig_id": None,
                    "event_id": None,
                    "recipient_email": tech["email"],
                    "kind": "soundtech_weekly_digest",
                    "status": f"error: {e}",
                    "ts": dt.datetime.utcnow().isoformat(),
                }
            ).execute()
            raise


if __name__ == "__main__":
    run_weekly_digest()
