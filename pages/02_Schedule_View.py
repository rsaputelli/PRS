# pages/02_Schedule_View.py
import os
import pandas as pd
import streamlit as st
from supabase import create_client, Client

st.title("📅 Schedule View")

# --- Supabase helper (secrets → env) ---
def _get_secret(name, default=None, required=False):
    if hasattr(st, "secrets") and name in st.secrets:
        val = st.secrets[name]
    else:
        val = os.getenv(name, default)
    if required and (val is None or str(val).strip() == ""):
        st.error(f"Missing required setting: {name}")
        st.stop()
    return val

SUPABASE_URL = _get_secret("SUPABASE_URL", required=True)
SUPABASE_ANON_KEY = _get_secret("SUPABASE_ANON_KEY", required=True)
sb: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# --- Auth gate ---
if "user" not in st.session_state or not st.session_state["user"]:
    st.error("Please sign in from the Login page.")
    st.stop()

# --- Filters ---
colf1, colf2 = st.columns([1, 1])
with colf1:
    status_filter = st.multiselect(
        "Status", ["Pending", "Hold", "Confirmed"],
        default=["Pending", "Hold", "Confirmed"]
    )
with colf2:
    upcoming_only = st.toggle("Upcoming only", value=True)

# --- Fetch gigs ---
with st.spinner("Loading gigs..."):
    gigs_data = sb.table("gigs").select("*").execute().data or []

if not gigs_data:
    st.info("No gigs found.")
    st.stop()

gigs = pd.DataFrame(gigs_data)

# --- Join venues (name, city) if available ---
venues_data = sb.table("venues").select("id,name,city").execute().data or []
if venues_data:
    vdf = pd.DataFrame(venues_data)
    gigs = gigs.merge(
        vdf, how="left", left_on="venue_id", right_on="id", suffixes=("", "_venue")
    )
    gigs.rename(columns={"name": "venue_name"}, inplace=True)

# --- Apply filters ---
if "status" in gigs.columns:
    gigs = gigs[gigs["status"].isin(status_filter)]
if upcoming_only and "date" in gigs.columns:
    gigs = gigs[gigs["date"] >= pd.Timestamp.today().date()]

# --- Display ---
cols_hide = [c for c in ["id", "venue_id", "created_at", "id_venue"] if c in gigs.columns]
disp_cols = [c for c in gigs.columns if c not in cols_hide]
if "fee" in disp_cols:
    gigs["fee"] = pd.to_numeric(gigs["fee"], errors="coerce")

sort_cols = [c for c in ["date", "start_time"] if c in gigs.columns]
st.dataframe(
    gigs[disp_cols].sort_values(by=sort_cols, ascending=True) if sort_cols else gigs[disp_cols],
    use_container_width=True,
    hide_index=True,
)

# --- Summary ---
if "fee" in gigs.columns and not gigs.empty:
    st.metric("Total Fees (shown)", f"${gigs['fee'].fillna(0).sum():,.0f}")
if "status" in gigs.columns and not gigs.empty:
    st.bar_chart(gigs["status"].value_counts())
