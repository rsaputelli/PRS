# lib/closeout_utils.py
# PRS – Gig Closeout helpers (uses existing public.gig_payments)
# - Reads roster from: gigs, gig_musicians -> musicians, agents, sound_techs
# - Writes closeout rows into: public.gig_payments (additive to your current schema)
# - Adds/updates gigs.closeout_* fields
#
# Assumptions (match your schema screenshot):
#   gigs.id (UUID), gigs.agent_id (UUID, optional), gigs.sound_tech_id (UUID, optional)
#   gig_musicians: id, gig_id, musician_id, role
#   musicians/agents/sound_techs: id, display_name
#
# Columns used in public.gig_payments (existing):
#   id, gig_id, kind, due_on, amount, paid_on, method, reference, created_at
# Additive columns expected (run the ALTERs I sent): payee_id, payee_name, role,
#   fee_withheld, eligible_1099, net_amount (generated)
#
# Closeout "kind" values we will write: 'venue_receipt' | 'musician' | 'sound' | 'agent'

from __future__ import annotations
from typing import List, Dict, Any, Optional
from datetime import date, datetime, timezone

import streamlit as st
from supabase import create_client, Client


# ---------- Supabase client ----------
def _sb() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)


# ---------- Small utils ----------
def money_fmt(x: Optional[float]) -> str:
    if x is None:
        return "$0.00"
    try:
        return f"${float(x):,.2f}"
    except Exception:
        return str(x)


def _iso_date(d: Optional[date]) -> Optional[str]:
    return d.isoformat() if isinstance(d, date) else None


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------- Gig lists / roster bundles ----------
def fetch_open_or_draft_gigs() -> List[Dict[str, Any]]:
    """
    Return gigs with closeout_status in ('open','draft'), ordered by event_date.
    """
    sb = _sb()
    res = (
        sb.table("gigs")
        .select("*")
        .in_("closeout_status", ["open", "draft"])
        .order("event_date")
        .execute()
    )
    return res.data or []


def fetch_closeout_bundle(gig_id: str):
    """
    Load a gig record, build a roster (musicians, agent, sound),
    and load any existing gig_payments for the gig.
    """
    sb = _sb()

    # Gig
    gig = (
        sb.table("gigs")
        .select("*")
        .eq("id", gig_id)
        .single()
        .execute()
        .data
    )

    if not gig:
        return None, [], []

    roster: List[Dict[str, Any]] = []

    # Musicians via join table -> people table
    mus_rows = (
        sb.table("gig_musicians")
        .select(
            "musician_id, role, musicians:musician_id(id, display_name)"
        )
        .eq("gig_id", gig_id)
        .execute()
        .data
        or []
    )
    for r in mus_rows:
        m = r.get("musicians") or {}
        roster.append(
            {
                "type": "musician",
                "id": m.get("id"),
                "name": m.get("display_name"),
                "role": r.get("role"),
                "label": f"Musician — {m.get('display_name')} ({r.get('role') or ''})".strip(),
            }
        )

    # Agent (optional one-to-one on gigs.agent_id)
    agent_id = gig.get("agent_id")
    if agent_id:
        a = (
            sb.table("agents")
            .select("id, display_name")
            .eq("id", agent_id)
            .single()
            .execute()
            .data
        )
        if a:
            roster.append(
                {
                    "type": "agent",
                    "id": a["id"],
                    "name": a["display_name"],
                    "label": f"Agent — {a['display_name']}",
                }
            )

    # Sound tech (optional one-to-one on gigs.sound_tech_id)
    sound_id = gig.get("sound_tech_id")
    if sound_id:
        s = (
            sb.table("sound_techs")
            .select("id, display_name")
            .eq("id", sound_id)
            .single()
            .execute()
            .data
        )
        if s:
            roster.append(
                {
                    "type": "sound",
                    "id": s["id"],
                    "name": s["display_name"],
                    "label": f"Sound — {s['display_name']}",
                }
            )

    # Existing payments (ledger lines)
    payments = _payments_for(gig_id)

    return gig, roster, payments


def _payments_for(gig_id: str) -> List[Dict[str, Any]]:
    sb = _sb()
    res = (
        sb.table("gig_payments")
        .select("*")
        .eq("gig_id", gig_id)
        .order("created_at")
        .execute()
    )
    return res.data or []


# ---------- Mutations ----------
def upsert_payment_row(
    *,
    gig_id: str,
    payee_type: str,             # 'musician' | 'sound' | 'agent' | 'venue_receipt'
    payee_id: Optional[str],
    payee_name: Optional[str],
    role: Optional[str],
    gross: float,
    fee: float = 0.0,
    method: Optional[str] = None,
    paid_date: Optional[date] = None,
    eligible_1099: bool = True,
    notes: Optional[str] = None,
) -> None:
    """
    Insert a closeout ledger row into public.gig_payments using your existing schema.
    - amount is the gross paid
    - fee_withheld reduces net_amount (generated column if you ran the ALTERs)
    - due_on is set to paid_date (same-day ledger); you can change this if you defer payments
    - reference stores freeform notes
    """
    sb = _sb()
    payload = {
        "gig_id": gig_id,
        "kind": payee_type,               # aligns with your existing 'kind' column
        "due_on": _iso_date(paid_date) or _iso_date(date.today()),
        "amount": float(gross or 0),
        "paid_on": _iso_date(paid_date),
        "method": (method or None),
        "reference": (notes or None),
        # Additive detail columns (safe if you've run the ALTERs):
        "payee_id": payee_id,
        "payee_name": payee_name,
        "role": role,
        "fee_withheld": float(fee or 0),
        "eligible_1099": bool(eligible_1099),
    }
    sb.table("gig_payments").insert(payload).execute()


def delete_payment_row(payment_id: str) -> None:
    _sb().table("gig_payments").delete().eq("id", payment_id).execute()


def mark_closeout_status(
    gig_id: str,
    *,
    status: str,                  # 'open' | 'draft' | 'closed'
    final_venue_gross: Optional[float] = None,
    final_venue_paid_date: Optional[date] = None,
    closeout_notes: Optional[str] = None,
) -> None:
    assert status in ("open", "draft", "closed")
    sb = _sb()
    patch: Dict[str, Any] = {
        "closeout_status": status,
    }
    if final_venue_gross is not None:
        patch["final_venue_gross"] = float(final_venue_gross)
    if final_venue_paid_date is not None:
        patch["final_venue_paid_date"] = _iso_date(final_venue_paid_date)
    if closeout_notes is not None:
        patch["closeout_notes"] = closeout_notes

    if status == "closed":
        patch["closeout_at"] = _iso_now()

    sb.table("gigs").update(patch).eq("id", gig_id).execute()
