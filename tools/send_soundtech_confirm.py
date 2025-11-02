# tools/send_soundtech_confirm.py
from __future__ import annotations
import os
import uuid
import datetime as dt
from typing import Dict, Any

import pytz
from supabase import create_client, Client

from lib.email_utils import gmail_send, build_html_table
from lib.calendar_utils import make_ics_bytes

TZ = os.getenv("APP_TZ", "America/New_York")
INCLUDE_ICS = os.getenv("INCLUDE_ICS", "true").lower() in {"1", "true", "yes"}

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE") or os.environ["SUPABASE_KEY"]
CC_RAY = os.getenv("CC_RAY", "ray@lutinemanagement.com")
FROM_NAME = os.getenv("BAND_FROM_NAME", "PRS Scheduling")
FROM_EMAIL = os.getenv("BAND_FROM_EMAIL", "no-reply@prs.local")


def _sb() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def _fetch_event_and_tech(sb: Client, gig_id: str) -> Dict[str, Any]:
    ev = (
        sb.table("gigs")
        .select(
            "id, title, gig_name, event_date, start_time, end_time, "
            "sound_provided, sound_fee, sound_tech_id"
        )
        .eq("id", gig_id)
        .single()
        .execute()
    ).data
    if not ev:
        raise ValueError(f"Gig {gig_id} not found")

    tech = None
    if ev.get("sound_tech_id"):
        tech = (
            sb.table("sound_techs")
            .select("id, full_name, email")
            .eq("id", ev["sound_tech_id"])
            .single()
            .execute()
        ).data

    if not tech or not tech.get("email"):
        raise ValueError("Assigned sound tech has no email")

    return {"event": ev, "tech": tech}


def _localize(event_date_str: str, time_str: str) -> tuple[dt.datetime, dt.datetime]:
    tz = pytz.timezone(TZ)
    day = dt.datetime.strptime(event_date_str, "%Y-%m-%d")
    st = dt.datetime.combine(day.date(), dt.datetime.strptime(time_str or "17:00", "%H:%M").time())
    et = st + dt.timedelta(hours=4)
    return tz.localize(st), tz.localize(et)


def _insert_email_audit(
    sb: Client, *, token: str, gig_id: str, recipient_email: str, kind: str, status: str
):
    sb.table("email_audit").insert(
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


def send_soundtech_confirm(gig_id: str) -> None:
    sb = _sb()
    payload = _fetch_event_and_tech(sb, gig_id)
    ev, tech = payload["event"], payload["tech"]

    starts_at, ends_at = _localize(ev["event_date"], ev.get("start_time") or "17:00")
    fee_str = None
    if not ev.get("sound_provided") and ev.get("sound_fee") is not None:
        fee_str = f"${float(ev['sound_fee']):,.2f}"

    token = uuid.uuid4().hex
    _insert_email_audit(
        sb,
        token=token,
        gig_id=ev["id"],
        recipient_email=tech["email"],
        kind="soundtech_confirm",
        status="sent",
    )

    title = ev.get("title") or ev.get("gig_name") or "Gig"

    rows = [
        {
            "Gig": title,
            "Date": ev["event_date"],
            "Call Time": ev.get("start_time", ""),
            "Fee (if applicable)": fee_str or "—",
        }
    ]
    html_table = build_html_table(rows)

    mailto = (
        f"mailto:{tech['email']}?subject="
        f"Confirm%20received%20-%20{title}%20({ev['event_date']})%20[{token}]&body=Reply%20to%20confirm.%20Token%3A%20{token}"
    )

    html = f"""
    <p>Hi {tech['full_name']},</p>
    <p>You’ve been assigned as <b>Sound Tech</b> for the gig below.</p>
    {html_table}
    <p>
      Please <a href="{mailto}"><b>confirm received</b></a>.
      This helps us keep staffing tight and on time.
    </p>
    <p>— {FROM_NAME}</p>
    """

    subject = f"[Sound Tech] {title} — {ev['event_date']}"

    atts = []
    if INCLUDE_ICS:
        ics_bytes = make_ics_bytes(
            uid=token + "@prs",
            title=f"{title} — Sound Tech",
            starts_at=starts_at,
            ends_at=ends_at,
            location="",  # optional: look up venue name/address if/when needed
            description="Sound tech call. Brought to you by PRS Scheduling.",
        )
        atts.append(
            {
                "filename": f"{title}-{ev['event_date']}.ics",
                "mime": "text/calendar",
                "content": ics_bytes,
            }
        )

    gmail_send(subject, tech["email"], html, cc=[CC_RAY], attachments=atts)


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Send immediate sound tech confirmation email")
    p.add_argument("gig_id", type=str, help="Gig ID (UUID)")
    args = p.parse_args()
    send_soundtech_confirm(args.gig_id)
