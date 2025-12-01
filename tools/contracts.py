# tools/contracts.py
from __future__ import annotations

import os
from typing import Any, Dict, Optional
from datetime import timedelta
from pathlib import Path

# Directory where assets live
ASSETS_DIR = (Path(__file__).resolve().parent.parent / "assets").resolve()

class ContractContextError(RuntimeError):
    """Raised when the contract context cannot be built for a given gig."""


def _safe_get_first(data: Optional[dict | list]) -> Optional[dict]:
    """
    Return the first element if data is a non-empty list,
    or data itself if it's a dict.
    """
    if data is None:
        return None
    if isinstance(data, list):
        return data[0] if data else None
    return data


def _resp_to_data_error(resp: Any) -> tuple[dict, Optional[Any]]:
    """
    Fully normalize Supabase responses (typed or dict) into (data_dict, error).

    Handles shapes like:
        {"data": {...}, "count": null}
        {"data": [{...}], "count": null}
        Pydantic typed responses with model_dump()
    """

    # --- 1. Convert Pydantic typed response to dict ---
    if hasattr(resp, "model_dump"):
        try:
            raw = resp.model_dump()
        except Exception:
            raw = {"_raw": resp}
    elif isinstance(resp, dict):
        raw = resp
    else:
        # Fallback: treat this as data already
        return ({"_raw": resp}, None)

    # --- 2. Extract error (if any) ---
    err = raw.get("error")

    # --- 3. Extract the data wrapper ---
    data = raw.get("data", raw)

    # CASE A: single object
    if isinstance(data, dict):
        return data, err

    # CASE B: single-row list
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
        return data[0], err

    # CASE C: empty list
    if isinstance(data, list) and len(data) == 0:
        return {}, err

    # CASE D: multi-row list (should not happen with .single())
    return data, err

def build_private_contract_context(sb, gig_id: str) -> Dict[str, Any]:
    """
    Build a merged context for contract generation for a given gig_id.

    Pulls from:
      - gigs
      - gigs_private
      - venues (via gigs.venues relationship)

    Returns a dict with:
      - flattened fields: gig_*, private_*, venue_*
      - nested subdicts: gig, private, venue
    """
    if not gig_id:
        raise ContractContextError("No gig_id provided")

    # DEBUG — ensure we know when this function runs
    try:
        print(
            "[contracts.build_private_contract_context] CALLED",
            "gig_id=", gig_id,
            "sb_type=", type(sb),
        )
    except Exception:
        pass

    # --- Fetch gig + venue ---
    gig_resp = (
        sb.table("gigs")
        .select(
            "*, "
            "venues("
            "  name, address_line1, address_line2, city, state, postal_code, country,"
            "  contact_name, contact_phone, contact_email"
            ")"
        )
        .eq("id", gig_id)
        .single()
        .execute()
    )

    # DEBUG raw response
    try:
        print("[build_private_contract_context] gig_resp:", type(gig_resp), repr(gig_resp))
    except Exception:
        pass

    gig_data, gig_error = _resp_to_data_error(gig_resp)

    if gig_error:
        msg = getattr(gig_error, "message", None) or str(gig_error)
        raise ContractContextError(f"Error loading gig {gig_id}: {msg}")

    gig: dict = gig_data or {}
    venue = _safe_get_first(gig.get("venues"))

    # --- Fetch private gig row ---
    priv_resp = (
        sb.table("gigs_private")
        .select("*")
        .eq("gig_id", gig_id)
        .single()
        .execute()
    )

    # DEBUG response
    try:
        print("[build_private_contract_context] priv_resp:", type(priv_resp), repr(priv_resp))
    except Exception:
        pass

    priv_data, priv_error = _resp_to_data_error(priv_resp)

    if priv_error:
        msg = getattr(priv_error, "message", None) or str(priv_error)
        raise ContractContextError(f"Error loading gigs_private for gig {gig_id}: {msg}")

    private: dict = priv_data or {}

    # --- Build flattened context ---
    ctx: Dict[str, Any] = {}

    # Gig basics — mapped directly to your gigs schema
    ctx["gig_id"] = gig.get("id")
    ctx["gig_title"] = gig.get("title")
    ctx["gig_event_date"] = gig.get("event_date")
    ctx["gig_start_time"] = gig.get("start_time")
    ctx["gig_end_time"] = gig.get("end_time")
    ctx["gig_fee"] = gig.get("fee")
    ctx["gig_total_fee"] = gig.get("total_fee")
    ctx["gig_notes"] = gig.get("notes")

    # Corrected — no "status" field in schema
    ctx["gig_contract_status"] = gig.get("contract_status")
    ctx["gig_closeout_status"] = gig.get("closeout_status")

    # Also include sound and related fields
    ctx["gig_sound_provided"] = gig.get("sound_provided")
    ctx["gig_sound_by_venue_name"] = gig.get("sound_by_venue_name")
    ctx["gig_sound_by_venue_phone"] = gig.get("sound_by_venue_phone")
    ctx["gig_sound_fee"] = gig.get("sound_fee")

    # Flatten all private fields with private_ prefix
    for key, value in private.items():
        ctx[f"private_{key}"] = value

    # Venue flattening
    if venue:
        ctx["venue_name"] = venue.get("name")
        ctx["venue_address_line1"] = venue.get("address_line1")
        ctx["venue_address_line2"] = venue.get("address_line2")
        ctx["venue_city"] = venue.get("city")
        ctx["venue_state"] = venue.get("state")
        ctx["venue_postal_code"] = venue.get("postal_code")
        ctx["venue_country"] = venue.get("country")
        ctx["venue_contact_name"] = venue.get("contact_name")
        ctx["venue_contact_phone"] = venue.get("contact_phone")
        ctx["venue_contact_email"] = venue.get("contact_email")

    # Keep raw nested objects (useful later for DOCX contracts)
    ctx["gig"] = gig
    ctx["private"] = private
    ctx["venue"] = venue

    # --------------------------------------------------------
    # DEPOSIT SCHEDULE
    # --------------------------------------------------------
    # Load deposits for this gig from gig_deposits
    dep_resp = (
        sb.table("gig_deposits")
        .select("*")
        .eq("gig_id", gig_id)
        .order("seq", asc=True)
        .execute()
    )

    dep_rows = dep_resp.data or []
    formatted_deposits = []

    from datetime import datetime as _dt

    for d in dep_rows:
        # Format date → "March 4, 2025"
        try:
            dt_obj = _dt.strptime(d["due_date"], "%Y-%m-%d").date()
            pretty_date = dt_obj.strftime("%B %-d, %Y")
        except Exception:
            pretty_date = d["due_date"]

        # Format currency → "$1,500.00"
        amt = d.get("amount", 0)
        pretty_amt = f"${float(amt):,.2f}"

        formatted_deposits.append({
            "seq": d.get("seq"),
            "due_date": pretty_date,
            "amount_formatted": pretty_amt,
        })

    ctx["deposit_schedule"] = formatted_deposits

    # --------------------------------------------------------
    # COMPUTED FIELDS FOR CONTRACT TEMPLATE
    # --------------------------------------------------------

    from datetime import datetime, time

    # Helpers
    def _to_12h(t: Optional[str]) -> Optional[str]:
        """Convert 'HH:MM:SS' → 'H:MM AM/PM'"""
        if not t:
            return None
        try:
            return datetime.strptime(t, "%H:%M:%S").strftime("%I:%M %p").lstrip("0")
        except Exception:
            return t

    def _parse_time(t: Optional[str]) -> Optional[time]:
        if not t:
            return None
        try:
            return datetime.strptime(t, "%H:%M:%S").time()
        except Exception:
            return None

    # Parse date
    event_date_raw = gig.get("event_date")
    computed_event_date_formatted = None
    if event_date_raw:
        try:
            computed_event_date_formatted = datetime.strptime(
                event_date_raw, "%Y-%m-%d"
            ).strftime("%B %d, %Y")
        except Exception:
            computed_event_date_formatted = event_date_raw

    # Parse times
    start_time_raw = gig.get("start_time")
    end_time_raw   = gig.get("end_time")

    start_time_12h = _to_12h(start_time_raw)
    end_time_12h   = _to_12h(end_time_raw)

    # Reception window
    if start_time_12h and end_time_12h:
        computed_reception_time_range_12h = f"{start_time_12h} – {end_time_12h}"
    else:
        computed_reception_time_range_12h = None

    # Duration calculation (hours)
    computed_duration_hours = None
    start_t = _parse_time(start_time_raw)
    end_t = _parse_time(end_time_raw)
    if start_t and end_t:
        # Handle past-midnight logic
        dt_start = datetime.combine(datetime.today(), start_t)
        dt_end = datetime.combine(datetime.today(), end_t)
        if dt_end < dt_start:
            dt_end = dt_end + timedelta(days=1)
        duration = (dt_end - dt_start).total_seconds() / 3600
        computed_duration_hours = round(duration, 2)

    # Final payment calculation
    deposit1 = private.get("deposit1_amount") or 0
    deposit2 = private.get("deposit2_amount") or 0
    contract_total = private.get("contract_total_amount") or gig.get("fee") or 0

    computed_final_payment_amount = max(
        contract_total - (deposit1 + deposit2), 0
    )

    # Has cocktails?
    cocktails_value = private.get("cocktail_coverage")
    if cocktails_value:
        computed_has_cocktails = True
        computed_cocktail_coverage = cocktails_value
    else:
        computed_has_cocktails = False
        computed_cocktail_coverage = "N/A"

    # --------------------------------------------------------
    # Add to ctx
    # --------------------------------------------------------
    ctx["computed_event_date_formatted"] = computed_event_date_formatted
    ctx["computed_start_time_12h"] = start_time_12h
    ctx["computed_end_time_12h"] = end_time_12h
    ctx["computed_reception_time_range_12h"] = computed_reception_time_range_12h
    ctx["computed_duration_hours"] = computed_duration_hours

    ctx["computed_final_payment_amount"] = computed_final_payment_amount
    ctx["computed_has_cocktails"] = computed_has_cocktails
    ctx["computed_cocktail_coverage"] = computed_cocktail_coverage

    # Also add convenience alias for template
    ctx["event_date_long"] = computed_event_date_formatted
    ctx["reception_time"] = computed_reception_time_range_12h
    ctx["duration_hours"] = computed_duration_hours

    # --------------------------------------------------------
    # CURRENCY + DEPOSIT FORMATTING (for contract display)
    # --------------------------------------------------------

    def _fmt_currency(val):
        """Format numeric as $1,500.00; return '' if missing."""
        if val in [None, ""]:
            return ""
        try:
            return f"${float(val):,.2f}"
        except Exception:
            return str(val)

    def _fmt_deposit(val):
        """
        For deposits:
        - If None, 0, '', return 'N/A'
        - Otherwise return formatted currency
        """
        if val in [None, "", 0, 0.0]:
            return "N/A"
        try:
            return f"${float(val):,.2f}"
        except Exception:
            return "N/A"

    # Fee + deposits (formatted fields for template)
    ctx["fee_formatted"] = _fmt_currency(gig.get("fee"))
    ctx["total_fee_formatted"] = _fmt_currency(gig.get("total_fee"))
    ctx["final_payment_formatted"] = _fmt_currency(computed_final_payment_amount)

    # Deposits
    deposit1_amount = private.get("deposit1_amount") or 0
    deposit2_amount = private.get("deposit2_amount") or 0

    ctx["deposit1_display"] = _fmt_deposit(deposit1_amount)
    ctx["deposit2_display"] = _fmt_deposit(deposit2_amount)
    
    # Overtime rate (formatted)
    overtime_raw = private.get("overtime_rate") or private.get("overtime_rate_per_hour") or private.get("private_overtime_rate_per_half_hour")
    ctx["overtime_rate_formatted"] = _fmt_currency(overtime_raw)

    # --------------------------------------------------------
    # SIGNATURE + LOGO FILE PATHS
    # --------------------------------------------------------
    ctx["signature_image_path"] = os.path.join(ASSETS_DIR, "ray_signature.png")
    ctx["logo_image_path"] = os.path.join(ASSETS_DIR, "prs_logo.png")

    return ctx
