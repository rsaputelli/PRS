# tools/contract_email.py
from __future__ import annotations
import uuid
import datetime as dt
from pathlib import Path
from typing import Any, Dict

from lib.email_utils import gmail_send
from supabase import create_client, Client

# ----------------------------------------------------
# Secrets and Supabase wiring (mirrors send_player_confirms)
# ----------------------------------------------------
import os

def _get_secret(name: str, default=None):
    try:
        import streamlit as st
        if hasattr(st, "secrets") and name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.environ.get(name, default)

SUPABASE_URL = _get_secret("SUPABASE_URL")
SUPABASE_KEY = (
    _get_secret("SUPABASE_SERVICE_ROLE")
    or _get_secret("SUPABASE_SERVICE_KEY")
    or _get_secret("supabase_service_key")
    or _get_secret("SUPABASE_KEY")
    or _get_secret("SUPABASE_ANON_KEY")
)

def _sb_admin() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)

def _insert_email_audit(*, token: str, gig_id: str, recipient_email: str, kind: str, status: str, detail: dict):
    _sb_admin().table("email_audit").insert({
        "token": token,
        "gig_id": gig_id,
        "event_id": None,
        "recipient_email": recipient_email,
        "kind": kind,       # "contract_email"
        "status": status,   # "sent", "error", "dry-run"
        "ts": dt.datetime.utcnow().isoformat(),
        "detail": detail,
    }).execute()

# ----------------------------------------------------
# Public API: send_contract_email(...)
# ----------------------------------------------------
def send_contract_email(*, recipient_email: str, ctx: Dict[str, Any], docx_path: Path):
    docx_path = Path(docx_path)
    """
    Sends the finalized contract DOCX to the organizer using gmail_send,
    and logs the email in email_audit (contract_email).
    """

    gig_id = ctx.get("gig_id")
    event_type = ctx.get("private_event_type") or ctx.get("gig_title") or "Your Event"
    event_date = ctx.get("event_date_long") or ctx.get("computed_event_date_formatted") or ""
    organizer_name = ctx.get("private_client_name") or ctx.get("private_organizer") or "there"

    subject = f"Philly Rock and Soul Contract – {event_type}"

    body_html = f"""
    <p>Dear {organizer_name},</p>

    <p>Attached is your finalized Philly Rock and Soul performance agreement for:</p>

    <ul>
        <li><b>Event:</b> {event_type}</li>
        <li><b>Date:</b> {event_date}</li>
    </ul>

    <p>Please review and let us know if anything needs to be adjusted.
    When ready, simply sign and return the contract.</p>

    <p>Thank you for choosing Philly Rock and Soul!<br/>
    Ray</p>
    """

    token = uuid.uuid4().hex

    try:
        # Prepare attachment using file_obj, not raw bytes
        attachments = []
        with open(docx_path, "rb") as f:
            attachments.append({
                "filename": f"PRS_Contract_{gig_id}.docx",
                "mime": (
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document"
                ),
                "file_obj": f,   # <-- CRITICAL FIX
            })

        gmail_send(
            subject=subject,
            to_email=recipient_email,
            text=f"Dear {organizer_name}, your Philly Rock and Soul contract is attached as a DOCX file.",
            html=body_html,
            cc=["ray@lutinemanagement.com"],
            attachments=attachments,
        )

        _insert_email_audit(
            token=token,
            gig_id=str(gig_id),
            recipient_email=recipient_email,
            kind="contract_email",
            status="sent",
            detail={
                "filename": str(docx_path.name),
                "event_type": event_type,
                "event_date": event_date,
            },
        )

    except Exception as e:
        _insert_email_audit(
            token=token,
            gig_id=str(gig_id),
            recipient_email=recipient_email,
            kind="contract_email",
            status=f"error: {str(e)}",
            detail={"filename": docx_path.name, "error": str(e)},
        )
        raise

