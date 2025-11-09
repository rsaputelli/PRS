import streamlit as st
import os
from lib.closeout_utils import (
    fetch_open_or_draft_gigs, fetch_closeout_bundle, upsert_payment_row,
    delete_payment_row, mark_closeout_status, money_fmt,
)

st.set_page_config(page_title="Gig Closeout", layout="wide")
missing = []
if not (st.secrets.get("SUPABASE_URL", None) if hasattr(st, "secrets") else None) and not os.environ.get("SUPABASE_URL"):
    missing.append("SUPABASE_URL")
if not (
    (hasattr(st, "secrets") and (st.secrets.get("SUPABASE_KEY") or st.secrets.get("SUPABASE_ANON_KEY") or st.secrets.get("SUPABASE_SERVICE_KEY")))
    or os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_SERVICE_KEY")
):
    missing.append("SUPABASE_KEY/ANON/SERVICE")
if missing:
    st.error("Missing configuration: " + ", ".join(missing))
    st.stop()

st.title("Gig Closeout")

mode = st.radio("Mode", ["Draft", "Closed"], horizontal=True, label_visibility="collapsed", key="closeout_mode")
status_target = "draft" if mode == "Draft" else "closed"

gigs = fetch_open_or_draft_gigs()
gig_opt = st.selectbox("Select gig", options=gigs, format_func=lambda g: f"{g['event_date']} — {g['title']} @ {g['venue_name']}")

if not gig_opt:
    st.info("Choose a gig to begin.")
    st.stop()

gig, roster, payments = fetch_closeout_bundle(gig_opt["id"])

colL, colR = st.columns([2, 1], gap="large")

with colL:
    st.subheader("Venue Receipt")
    with st.form("venue_receipt"):
        venue_paid = st.number_input("Final Gross Received from Venue", min_value=0.0, step=50.0, value=float(gig.get("final_venue_gross") or 0))
        venue_date = st.date_input("Venue Paid Date", value=gig.get("final_venue_paid_date"))
        notes = st.text_area("Closeout Notes", value=gig.get("closeout_notes") or "")
        if st.form_submit_button("Save Venue Closeout"):
            mark_closeout_status(gig["id"], status="draft", final_venue_gross=venue_paid, final_venue_paid_date=venue_date, closeout_notes=notes)
            st.success("Saved.")

    st.divider()
    st.subheader("Payments to People")
    st.caption("Enter what was actually paid. These figures drive 1099s.")

    # Quick add rows for each roster entry
    for r in roster:
        with st.expander(f"{r['label']}"):
            with st.form(f"pay_{r['type']}_{r['id']}"):
                gross = st.number_input("Gross Amount", min_value=0.0, step=25.0)
                fee = st.number_input("Fee Withheld (if any)", min_value=0.0, step=5.0)
                method = st.text_input("Method (check#, ACH, Zelle…)", "")
                paid_date = st.date_input("Paid Date")
                eligible = st.checkbox("1099 Eligible", value=True)
                note = st.text_input("Notes", "")
                if st.form_submit_button("Add Payment"):
                    upsert_payment_row(
                        gig_id=gig["id"], payee_type=r["type"], payee_id=r["id"], payee_name=r["name"],
                        role=r.get("role"), gross=gross, fee=fee, method=method, paid_date=paid_date,
                        eligible_1099=eligible, notes=note
                    )
                    st.success("Added.")

with colR:
    st.subheader("Current Payments")
    if not payments:
        st.info("No payments recorded yet.")
    else:
        for p in payments:
            st.write(f"**{p['payee_type']}** — {p['payee_name']} {('('+p['role']+')') if p.get('role') else ''}")
            st.write(f"Gross {money_fmt(p['gross_amount'])} | Fee {money_fmt(p['fee_withheld'])} | Net {money_fmt(p['net_amount'])}")
            st.write(f"{p.get('method') or ''} · Paid {p.get('paid_date') or ''}")
            if st.button("Delete", key=f"del_{p['id']}"):
                delete_payment_row(p["id"])
                st.warning("Deleted.")

st.divider()
left, right = st.columns(2)
if left.button("Save as Draft"):
    mark_closeout_status(gig["id"], status="draft")
    st.success("Saved as Draft")

if right.button("Mark Closed"):
    mark_closeout_status(gig["id"], status="closed")
    st.success("Gig marked Closed (payments locked).")
