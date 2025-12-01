# pages/03_Contracts_Create.py
from __future__ import annotations

import os
import datetime as dt
from typing import Any, Dict, List

import streamlit as st
from supabase import Client, create_client

from tools.contracts import build_private_contract_context, ContractContextError, ASSETS_DIR
from tools.contract_generate import render_contract_docx, convert_contract_docx_to_pdf
from pathlib import Path
from tools.contract_email import send_contract_email

# ============================
# Secrets / Supabase (match Enter_Gig pattern)
# ============================
def _get_secret(name: str, default=None, required: bool = False):
    if hasattr(st, "secrets") and name in st.secrets:
        val = st.secrets[name]
    else:
        val = os.environ.get(name, default)
    if required and (val is None or str(val).strip() == ""):
        st.error(f"Missing required secret: {name}")
        st.stop()
    return val


SUPABASE_URL = _get_secret("SUPABASE_URL", required=True)
SUPABASE_ANON_KEY = _get_secret("SUPABASE_ANON_KEY", required=True)
sb: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# Attach session for RLS (same approach as Enter_Gig/Edit_Gig)
if st.session_state.get("sb_access_token") and st.session_state.get("sb_refresh_token"):
    try:
        sb.auth.set_session(
            access_token=st.session_state["sb_access_token"],
            refresh_token=st.session_state["sb_refresh_token"],
        )
    except Exception as e:
        st.warning(f"Could not attach session; proceeding with limited access. ({e})")


# ============================
# Helpers
# ============================
def _fmt_ts(value: Any) -> str:
    """Format timestamps/dates nicely for display."""
    if not value:
        return "—"
    if isinstance(value, str):
        try:
            # Handle ISO with or without Z
            s = value.replace("Z", "+00:00")
            parsed = dt.datetime.fromisoformat(s)
            return parsed.strftime("%Y-%m-%d %H:%M")
        except Exception:
            # Just return the raw string if parsing fails
            return value
    if isinstance(value, dt.datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, dt.date):
        return value.strftime("%Y-%m-%d")
    return str(value)


def _fmt_date(value: Any) -> str:
    if not value:
        return "—"
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, dt.datetime):
        return value.date().strftime("%Y-%m-%d")
    if isinstance(value, str):
        try:
            parsed = dt.date.fromisoformat(value)
            return parsed.strftime("%Y-%m-%d")
        except Exception:
            return value
    return str(value)


def _fmt_currency(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, (int, float)):
        return f"${value:,.2f}"
    try:
        return f"${float(value):,.2f}"
    except Exception:
        return str(value)


def _load_private_gigs() -> List[Dict[str, Any]]:
    """
    Load all private gigs, joined to gigs for labeling.
    Based on gigs_private schema (primary key = gig_id).
    """
    resp = (
        sb.table("gigs_private")
        .select(
            "gig_id, organizer, event_type, honoree, "
            "client_name, client_email, contract_total_amount, "
            "contract_status, contract_sent_at, contract_last_sent_at, "
            "contract_signed_at, contract_pdf_path, contract_version, "
            "gigs(title, event_date)"
        )
        .order("contract_sent_at", desc=True)
        .execute()
    )

    # Be defensive about the response shape
    error = getattr(resp, "error", None)
    if error:
        msg = getattr(error, "message", None) or str(error)
        st.error(f"Error loading private gigs: {msg}")
        return []

    data = getattr(resp, "data", None)
    if data is not None:
        return data or []

    # Fallbacks if your client returns raw structures
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict) and "data" in resp:
        return resp["data"] or []

    return []


def _make_option_label(row: Dict[str, Any]) -> str:
    gig = row.get("gigs") or {}
    title = gig.get("title") or "(Untitled gig)"
    date = gig.get("event_date") or "No date"
    client = row.get("client_name") or "Unknown client"
    status = row.get("contract_status") or "draft"

    return f"{client} — {title} ({date}) [{status}]"


def _render_contract_preview(ctx: Dict[str, Any]) -> str:
    """
    Simple text-based preview using the merged context.
    Later this will be aligned with PRS_Contract_Template.docx.
    """

    # Core parties
    organizer = ctx.get("private_organizer") or ctx.get("private_client_name") or "ORGANIZER NAME"
    client_name = ctx.get("private_client_name") or "CLIENT NAME"
    client_email = ctx.get("private_client_email") or ""
    client_phone = ctx.get("private_client_phone") or ""
    client_address = ctx.get("private_client_mailing_address") or ""

    # Event basics
    event_type = ctx.get("private_event_type") or "Private Event"
    honoree = ctx.get("private_honoree") or ""
    event_title = ctx.get("gig_title") or event_type
    event_date = ctx.get("gig_event_date") or "EVENT DATE"
    start_time = ctx.get("gig_start_time") or ""
    end_time = ctx.get("gig_end_time") or ""

    # Reception coverage
    reception_start = ctx.get("private_reception_start_time") or ""
    reception_end = ctx.get("private_reception_end_time") or ""

    # Venue
    venue_name = ctx.get("venue_name") or "VENUE NAME"
    venue_city = ctx.get("venue_city") or ""
    venue_state = ctx.get("venue_state") or ""
    venue_line = venue_name
    if venue_city or venue_state:
        venue_line += f" — {venue_city}, {venue_state}"

    # Package / band details
    package_name = ctx.get("private_package_name") or "Performance Package"
    band_size = ctx.get("private_band_size")
    num_vocalists = ctx.get("private_num_vocalists")
    ceremony = ctx.get("private_ceremony_coverage") or ""
    cocktail = ctx.get("private_cocktail_coverage") or ""
    reception_block = ""
    if reception_start or reception_end:
        reception_block = f"Reception coverage: {reception_start} – {reception_end}"

    # Money / due dates
    total_amount = ctx.get("private_contract_total_amount") or ctx.get("gig_fee")
    deposit1_amount = ctx.get("private_deposit1_amount")
    deposit1_due = ctx.get("private_deposit1_due_date")
    deposit2_amount = ctx.get("private_deposit2_amount")
    deposit2_due = ctx.get("private_deposit2_due_date")
    final_due = ctx.get("private_final_payment_due_date")
    overtime_rate = ctx.get("private_overtime_rate_per_half_hour")

    payment_method_notes = ctx.get("private_payment_method_notes") or ""

    special_instructions = ctx.get("private_special_instructions") or ""
    gig_notes = ctx.get("gig_notes") or ""

    band_line = ""
    if band_size:
        band_line += f"{band_size}-piece band"
    if num_vocalists:
        band_line += f" with {num_vocalists} vocalists" if band_line else f"{num_vocalists} vocalists"

    deposits_lines = []
    if deposit1_amount:
        deposits_lines.append(
            f"Deposit 1: {_fmt_currency(deposit1_amount)} due {_fmt_date(deposit1_due)}"
        )
    if deposit2_amount:
        deposits_lines.append(
            f"Deposit 2: {_fmt_currency(deposit2_amount)} due {_fmt_date(deposit2_due)}"
        )
    if final_due:
        deposits_lines.append(f"Final balance due {_fmt_date(final_due)}")

    deposits_text = "\n".join(f"- {line}" for line in deposits_lines) if deposits_lines else "None specified"

    overtime_line = (
        f"Overtime: {_fmt_currency(overtime_rate)} per 30 minutes"
        if overtime_rate is not None
        else "Overtime: per band agreement"
    )

    preview = f"""
### Contract Preview (Text-Only)

**Client / Organizer**  
Organizer: {organizer}  
Client: {client_name}  
{client_email or ""}  
{client_phone or ""}  
{client_address or ""}

**Event**  
{event_title}  
Type: {event_type}{f" — Honoring {honoree}" if honoree else ""}  
Date: {event_date}{f" • {start_time} – {end_time}" if start_time or end_time else ""}

**Venue**  
{venue_line}

**Package**  
**{package_name}**  
{band_line or ""}  

Ceremony: {ceremony or "n/a"}  
Cocktail: {cocktail or "n/a"}  
{reception_block or ""}

**Financial Terms**  
Total Fee: {_fmt_currency(total_amount)}  

{deposits_text}

{overtime_line}

Payment Method Notes:  
{payment_method_notes or "None specified."}

**Special Instructions**  
{special_instructions or "None."}

**Additional Notes (from gig)**  
{gig_notes or "None."}

---

This is a *preview* based on the current contract context.
Next steps will wire this into **PRS_Contract_Template.docx** for PDF generation,
email sending to the organizer, and storage of the finalized PDF.
"""
    return preview


# ============================
# Main page
# ============================
def main() -> None:
    st.set_page_config(page_title="Contracts — PRS", page_icon="📄")
    st.title("Contracts")

    st.caption(
        "Create and manage private gig contracts. "
        "This page currently supports context building and preview; "
        "PDF and email flows will be layered on top."
    )

    private_gigs = _load_private_gigs()
    if not private_gigs:
        st.info("No private gigs found yet. Create a private gig record to begin.")
        return

    options = {_make_option_label(row): row for row in private_gigs}
    labels = list(options.keys())

    selected_label = st.selectbox("Select a private gig", labels)
    selected_row = options[selected_label]

    # --- Metadata section ---
    st.subheader("Contract Metadata")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Status", selected_row.get("contract_status") or "draft")
    col2.metric("Version", str(selected_row.get("contract_version") or 1))
    col3.metric("Sent at", _fmt_ts(selected_row.get("contract_sent_at")))
    col4.metric("Last Sent", _fmt_ts(selected_row.get("contract_last_sent_at")))

    col5, col6 = st.columns(2)
    col5.metric("Signed at", _fmt_ts(selected_row.get("contract_signed_at")))
    col6.metric("Total Amount", _fmt_currency(selected_row.get("contract_total_amount")))

    pdf_path = selected_row.get("contract_pdf_path") or "—"
    st.text(f"Current PDF path: {pdf_path}")

    # --- Build context + preview ---
    st.subheader("Contract Preview")

    try:
        ctx = build_private_contract_context(sb, selected_row["gig_id"])
    except ContractContextError as e:
        st.error(str(e))
        return

    st.markdown(_render_contract_preview(ctx))

    # --------------------------------------------------------
    # Generate Filled Contract (DOCX)
    # --------------------------------------------------------
    st.markdown("### Generate Filled Contract (DOCX)")

    template_path = ASSETS_DIR / "PRS_Contract_Template_Full_Modernized_v2.docx"
    # DEBUG: Inspect runtime paths
    import os
    st.write("ASSETS_DIR:", ASSETS_DIR)
    st.write("Files in ASSETS_DIR:", os.listdir(ASSETS_DIR))
    st.write("Template file full path:", str(template_path))
    st.write("Exists?", os.path.exists(template_path))

    print("DEBUG TEMPLATE PATH:", template_path)
    print("EXISTS:", template_path.exists())


    if st.button("Generate Contract (DOCX)"):
        try:
            with st.spinner("Rendering contract..."):
                output_path = render_contract_docx(ctx, template_path)

            st.success("Contract DOCX generated successfully!")

            with open(output_path, "rb") as f:
                st.download_button(
                    label="Download Contract DOCX",
                    data=f,
                    file_name="PRS_Contract_Filled.docx",
                    mime=(
                        "application/vnd.openxmlformats-"
                        "officedocument.wordprocessingml.document"
                    ),
                )

        except Exception as e:
            st.error(f"Error generating contract: {e}")
            
    # --------------------------------------------------------
    # Generate Contract (DOCX) and Email to Organizer
    # --------------------------------------------------------
    st.markdown("### Generate Contract (DOCX) and Email to Organizer")

    organizer_email = (
        ctx.get("private_client_email")
        or ctx.get("private_organizer_email")
        or ctx.get("private_organizer")
    )

    if not organizer_email:
        st.warning("Organizer email not found in context. Add an email in Edit Gig before using this feature.")
    else:
        st.info(f"Email will be sent to: **{organizer_email}**")

    if st.button("Generate DOCX and Email Contract"):
        try:
            with st.spinner("Rendering contract…"):
                filled_docx = render_contract_docx(ctx, template_path)

            with st.spinner(f"Emailing contract to {organizer_email}…"):
                send_contract_email(
                    recipient_email=organizer_email,
                    ctx=ctx,
                    docx_path=filled_docx,
                )

            st.success(f"Contract DOCX emailed to {organizer_email}!")

            # Optional: Let user download it too
            with open(filled_docx, "rb") as f:
                st.download_button(
                    label="Download DOCX",
                    data=f.read(),
                    file_name=f"PRS_Contract_{ctx.get('gig_id')}.docx",
                    mime=(
                        "application/vnd.openxmlformats-officedocument."
                        "wordprocessingml.document"
                    ),
                )

        except Exception as e:
            st.error(f"Error generating or sending contract: {e}")

         
    # --------------------------------------------------------
    # Debug expanded section
    # --------------------------------------------------------
    with st.expander("Show merged context (debug)", expanded=False):
        st.json(ctx)

if __name__ == "__main__":
    main()
