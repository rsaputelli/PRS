import streamlit as st

st.title("📅 Schedule View")

if "user" not in st.session_state or not st.session_state["user"]:
    st.error("Please sign in first from the Login page.")
    st.stop()

st.write("This is where the gig schedule will appear.")
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
colf1, colf2 = st.columns([1,1])
with colf1:
    status_filter = st.multiselect("Status", ["Pending","Hold","Confirmed"],
                                   default=["Pending","Hold","Confirmed"])
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
venues_data = sb.table("venues").select("id,name,city").execute().data or_
