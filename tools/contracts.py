# tools/contracts.py
from __future__ import annotations

from typing import Any, Dict, Optional


class ContractContextError(RuntimeError):
    """Raised when the contract context cannot be built for a given gig."""


def _safe_get_first(data: Optional[dict | list]) -> Optional[dict]:
    if data is None:
        return None
    if isinstance(data, list):
        return data[0] if data else None
    return data


def build_private_contract_context(sb, gig_id: str) -> Dict[str, Any]:
    """
    Build a merged context for contract generation for a given gig_id.

    Pulls from:
      - gigs
      - gigs_private (keyed by gig_id)
      - venues (via gigs.venues relationship)

    Returns a dict with:
      - flattened fields: gig_*, private_*, venue_*
      - nested subdicts: gig, private, venue
    """
    if not gig_id:
        raise ContractContextError("No gig_id provided")

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

    if gig_resp.error:
        raise ContractContextError(f"Error loading gig {gig_id}: {gig_resp.error.message}")

    gig = gig_resp.data or {}
    venue = _safe_get_first(gig.get("venues"))

    # --- Fetch private gig row ---
    priv_resp = (
        sb.table("gigs_private")
        .select("*")
        .eq("gig_id", gig_id)
        .single()
        .execute()
    )

    if priv_resp.error:
        raise ContractContextError(
            f"Error loading gigs_private for gig {gig_id}: {priv_resp.error.message}"
        )

    private = priv_resp.data or {}

    # --- Build flattened context ---
    ctx: Dict[str, Any] = {}

    # Gig basics (adapt to your actual gigs schema if needed)
    ctx["gig_id"] = gig.get("id")
    ctx["gig_title"] = gig.get("title")
    ctx["gig_event_date"] = gig.get("event_date")
    ctx["gig_start_time"] = gig.get("start_time")
    ctx["gig_end_time"] = gig.get("end_time")
    ctx["gig_fee"] = gig.get("fee")
    ctx["gig_notes"] = gig.get("notes")
    ctx["gig_status"] = gig.get("status")

    # Flatten all private fields with private_ prefix
    for key, value in private.items():
        ctx[f"private_{key}"] = value

    # Venue fields
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

    # Keep raw nested dicts as well (useful for DOCX templating later)
    ctx["gig"] = gig
    ctx["private"] = private
    ctx["venue"] = venue

    return ctx
