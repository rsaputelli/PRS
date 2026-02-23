# pages/07_Gig_Closeout.py
import os
import streamlit as st
from datetime import date

# === DIAGNOSTIC BANNER — REMOVE AFTER VERIFIED ===
# VERSION_TAG = "Gig Closeout • v2025-11-09.3 (bulk + dropdown)"
# st.markdown(f":red_circle: **{VERSION_TAG}**")

# ===============================
# Secrets / Supabase init (canonical)
# ===============================
from supabase import create_client, Client

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

# Attach session for RLS
if st.session_state.get("sb_access_token") and st.session_state.get("sb_refresh_token"):
    try:
        sb.auth.set_session(
            access_token=st.session_state["sb_access_token"],
            refresh_token=st.session_state["sb_refresh_token"],
        )
    except Exception as e:
        st.warning(f"Could not attach session; proceeding with limited access. ({e})")

# ===============================
# Admin gate (canonical)
# ===============================
from auth_helper import require_admin

user, session, user_id = require_admin()
if not user:
    st.stop()

# ---------- config guard ----------
missing = []
if not os.environ.get("SUPABASE_URL"):
    missing.append("SUPABASE_URL")
if not (
    os.environ.get("SUPABASE_SERVICE_KEY")
    or os.environ.get("SUPABASE_KEY")
    or os.environ.get("SUPABASE_ANON_KEY")
):
    missing.append("SUPABASE_KEY/ANON/SERVICE")
if missing:
    st.error("Missing configuration: " + ", ".join(missing))
    st.stop()

# ---------- imports AFTER env is populated ----------
from lib.closeout_utils import (  # type: ignore
    fetch_gigs_by_status,
    fetch_closeout_bundle,
    upsert_payment_row,
    delete_payment_row,
    mark_closeout_status,
    money_fmt,
)

st.set_page_config(page_title="Gig Closeout", layout="wide")
st.title("Gig Closeout")

# ---------- constants ----------
PAYMENT_METHODS = ["Check", "Zelle", "Cash", "Venmo", "Other"]  # stored in 'method'

# ---------- gig_receipts helpers ----------
RECEIPT_TYPES = ["deposit", "final", "other"]

def fetch_gig_receipts(gig_id: str):
    """Return list of receipt rows for a gig, ordered by received_on asc."""
    try:
        resp = sb.table("gig_receipts").select("*").eq("gig_id", gig_id).order("received_on").execute()
        return resp.data or []
    except Exception as e:
        # Most common: table missing or RLS not enabled
        st.warning(f"Could not load client receipts (gig_receipts). ({e})")
        return []

def insert_gig_receipt(
    gig_id: str,
    received_on: date,
    amount: float,
    method: str = None,
    reference: str = None,
    receipt_type: str = "deposit",
    deposit_seq: int = None,
    notes: str = None,
):
    payload = {
        "gig_id": gig_id,
        "received_on": received_on.isoformat() if hasattr(received_on, "isoformat") else received_on,
        "amount": float(amount) if amount is not None else None,
        "method": method,
        "reference": reference,
        "receipt_type": receipt_type,
        "deposit_seq": deposit_seq,
        "notes": notes,
    }
    # Drop Nones to keep inserts clean
    payload = {k: v for k, v in payload.items() if v is not None and v != ""}
    sb.table("gig_receipts").insert(payload).execute()

def delete_gig_receipt(receipt_id: str):
    sb.table("gig_receipts").delete().eq("id", receipt_id).execute()

def summarize_receipts(receipts: list):
    """Return (total_received, last_received_on) from receipt rows."""
    total = 0.0
    last_dt = None
    for r in receipts or []:
        try:
            total += float(r.get("amount") or 0)
        except Exception:
            pass
        ro = r.get("received_on")
        if isinstance(ro, str):
            try:
                ro = date.fromisoformat(ro)
            except Exception:
                ro = None
        if ro and (last_dt is None or ro > last_dt):
            last_dt = ro
    return total, last_dt

def _compose_reference(method_detail: str, notes: str) -> str:
    """Join method detail (e.g., check #) and notes into a single 'reference' string."""
    parts = []
    if method_detail:
        md = method_detail.strip()
        if md:
            parts.append(md)
    if notes:
        nt = notes.strip()
        if nt:
            parts.append(nt)
    return " | ".join(parts)

# ---------- header controls ----------
mode = st.radio("Mode", ["Open", "Closed"], horizontal=True, label_visibility="collapsed", key="closeout_mode")
status_target = "open" if mode == "Open" else "closed"

gigs = fetch_gigs_by_status(status_target)
gig_opt = st.selectbox(
    "Select gig",
    options=gigs,
    format_func=lambda g: f"{g.get('event_date','?')} — {g.get('title','?')}{(' @ ' + g.get('venue_name','')) if g.get('venue_name') else ''}",
)

if not gig_opt:
    st.info(f"No gigs in {status_target.upper()} status.")
    st.stop()

gig, roster, payments = fetch_closeout_bundle(gig_opt["id"])

colL, colR = st.columns([2, 1], gap="large")

# ============================== LEFT: Venue & Payments Entry ==============================
with colL:
    # -------- Client receipts (Deposits + Final) --------
    receipts = fetch_gig_receipts(gig["id"])
    receipts_total, receipts_last_date = summarize_receipts(receipts)

    with st.expander("Client Receipts (Deposits + Final)", expanded=True):
        c1, c2, c3 = st.columns([1, 1, 2])
        with c1:
            st.metric("Total received", money_fmt(receipts_total))
        with c2:
            st.metric(
                "Outstanding (vs fee)",
                money_fmt((gig.get("fee") or 0) - receipts_total) if gig.get("fee") is not None else "—"
            )
        with c3:
            st.write("")  # spacer

        if receipts:
            # Display existing receipts
            rows = []
            for r in receipts:
                rows.append({
                    "Date": r.get("received_on"),
                    "Type": r.get("receipt_type"),
                    "Amount": r.get("amount"),
                    "Method": r.get("method"),
                    "Reference": r.get("reference"),
                    "Notes": r.get("notes"),
                    "id": r.get("id"),
                })
            st.dataframe(
                rows,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Amount": st.column_config.NumberColumn(format="$%.2f"),
                    "id": st.column_config.TextColumn("id", disabled=True),
                },
            )

            del_id = st.selectbox(
                "Delete a receipt (select by id)",
                options=[""] + [r.get("id") for r in receipts if r.get("id")],
                help="Deletes the selected receipt row.",
                key="prs_receipt_del_select",
            )
            if del_id and st.button("Delete selected receipt", type="secondary"):
                delete_gig_receipt(del_id)
                st.success("Receipt deleted.")
                st.rerun()
        else:
            st.info("No client receipts recorded yet for this gig.")

        st.markdown("### Add receipt")
        with st.form("prs_add_receipt_form", clear_on_submit=True):
            rc1, rc2, rc3, rc4 = st.columns([1.1, 1, 1, 1.2])
            with rc1:
                r_date = st.date_input("Received on", value=date.today(), key="prs_r_date")
            with rc2:
                r_amount = st.number_input("Amount", min_value=0.0, step=25.0, format="%.2f", key="prs_r_amount")
            with rc3:
                r_type = st.selectbox("Type", options=RECEIPT_TYPES, index=0, key="prs_r_type")
            with rc4:
                r_method = st.selectbox("Method", options=["", *PAYMENT_METHODS], index=0, key="prs_r_method")

            r_detail = st.text_input("Reference / check # / transaction id (optional)", key="prs_r_detail")
            r_notes = st.text_input("Notes (optional)", key="prs_r_notes")

            if st.form_submit_button("Add receipt"):
                if not r_amount or float(r_amount) <= 0:
                    st.error("Amount must be greater than $0.")
                else:
                    ref = _compose_reference(r_detail, "")
                    insert_gig_receipt(
                        gig_id=gig["id"],
                        received_on=r_date,
                        amount=float(r_amount),
                        method=r_method or None,
                        reference=ref or None,
                        receipt_type=r_type,
                        notes=r_notes or None,
                    )
                    st.success("Receipt added.")
                    st.rerun()

    # -------- Venue receipt snapshot (compat) --------
    st.subheader("Venue Receipt (Snapshot)")

    _default_paid = gig.get("final_venue_paid_date")
    if isinstance(_default_paid, str):
        try:
            _default_paid = date.fromisoformat(_default_paid)
        except Exception:
            _default_paid = None
    if _default_paid is None:
        _default_paid = date.today()

    _default_paid_amt = gig.get("final_venue_gross") or 0.0

    with st.form("venue_receipt_form"):
        venue_paid = st.number_input(
            "Total paid by venue",
            min_value=0.0,
            step=25.0,
            value=float(receipts_total) if receipts_total and receipts_total > 0 else float(_default_paid_amt),
            format="%.2f",
        )
        venue_date = st.date_input("Paid date", value=(receipts_last_date or _default_paid))

        use_calc = st.checkbox(
            "Use calculated Client Receipts total for this snapshot",
            value=True if receipts_total and receipts_total > 0 else False,
            help="When checked, the snapshot fields (final_venue_gross / final_venue_paid_date) will be set from Client Receipts above."
        )

        notes = st.text_area("Closeout Notes", value=gig.get("closeout_notes") or "")

        if st.form_submit_button("Save Venue Closeout"):
            mark_closeout_status(
                gig["id"],
                status="open",
                final_venue_gross=(receipts_total if use_calc and receipts_total is not None else venue_paid),
                final_venue_paid_date=(receipts_last_date if use_calc and receipts_last_date is not None else venue_date),
                closeout_notes=notes,
            )
            st.success("Saved.")
            st.rerun()

    st.divider()
    st.subheader("Payments to People")
    st.caption("Enter what was actually paid. These figures drive 1099s.")

# -------- Bulk Payments (NEW) --------
with st.expander("Bulk Payments"):
    label_to_r = {r["label"]: r for r in roster}

    with st.form("bulk_payments_form"):

        chosen_labels = st.multiselect(
            "Select roster entries to pay",
            options=list(label_to_r.keys()),
        )

        bulk_gross = st.number_input("Gross", min_value=0.0, step=10.0, format="%.2f")
        bulk_method = st.selectbox("Method", PAYMENT_METHODS, index=0)
        bulk_detail = st.text_input("Method detail (check # / txn id)", value="")
        bulk_notes = st.text_input("Notes (optional)", value="")

        if st.form_submit_button("Apply payments"):
            if not chosen_labels:
                st.error("Select at least one roster entry.")
            elif bulk_gross <= 0:
                st.error("Gross must be > 0.")
            else:
                for lbl in chosen_labels:
                    r = label_to_r[lbl]
                    payload = {
                        "gig_id": gig["id"],
                        "musician_id": r.get("musician_id"),
                        "label": r.get("label"),
                        "role": r.get("role"),
                        "gross": float(bulk_gross),
                        "method": bulk_method,
                        "reference": _compose_reference(bulk_detail, bulk_notes),
                    }
                    upsert_payment_row(payload)
                st.success(f"Applied {len(chosen_labels)} payments.")
                st.rerun()

# -------- Individual Payments --------
with colR:
    st.subheader("Roster")
    for r in roster:
        st.write(f"- {r.get('label','?')}")

    st.divider()
    st.subheader("Recorded Payments")
    if not payments:
        st.info("No payments recorded yet.")
    else:
        for p in payments:
            st.write(
                f"• {p.get('label','?')} — {money_fmt(p.get('gross') or 0)} ({p.get('method') or ''})"
            )
            if st.button("Delete", key=f"del_pay_{p.get('id')}"):
                delete_payment_row(p["id"])
                st.success("Deleted.")
                st.rerun()

st.divider()

colA, colB = st.columns([1, 1])
with colA:
    if st.button("Mark Closed", type="primary"):
        mark_closeout_status(
            gig["id"],
            status="closed",
            final_venue_gross=gig.get("final_venue_gross"),
            final_venue_paid_date=gig.get("final_venue_paid_date"),
            closeout_notes=gig.get("closeout_notes") or "",
        )
        st.success("Gig marked CLOSED.")
        st.rerun()

with colB:
    if st.button("Re-open"):
        mark_closeout_status(
            gig["id"],
            status="open",
            final_venue_gross=gig.get("final_venue_gross"),
            final_venue_paid_date=gig.get("final_venue_paid_date"),
            closeout_notes=gig.get("closeout_notes") or "",
        )
        st.success("Gig marked OPEN.")
        st.rerun()
