# tools/contracts.py
from __future__ import annotations

from typing import Any, Dict, Optional


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


def _resp_to_data_error(resp: Any) -> tuple[Any, Optional[Any]]:
    """
    Normalize Supabase responses across both old and new client libraries.

    Handles:
      - Pydantic model (new client, typed response)
      - dict-style responses
      - PostgrestResponse-like objects with .data / .error

    Returns:
      (data, error)
    """

    # NEW Supabase (Pydantic v2 typed model)
    if hasattr(resp, "model_dump"):
        try:
            return resp.model_dump(), None
        except Exception:
            pass  # Fall through to other strategies

    # NEW Supabase (dict-style)
    if isinstance(resp, dict):
        data = resp.get("data", resp)
        err = resp.get("error")
        return data, err

    # OLD supabase-py (PostgrestResponse)
    if hasattr(resp, "data"):
        data = resp.data
        err = getattr(resp, "error", None)
        return data, err

    # Fallback — treat resp as data, no error
    return resp, None


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

    return ctx
