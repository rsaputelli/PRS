# tools/send_setlist_notifications.py
from __future__ import annotations

from typing import Any, Dict, List
import uuid

from lib.email_utils import gmail_send
from tools.send_player_confirms import (
    _fetch_gig,
    _fetch_venue,
    _fetch_musicians_map,
    _gig_musicians_rows,
    _insert_email_audit,
    _normalize_and_validate_email,
    _nz,
    _stage_pref,
    _fmt_time12,
    _html_escape,
    _is_dry_run,
)


def send_setlist_notifications(gig_id: str) -> Dict[str, Any]:
    """Send a set list uploaded notification to all assigned players."""
    gig_id = str(gig_id)
    gig = _fetch_gig(gig_id)
    setlist_url = str(gig.get("setlist_url") or "").strip()
    if not setlist_url:
        raise ValueError("No set list URL available for this gig.")

    title = _nz(gig.get("title")) or "Gig"
    event_dt = _nz(gig.get("event_date"))
    start_time = _nz(gig.get("start_time"))
    end_time = _nz(gig.get("end_time"))
    formatted_start_time = _fmt_time12(start_time)
    formatted_end_time = _fmt_time12(end_time)
    venue = _fetch_venue(gig.get("venue_id"))
    venue_name = _nz(venue.get("name"))

    gm_rows = _gig_musicians_rows(gig_id)
    ordered_ids = [str(r.get("musician_id")) for r in gm_rows if r.get("musician_id")]
    if not ordered_ids:
        return {
            "sent": 0,
            "skipped_no_email": 0,
            "errors": [],
            "no_players": True,
        }

    mus_map = _fetch_musicians_map(ordered_ids)
    sent_count = 0
    skipped_count = 0
    errors: List[str] = []

    subject = f"Updated Set List Available: {title}"

    for mid in ordered_ids:
        mrow = mus_map.get(mid) or {}
        raw_email = mrow.get("email")
        to_email = _normalize_and_validate_email(raw_email)
        if not to_email:
            skipped_count += 1
            _insert_email_audit(
                token=uuid.uuid4().hex,
                gig_id=gig_id,
                recipient_email="",
                kind="setlist_notification",
                status="skipped-no-email",
                detail={
                    "musician_id": mid,
                    "raw_email": _nz(raw_email),
                    "error": "missing_or_invalid_email",
                },
            )
            continue

        greet = _stage_pref(mrow)
        role = _nz(next((r.get("role") for r in gm_rows if str(r.get("musician_id")) == mid), ""))

        html_body = f"""
        <p>Hello {greet},</p>
        <p>A new set list has been uploaded for <b>{_html_escape(title)}</b>.</p>
        <h4>Event Details</h4>
        <table border="0" cellpadding="4" cellspacing="0">
          <tr><td><b>Date</b></td><td>{_html_escape(event_dt)}</td></tr>
          <tr><td><b>Time</b></td><td>{_html_escape(formatted_start_time)} - {_html_escape(formatted_end_time)}</td></tr>
          <tr><td><b>Venue</b></td><td>{_html_escape(venue_name) or 'TBD'}</td></tr>
          <tr><td><b>Role</b></td><td>{_html_escape(role) or 'Player'}</td></tr>
        </table>
        <p><a href="{_html_escape(setlist_url)}">Download the updated set list</a></p>
        <p>Please review the set list before the gig and let us know if anything needs attention.</p>
        """

        try:
            if not _is_dry_run():
                gmail_send(subject, to_email, html_body)
            sent_count += 1
            _insert_email_audit(
                token=uuid.uuid4().hex,
                gig_id=gig_id,
                recipient_email=to_email,
                kind="setlist_notification",
                status="dry-run" if _is_dry_run() else "sent",
                detail={
                    "musician_id": mid,
                    "subject": subject,
                    "setlist_url": setlist_url,
                    "event_date": event_dt,
                    "start_time": start_time,
                    "end_time": end_time,
                },
            )
        except Exception as exc:
            error_text = str(exc)
            errors.append(error_text)
            _insert_email_audit(
                token=uuid.uuid4().hex,
                gig_id=gig_id,
                recipient_email=to_email,
                kind="setlist_notification",
                status=f"error: {error_text}",
                detail={
                    "musician_id": mid,
                    "subject": subject,
                    "setlist_url": setlist_url,
                    "event_date": event_dt,
                    "start_time": start_time,
                    "end_time": end_time,
                    "error": error_text,
                },
            )

    return {
        "sent": sent_count,
        "skipped_no_email": skipped_count,
        "errors": errors,
        "no_players": False,
    }
