# pages/02_Schedule_View.py
import os
import pandas as pd
import streamlit as st
from supabase import create_client, Client

st.title("📅 Schedule View")

# widen the content area a bit (helps reduce horizontal scrolling)
st.markdown(
    "<style>.block-container{max-width: 1400px;}</style>",
    unsafe_allow_html=True,
)

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

# --- Reattach session so RLS = authenticated ---
if st.session_state.get("sb_access_token") and st.session_state.get("sb_refresh_token"):
    try:
        sb.auth.set_session(
            access_token=st.session_state["sb_access_token"],
            refresh_token=st.session_state["sb_refresh_token"],
        )
    except Exception as e:
        st.warning(f"Could not attach session; showing public data only. ({e})")

# --- Filters (use your schema names) ---
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
if "end_time" in gigs.columns:
    gigs["end_time"] = pd.to_datetime(gigs["end_time"], errors="coerce").dt.time
# Pretty updated_at (keep as string for nicer display)
if "updated_at" in gigs.columns:
    gigs["updated_at"] = (
        pd.to_datetime(gigs["updated_at"], errors="coerce")
          .dt.tz_convert(None)  # drop timezone for display, if present
          .dt.strftime("%Y-%m-%d %H:%M")
    )

if "contract_status" in gigs.columns and gigs["contract_status"].dtype == object:
    gigs["contract_status"] = gigs["contract_status"].astype(str).str.strip().str.title()


# --- Join venues (name, city) ---
venues_data = sb.table("venues").select("id,name,city").execute().data or []
if venues_data:
    vdf = pd.DataFrame(venues_data)
    gigs = gigs.merge(
        vdf, how="left", left_on="venue_id", right_on="id", suffixes=("", "_venue")
    )
    gigs.rename(columns={"name": "venue_name"}, inplace=True)
    if "id_venue" in gigs.columns:
        gigs.drop(columns=["id_venue"], inplace=True, errors="ignore")

# --- Join sound techs (uuid -> display) ---
try:
    techs_data = sb.table("sound_techs").select("id,name,full_name,company,company_name,business_name").execute().data or []
except Exception:
    techs_data = []

def _first_nonnull(row, cols):
    for c in cols:
        val = row.get(c)
        if pd.notna(val) and str(val).strip() != "":
            return str(val).strip()
    return None

if techs_data and "sound_tech_id" in gigs.columns:
    tdf = pd.DataFrame(techs_data)
    gigs = gigs.merge(
        tdf, how="left", left_on="sound_tech_id", right_on="id", suffixes=("", "_tech")
    )
    # Build a friendly display like "Alex Smith (AudioCo)"
    name_cols    = [c for c in ["name", "full_name"] if c in gigs.columns]
    company_cols = [c for c in ["company", "company_name", "business_name"] if c in gigs.columns]
    if name_cols or company_cols:
        gigs["sound_tech"] = gigs.apply(
            lambda r: (
                f"{_first_nonnull(r, name_cols)} ({_first_nonnull(r, company_cols)})"
                if _first_nonnull(r, name_cols) and _first_nonnull(r, company_cols)
                else (_first_nonnull(r, name_cols) or _first_nonnull(r, company_cols))
            ),
            axis=1,
        )
    # Drop raw columns we don't want to show
    gigs.drop(columns=[c for c in ["id_tech","name","full_name","company","company_name","business_name"] if c in gigs.columns],
              inplace=True, errors="ignore")


# --- Apply filters ---
if "contract_status" in gigs.columns:
    gigs = gigs[gigs["contract_status"].isin(status_filter)]
if upcoming_only and "event_date" in gigs.columns:
    gigs = gigs[gigs["event_date"] >= pd.Timestamp.today().date()]

# --- De-duplicate by gig id (protect against multi-joins) ---
if "id" in gigs.columns:
    gigs = gigs.drop_duplicates(subset=["id"])

# --- Format & single display (no chart) ---
hide_cols = ["id", "venue_id", "created_at", "sound_tech_id", "agent_id"]
disp_cols = [c for c in gigs.columns if c not in hide_cols]

# Preferred order if present
preferred = ["event_date", "start_time", "end_time", "venue_name", "city", "sound_tech",
             "contract_status", "fee", "title", "notes"]
ordered = [c for c in preferred if c in disp_cols] + [c for c in disp_cols if c not in preferred]

# Fee numeric
if "fee" in gigs.columns:
    gigs["fee"] = pd.to_numeric(gigs["fee"], errors="coerce")

# Sort by date/time
sort_cols = [c for c in ["event_date", "start_time"] if c in gigs.columns]
df_show = gigs[ordered].sort_values(by=sort_cols, ascending=True) if sort_cols else gigs[ordered]

st.dataframe(df_show, use_container_width=True, hide_index=True)

# Optional summary (kept)
if "fee" in gigs.columns and not gigs.empty:
    st.metric("Total Fees (shown)", f"${gigs['fee'].fillna(0).sum():,.0f}")


