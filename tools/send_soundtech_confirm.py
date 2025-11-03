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

def _get_secret(name: str, default: str | None = None):
    # Prefer Streamlit secrets when running inside the app
    try:
        import streamlit as st
        if hasattr(st, "secrets") and name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    # Fallback to environment variables (e.g., GitHub Actions)
    return os.environ.get(name, default)

TZ = _get_secret("APP_TZ", "America/New_York")
INCLUDE_ICS = str(_get_secret("INCLUDE_ICS", "true")).lower() in {"1", "true", "yes"}

SUPABASE_URL = _get_secret("SUPABASE_URL")
# Prefer service role for server-side scripts; fallback to standard or anon if needed
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
        "SUPABASE_SERVICE_ROLE / SUPABASE_KEY / SUPABASE_ANON_KEY in secrets."
    )



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
            .select("id, full_name, display_name, email")
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

    greet_name = tech.get("full_name") or tech.get("display_name") or "there"
    mailto = (
        f"mailto:{tech['email']}?subject="
        f"Confirm%20received%20-%20{title}%20({ev['event_date']})%20[{token}]&body=Reply%20to%20confirm.%20Token%3A%20{token}"
    )

    html = f"""
    <p>Hi {greet_name},</p>
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

    # ---- SEND + AUDIT with try/except ----
    try:
        gmail_send(subject, tech["email"], html, cc=[CC_RAY], attachments=atts)
        _insert_email_audit(
            sb,
            token=token,
            gig_id=ev["id"],
            recipient_email=tech["email"],
            kind="soundtech_confirm",
            status="sent",
        )
    except Exception as e:
        _insert_email_audit(
            sb,
            token=token,
            gig_id=ev["id"],
            recipient_email=tech["email"],
            kind="soundtech_confirm",
            status=f"error: {e}",
        )
        raise


# -----------------------------
# Auto T-7 sender (for scheduler)
# -----------------------------
def run_auto_t7(today: dt.date | None = None) -> None:
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
