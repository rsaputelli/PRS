# lib/closeout_env.py
from __future__ import annotations

from typing import List, Dict, Any, Optional
from datetime import date, datetime, timezone
import os

from supabase import create_client, Client

def _sb() -> Client:
    url = os.environ.get("SUPABASE_URL")
    # Prefer SERVICE first, then KEY, then ANON
    key = (
        os.environ.get("SUPABASE_SERVICE_KEY")
        or os.environ.get("SUPABASE_KEY")
        or os.environ.get("SUPABASE_ANON_KEY")
    )
    if not url or not key:
        missing = []
        if not url: missing.append("SUPABASE_URL")
        if not key: missing.append("SUPABASE_SERVICE_KEY/KEY/ANON")
        raise RuntimeError("Missing configuration: " + ", ".join(missing))
    return create_client(url, key)


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

def fetch_open_or_draft_gigs() -> List[Dict[str, Any]]:
    sb = _sb()
    res = (
        sb.table("gigs")
        .select("*")
        .in_("closeout_status", ["open", "draft"])
        .order("event_date")
        .execute()
    )
    return res.data or []

def fetch_gigs_by_status(*statuses: str) -> List[Dict[str, Any]]:
    sb = _sb()
    sts = list(statuses) if statuses else ["open"]
    res = (
        sb.table("gigs")
        .select("*")
        .in_("closeout_status", sts)
        .order("event_date")
        .execute()
    )
    return res.data or []

def fetch_closeout_bundle(gig_id: str):
    sb = _sb()

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

    mus_rows = (
        sb.table("gig_musicians")
        .select("musician_id, role, musicians:musician_id(id, display_name)")
        .eq("gig_id", gig_id)
        .execute()
        .data
        or []
    )
    for r in mus_rows:
        m = r.get("musicians") or {}
        roster.append({
            "type": "musician",
            "id": m.get("id"),
            "name": m.get("display_name"),
            "role": r.get("role"),
            "label": f"Musician — {m.get('display_name')} ({r.get('role') or ''})".strip(),
        })

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
            roster.append({
                "type": "agent",
                "id": a["id"],
                "name": a["display_name"],
                "label": f"Agent — {a['display_name']}",
            })

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
            roster.append({
                "type": "sound",
                "id": s["id"],
                "name": s["display_name"],
                "label": f"Sound — {s['display_name']}",
            })

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

def upsert_payment_row(
    *,
    gig_id: str,
    payee_type: str,   # 'musician' | 'sound' | 'agent' | 'venue_receipt'
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
    sb = _sb()
    payload = {
        "gig_id": gig_id,
        "kind": payee_type,
        "due_on": _iso_date(paid_date) or _iso_date(date.today()),
        "amount": float(gross or 0),
        "paid_on": _iso_date(paid_date),
        "method": (method or None),
        "reference": (notes or None),
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
    status: str,   # 'open' | 'draft' | 'closed'
    final_venue_gross: Optional[float] = None,
    final_venue_paid_date: Optional[date] = None,
    closeout_notes: Optional[str] = None,
) -> None:
    assert status in ("open", "draft", "closed")
    sb = _sb()
    patch: Dict[str, Any] = {"closeout_status": status}
    if final_venue_gross is not None:
        patch["final_venue_gross"] = float(final_venue_gross)
    if final_venue_paid_date is not None:
        patch["final_venue_paid_date"] = _iso_date(final_venue_paid_date)
    if closeout_notes is not None:
        patch["closeout_notes"] = closeout_notes
    if status == "closed":
        patch["closeout_at"] = _iso_now()
    sb.table("gigs").update(patch).eq("id", gig_id).execute()
