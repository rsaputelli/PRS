# tools/weekly_soundtech_digest.py
from __future__ import annotations
import os
import uuid
import datetime as dt
from collections import defaultdict
from typing import Optional, Dict, Any, List

import pytz
from supabase import create_client

from lib.email_utils import gmail_send, build_html_table
from lib.calendar_utils import make_ics_bytes

TZ = os.getenv("APP_TZ", "America/New_York")
INCLUDE_ICS = os.getenv("INCLUDE_ICS", "true").lower() in {"1", "true", "yes"}

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE") or os.environ["SUPABASE_KEY"]
CC_RAY = os.getenv("CC_RAY", "ray@lutinemanagement.com")
FROM_NAME = os.getenv("BAND_FROM_NAME", "PRS Scheduling")


def _sb():
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def _week_window(now: dt.datetime) -> tuple[dt.datetime, dt.datetime]:
    tz = pytz.timezone(TZ)
    start = tz.localize(dt.datetime(now.year, now.month, now.day))
    end = start + dt.timedelta(days=7)
    return start, end


def _fetch_soundtechs(sb) -> list[dict]:
    # Using sound_techs table
    resp = sb.table("sound_techs").select("id, full_name, email").execute()
    return [r for r in (resp.data or []) if r.get("email")]


def _fetch_events_for_range(sb, start: dt.datetime, end: dt.datetime) -> list[dict]:
    start_s = start.strftime("%Y-%m-%d")
    end_s = end.strftime("%Y-%m-%d")

    # PUBLIC gigs
    pub = (
        sb.table("gigs")
        .select(
            "id, title, gig_name, date, start_time, end_time, venue, city, state, address, "
            "sound_provided, sound_fee, sound_tech_id"
        )
        .gte("date", start_s)
        .lt("date", end_s)
        .execute()
    ).data or []

    # OPTIONAL: include PRIVATE gigs in digest (enable when ready)
    priv = []
    try:
        priv = (
            sb.table("gigs_private")
            .select(
                "id, title, gig_name, date, start_time, end_time, venue, city, state, address, "
                "sound_provided, sound_fee, sound_tech_id"
            )
            .gte("date", start_s)
            .lt("date", end_s)
            .execute()
        ).data or []
    except Exception:
        pass

    return pub + priv


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

    techs = _fetch_soundtechs(sb)
    events = _fetch_events_for_range(sb, start, end)

    by_tech: dict[str, list[dict]] = defaultdict(list)  # sound_tech_id is UUID
    for ev in events:
        tid = ev.get("sound_tech_id")
        if tid:
            by_tech[tid].append(ev)

    for tech in techs:
        gigs = by_tech.get(tech["id"], [])
        if not gigs:
            continue

        rows = []
        attachments = []
        for ev in sorted(gigs, key=lambda r: (r["date"], r.get("start_time") or "")):
            fee_str = None
            if not ev.get("sound_provided") and ev.get("sound_fee") is not None:
                fee_str = f"${float(ev['sound_fee']):,.2f}"

            title = ev.get("title") or ev.get("gig_name") or "Gig"

            rows.append(
                {
                    "Gig": title,
                    "Date": ev["date"],
                    "Call Time": ev.get("start_time", ""),
                    "Venue": ev.get("venue", ""),
                    "Fee": fee_str or "—",
                }
            )

            if INCLUDE_ICS:
                uid = uuid.uuid4().hex + "@prs"
                tz = pytz.timezone(TZ)
                day = dt.datetime.strptime(ev["date"], "%Y-%m-%d").date()
                st = dt.datetime.combine(
                    day, dt.datetime.strptime(ev.get("start_time") or "17:00", "%H:%M").time()
                )
                et = st + dt.timedelta(hours=4)
                stz, etz = tz.localize(st), tz.localize(et)

                ics = make_ics_bytes(
                    uid=uid,
                    title=f"{title} — Sound Tech",
                    starts_at=stz,
                    ends_at=etz,
                    location=f"{ev.get('venue','')} {ev.get('address','')} {ev.get('city','')}, {ev.get('state','')}",
                    description="Sound tech call. PRS Scheduling.",
                )
                attachments.append(
                    {
                        "filename": f"{title}-{ev['date']}.ics",
                        "mime": "text/calendar",
                        "content": ics,
                    }
                )

        html = (
            f"<p>Hi {tech['full_name']},</p>"
            f"<p>Here are your sound gigs for the coming week ({start.date()} → {end.date()}).</p>"
            + build_html_table(rows)
            + f"<p>— {FROM_NAME}</p>"
        )
        subject = f"[Sound Tech] Weekly Digest — {start.date()}"

        token = uuid.uuid4().hex
        _insert_email_audit(sb, token=token, recipient_email=tech["email"], gig_id=None)
        gmail_send(subject, tech["email"], html, cc=[CC_RAY], attachments=attachments)


if __name__ == "__main__":
    run_weekly_digest()
