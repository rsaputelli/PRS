import os
import streamlit as st
from datetime import date

# --- Bootstrap Streamlit secrets into environment variables (safe per-key) ---
def _sec(name: str):
    try:
        return st.secrets[name]   # per-key try/except => no KeyError propagation
    except Exception:
        return None

for k in ("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_KEY"):
    v = _sec(k)
    if v and not os.environ.get(k):
        os.environ[k] = str(v)

st.set_page_config(page_title="Gig Closeout", layout="wide")

# --- Env-only guard (no st.secrets lookups here) ---
missing = []
if not os.environ.get("SUPABASE_URL"):
    missing.append("SUPABASE_URL")
if not (
    os.environ.get("SUPABASE_KEY")
    or os.environ.get("SUPABASE_ANON_KEY")
    or os.environ.get("SUPABASE_SERVICE_KEY")
):
    missing.append("SUPABASE_KEY/ANON/SERVICE")
if missing:
    st.error("Missing configuration: " + ", ".join(missing))
    st.stop()

# Import AFTER env is populated so utils can read it
from lib.closeout_utils import (
    fetch_open_or_draft_gigs, fetch_closeout_bundle, upsert_payment_row,
    delete_payment_row, mark_closeout_status, money_fmt,
)

if st.sidebar.checkbox("Show secrets debug", value=False):
    def _present(k: str) -> bool: return bool(os.environ.get(k) or _sec(k))
    st.sidebar.write({
        "SUPABASE_URL": _present("SUPABASE_URL"),
        "USING_KEY": (
            "SERVICE" if os.environ.get("SUPABASE_SERVICE_KEY") else
            "KEY" if os.environ.get("SUPABASE_KEY") else
            "ANON" if os.environ.get("SUPABASE_ANON_KEY") else
            "NONE"
        )
    })

st.title("Gig Closeout")

mode = st.radio("Mode", ["Draft", "Closed"], horizontal=True, label_visibility="collapsed", key="closeout_mode")
status_target = "draft" if mode == "Draft" else "closed"

gigs = fetch_open_or_draft_gigs()
gig_opt = st.selectbox(
    "Select gig",
    options=gigs,
    format_func=lambda g: f"{g.get('event_date','?')} — {g.get('title','?')}{(' @ '+g.get('venue_name','')) if g.get('venue_name') else ''}"
)

if not gig_opt:
    st.info("Choose a gig to begin.")
    st.stop()

gig, roster, payments = fetch_closeout_bundle(gig_opt["id"])

colL, colR = st.columns([2, 1], gap="large")

with colL:
    st.subheader("Venue Receipt")
    _default_paid = gig.get("final_venue_paid_date")
    if isinstance(_default_paid, str):
        try:
            _default_paid = date.fromisoformat(_default_paid)
        except Exception:
            _default_paid = None
    with st.form("venue_receipt"):
        venue_paid = st.number_input("Final Gross Received from Venue", min_value=0.0, step=50.0, value=float(gig.get("final_venue_gross") or 0))
        venue_date = st.date_input("Venue Paid Date", value=_default_paid or date.today())
        notes = st.text_area("Closeout Notes", value=gig.get("closeout_notes") or "")
        if st.form_submit_button("Save Venue Closeout"):
            mark_closeout_status(gig["id"], status="draft", final_venue_gross=venue_paid, final_venue_paid_date=venue_date, closeout_notes=notes)
            st.success("Saved.")
            st.rerun()
            
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
                    st.rerun()
with colR:
    st.subheader("Current Payments")
    if not payments:
        st.info("No payments recorded yet.")
    else:
        for p in payments:
            kind = p.get("kind") or "payment"
            name = p.get("payee_name") or ""
            role = p.get("role") or ""
            gross = p.get("amount") or 0
            fee = p.get("fee_withheld") or 0
            net = p.get("net_amount") if "net_amount" in p else (gross - fee)
            paid_on = p.get("paid_on") or ""

            st.write(f"**{kind}** — {name} {('('+role+')') if role else ''}")
            st.write(f"Gross {money_fmt(gross)} | Fee {money_fmt(fee)} | Net {money_fmt(net)}")
            st.write(f"{p.get('method') or ''} · Paid {paid_on}")
            if st.button("Delete", key=f"del_{p['id']}"):
                delete_payment_row(p["id"])
                st.warning("Deleted.")
                st.rerun()  # refresh the list immediately
                
st.divider()
left, right = st.columns(2)
if left.button("Save as Draft"):
    mark_closeout_status(gig["id"], status="draft")
    st.success("Saved as Draft")

if right.button("Mark Closed"):
    mark_closeout_status(gig["id"], status="closed")
    st.success("Gig marked Closed (payments locked).")
