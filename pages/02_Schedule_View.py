# pages/02_Schedule_View.py
import os
import pandas as pd
import streamlit as st
from supabase import create_client, Client

st.title("📅 Schedule View")

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

# --- Reattach Supabase session so RLS treats us as authenticated ---
if st.session_state.get("sb_access_token") and st.session_state.get("sb_refresh_token"):
    try:
        sb.auth.set_session(
            access_token=st.session_state["sb_access_token"],
            refresh_token=st.session_state["sb_refresh_token"],
        )
    except Exception as e:
        st.warning(f"Could not attach session; showing public data only. ({e})")

# --- Filters (use your schema: contract_status, event_date) ---
colf1, colf2 = st.columns([1, 1])
with colf1:
    status_filter = st.multiselect(
        "Contract status", ["Pending", "Hold", "Confirmed"],
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

# --- Normalize types BEFORE filtering ---
if "event_date" in gigs.columns:
    gigs["event_date"] = pd.to_datetime(gigs["event_date"], errors="coerce").dt.date
if "start_time" in gigs.columns:
    gigs["start_time"] = pd.to_datetime(gigs["start_time"], errors="coerce").dt.time
if "contract_status" in gigs.columns and gigs["contract_status"].dtype == object:
    gigs["contract_status"] = gigs["contract_status"].astype(str).str.strip().str.title()

# --- Join venues (optional) ---
venues_data = sb.table("venues").select("id,name,city").execute().data or []
if venues_data:
    vdf = pd.DataFrame(venues_data)
    gigs = gigs.merge(vdf, how="left", left_on="venue_id", right_on="id", suffixes=("", "_venue"))
    gigs.rename(columns={"name": "venue_name"}, inplace=True)

# --- Apply filters ---
if "contract_status" in gigs.columns:
    gigs = gigs[gigs["contract_status"].isin(status_filter)]
if upcoming_only and "event_date" in gigs.columns:
    gigs = gigs[gigs["event_date"] >= pd.Timestamp.today().date()]

# --- Display ---
cols_hide = [c for c in ["id","venue_id","created_at","id_venue"] if c in gigs.columns]
disp_cols = [c for c in gigs.columns if c not in cols_hide]
if "fee" in disp_cols:
    gigs["fee"] = pd.to_numeric(gigs["fee"], errors="coerce")

sort_cols = [c for c in ["event_date","start_time"] if c in gigs.columns]
st.dataframe(
    gigs[disp_cols].sort_values(by=sort_cols, ascending=True) if sort_cols else gigs[disp_cols],
    use_container_width=True, hide_index=True
)


# --- Display ---
cols_hide = [c for c in ["id","venue_id","created_at","id_venue"] if c in gigs.columns]
disp_cols = [c for c in gigs.columns if c not in cols_hide]

if "fee" in disp_cols:
    gigs["fee"] = pd.to_numeric(gigs["fee"], errors="coerce")

sort_cols = [c for c in ["event_date","start_time"] if c in gigs.columns]
st.dataframe(
    gigs[disp_cols].sort_values(by=sort_cols, ascending=True) if sort_cols else gigs[disp_cols],
    use_container_width=True,
    hide_index=True
)

# --- Summary ---
if "fee" in gigs.columns and not gigs.empty:
    st.metric("Total Fees (shown)", f"${gigs['fee'].fillna(0).sum():,.0f}")
if "contract_status" in gigs.columns and not gigs.empty:
    st.bar_chart(gigs["contract_status"].value_counts())

