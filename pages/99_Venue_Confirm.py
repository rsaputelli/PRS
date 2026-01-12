##--- pages/99_Venue_Confirm.py

import datetime as dt
import streamlit as st
from supabase import create_client
from pathlib import Path

SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_SERVICE_ROLE"]

sb = create_client(SUPABASE_URL, SUPABASE_KEY)

st.set_page_config(page_title="Venue Confirmation", page_icon="✅")

token = st.query_params.get("token")

# --- PRS Logo ---
logo_path = Path(__file__).parents[1] / "assets" / "prs_logo.png"
if logo_path.exists():
    st.image(str(logo_path), width=200)

st.title("Venue Booking Confirmation")

if not token:
    st.error("Invalid or missing confirmation link.")
    st.stop()

vc = (
    sb.table("gig_confirmations")
    .select("id, gig_id, confirmed_at")
    .eq("token", token)
    .maybe_single()
    .execute()
    .data
)

if not vc:
    st.error("This confirmation link is not valid.")
    st.stop()

if vc.get("confirmed_at"):
    st.success("✅ This booking has been confirmed.")
    st.write("Thank you — no further action is needed.")
    st.stop()

sb.table("gig_confirmations").update({
    "confirmed_at": dt.datetime.utcnow().isoformat(),
    "confirmation_method": "link",
}).eq("id", vc["id"]).execute()

st.success("🎉 Thank you! Your booking is now confirmed.")
st.write("You may close this window.")
