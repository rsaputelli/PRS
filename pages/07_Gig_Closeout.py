# pages/07_Gig_Closeout.py
import os
import streamlit as st
from datetime import date

# === DIAGNOSTIC BANNER — REMOVE AFTER VERIFIED ===
# VERSION_TAG = "Gig Closeout • v2025-11-09.3 (bulk + dropdown)"
# st.markdown(f":red_circle: **{VERSION_TAG}**")

# ---------- bootstrap secrets to env (safe per-key) ----------
def _sec(name: str):
    try:
        return st.secrets[name]
    except Exception:
        return None

for k in ("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_KEY"):
    v = _sec(k)
    if v and not os.environ.get(k):
        os.environ[k] = str(v)

st.set_page_config(page_title="Gig Closeout", layout="wide")

# ===============================
# AUTH + ADMIN GATE (Unified PRS Model)
# ===============================
from lib.auth import is_logged_in, current_user, IS_ADMIN

# Require login
if not is_logged_in():
    st.error("Please sign in from the Login page.")
    st.stop()

USER = current_user()

# Require admin
if not IS_ADMIN():
    st.error("You do not have permission to access Gig Closeout.")
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

st.title("Gig Closeout")

# ---------- constants ----------
PAYMENT_METHODS = ["Check", "Zelle", "Cash", "Venmo", "Other"]  # stored in 'method'


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
    # -------- Venue receipt block (unchanged behavior) --------
    st.subheader("Venue Receipt")

    _default_paid = gig.get("final_venue_paid_date")
    if isinstance(_default_paid, str):
        try:
            _default_paid = date.fromisoformat(_default_paid)
        except Exception:
            _default_paid = None

    with st.form("venue_receipt"):
        venue_paid = st.number_input(
            "Final Gross Received from Venue",
            min_value=0.0,
            step=50.0,
            value=float(gig.get("final_venue_gross") or 0.0),
        )
        venue_date = st.date_input("Venue Paid Date", value=_default_paid or date.today())
        notes = st.text_area("Closeout Notes", value=gig.get("closeout_notes") or "")

        if st.form_submit_button("Save Venue Closeout"):
            # keep status OPEN on save; only Mark Closed flips it
            mark_closeout_status(
                gig["id"],
                status="open",
                final_venue_gross=venue_paid,
                final_venue_paid_date=venue_date,
                closeout_notes=notes,
            )
            st.success("Saved.")
            st.rerun()

    st.divider()
    st.subheader("Payments to People")
    st.caption("Enter what was actually paid. These figures drive 1099s.")

    # -------- Bulk Payments (NEW) --------
    with st.expander("Bulk Payments"):
        # Build label map for roster
        label_to_r = {r["label"]: r for r in roster}
        chosen_labels = st.multiselect(
            "Select roster entries to pay",
            options=list(label_to_r.keys()),
        )

        with st.form("bulk_payments_form"):
            bulk_gross = st.number_input("Gross Amount (per person)", min_value=0.0, step=25.0, value=0.0)
            bulk_fee = st.number_input("Fee Withheld (per person, optional)", min_value=0.0, step=5.0, value=0.0)
            bulk_method = st.selectbox("Payment Method", PAYMENT_METHODS, index=0, key="bulk_method")
            bulk_method_detail = st.text_input("Method details (e.g., check #1234)", "", key="bulk_method_detail")
            bulk_paid_date = st.date_input("Paid Date", value=date.today(), key="bulk_paid_date")
            bulk_1099 = st.checkbox("1099 Eligible (apply to all)", value=True, key="bulk_1099")
            bulk_notes = st.text_area("Notes (applies to all)", "", key="bulk_notes")

            add_disabled = (len(chosen_labels) == 0) or (bulk_gross <= 0.0)

            if st.form_submit_button("Add Payments", disabled=add_disabled):
                count = 0
                reference = _compose_reference(bulk_method_detail, bulk_notes)
                for lbl in chosen_labels:
                    r = label_to_r.get(lbl)
                    if not r:
                        continue
                    upsert_payment_row(
                        gig_id=gig["id"],
                        payee_type=r["type"],
                        payee_id=r["id"],
                        payee_name=r["name"],
                        role=r.get("role"),
                        gross=bulk_gross,
                        fee=bulk_fee,
                        method=bulk_method,        # dropdown value into 'method'
                        paid_date=bulk_paid_date,
                        eligible_1099=bulk_1099,
                        notes=reference,           # free text into 'reference'
                    )
                    count += 1
                st.success(f"Added {count} payment(s).")
                st.rerun()

    # -------- Per-roster single add (existing, upgraded with dropdown) --------
    for r in roster:
        with st.expander(f"{r['label']}"):
            with st.form(f"pay_{r['type']}_{r['id']}"):
                gross = st.number_input(
                    "Gross Amount",
                    min_value=0.0,
                    step=25.0,
                    key=f"gross_{r['type']}_{r['id']}",
                )
                fee = st.number_input(
                    "Fee Withheld (if any)",
                    min_value=0.0,
                    step=5.0,
                    key=f"fee_{r['type']}_{r['id']}",
                )
                method = st.selectbox(
                    "Payment Method",
                    PAYMENT_METHODS,
                    index=0,
                    key=f"method_{r['type']}_{r['id']}",
                )
                method_detail = st.text_input(
                    "Method details (e.g., check #1234)",
                    "",
                    key=f"mdetail_{r['type']}_{r['id']}",
                )
                paid_date = st.date_input(
                    "Paid Date",
                    key=f"pdate_{r['type']}_{r['id']}",
                )
                eligible = st.checkbox(
                    "1099 Eligible",
                    value=True,
                    key=f"e1099_{r['type']}_{r['id']}",
                )
                note = st.text_input(
                    "Notes",
                    "",
                    key=f"note_{r['type']}_{r['id']}",
                )

                if st.form_submit_button("Add Payment"):
                    reference = _compose_reference(method_detail, note)
                    upsert_payment_row(
                        gig_id=gig["id"],
                        payee_type=r["type"],
                        payee_id=r["id"],
                        payee_name=r["name"],
                        role=r.get("role"),
                        gross=gross,
                        fee=fee,
                        method=method,           # dropdown value into 'method'
                        paid_date=paid_date,
                        eligible_1099=eligible,
                        notes=reference,         # free text into 'reference'
                    )
                    st.success("Added.")
                    st.rerun()

    # -------- Manual payment (agent/musician/sound/other) --------
    with st.expander("Add manual payment (agent/musician/sound/other)"):
        with st.form("manual_pay"):
            manual_kind = st.selectbox("Payee type", ["agent", "musician", "sound", "other"])
            manual_name = st.text_input("Payee name")
            manual_role = st.text_input("Role (optional)")
            m_gross = st.number_input("Gross Amount", min_value=0.0, step=25.0, key="manual_gross")
            m_fee = st.number_input("Fee Withheld (if any)", min_value=0.0, step=5.0, key="manual_fee")
            m_method = st.selectbox("Payment Method", PAYMENT_METHODS, index=0, key="manual_method")
            m_method_detail = st.text_input("Method details (e.g., check #1234)", "", key="manual_method_detail")
            m_paid_date = st.date_input("Paid Date", key="manual_date")
            m_eligible = st.checkbox("1099 Eligible", value=True, key="manual_1099")
            m_note = st.text_input("Notes", "", key="manual_note")

            if st.form_submit_button("Add Manual Payment"):
                reference = _compose_reference(m_method_detail, m_note)
                upsert_payment_row(
                    gig_id=gig["id"],
                    payee_type=manual_kind,
                    payee_id=None,
                    payee_name=manual_name or None,
                    role=(manual_role or None),
                    gross=m_gross,
                    fee=m_fee,
                    method=m_method,        # dropdown value into 'method'
                    paid_date=m_paid_date,
                    eligible_1099=m_eligible,
                    notes=reference,        # free text into 'reference'
                )
                st.success("Added.")
                st.rerun()

# ============================== RIGHT: Current Payments ==============================
with colR:
    st.subheader("Current Payments")
    if not payments:
        st.info("No payments recorded yet.")
    else:
        for p in payments:
            kind = p.get("kind") or "payment"
            name = p.get("payee_name") or ""
            role = p.get("role") or ""
            gross = p.get("amount") or 0.0
            fee = p.get("fee_withheld") or 0.0
            net = p.get("net_amount") if "net_amount" in p else (gross - fee)
            paid_on = p.get("paid_on") or ""

            st.write(f"**{kind}** — {name} {('('+role+')') if role else ''}")
            st.write(f"Gross {money_fmt(gross)} | Fee {money_fmt(fee)} | Net {money_fmt(net)}")
            st.write(f"{p.get('method') or ''} · Paid {paid_on}")
            if p.get("reference"):
                st.caption(p.get("reference"))

            if st.button("Delete", key=f"del_{p['id']}"):
                delete_payment_row(p["id"])
                st.warning("Deleted.")
                st.rerun()

st.divider()
left, right = st.columns(2)
if left.button("Save (keep Open)"):
    mark_closeout_status(gig["id"], status="open")
    st.success("Saved (status remains Open).")

if right.button("Mark Closed"):
    mark_closeout_status(gig["id"], status="closed")
    st.success("Gig marked Closed (payments locked).")
