# pages/02_Schedule_View.py
import os
import pandas as pd
import streamlit as st
from supabase import create_client, Client

st.title("📅 Schedule View")

# widen content area to reduce horizontal scrolling
st.markdown("<style>.block-container{max-width:1400px;}</style>", unsafe_allow_html=True)

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

# Reattach session so RLS treats us as 'authenticated'
if st.session_state.get("sb_access_token") and st.session_state.get("sb_refresh_token"):
    try:
        sb.auth.set_session(
            access_token=st.session_state["sb_access_token"],
            refresh_token=st.session_state["sb_refresh_token"],
        )
    except Exception as e:
        st.warning(f"Could not attach session; showing public data only. ({e})")

# --- Filters ---
colf1, colf2 = st.columns([1, 1])
with colf1:
    status_filter = st.multiselect(
        "Contract status", ["Pending", "Hold", "Confirmed"],
        default=["Pending", "Hold", "Confirmed"],
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

# --- TEMP DEBUG: see what columns we have ---
with st.expander("🔎 Debug (temporary)"):
    st.write("gigs columns:", list(gigs.columns))
    st.write("sample sound_tech_id:", (gigs.get("sound_tech_id") or [])[:5])
    try:
        _preview = sb.table("sound_techs").select("id, display_name, company").limit(3).execute().data
        import pandas as _p
        st.write("sound_techs sample rows:", _p.DataFrame(_preview))
    except Exception as _e:
        st.write("sound_techs query error:", _e)


# --- Normalize types BEFORE filtering ---
if "event_date" in gigs.columns:
    gigs["event_date"] = pd.to_datetime(gigs["event_date"], errors="coerce").dt.date

# keep raw dt for formatting later (don’t break these lines)
if "start_time" in gigs.columns:
    gigs["_start_dt"] = pd.to_datetime(gigs["start_time"], errors="coerce")
else:
    gigs["_start_dt"] = pd.NaT

if "end_time" in gigs.columns:
    gigs["_end_dt"] = pd.to_datetime(gigs["end_time"], errors="coerce")
else:
    gigs["_end_dt"] = pd.NaT

# pretty updated_at (safe even if tz-aware/na)
if "updated_at" in gigs.columns:
    upd = pd.to_datetime(gigs["updated_at"], errors="coerce")
    # drop tz for display if present
    try:
        upd = upd.dt.tz_convert(None)
    except Exception:
        try:
            upd = upd.dt.tz_localize(None)
        except Exception:
            pass
    gigs["updated_at"] = upd.dt.strftime("%Y-%m-%d %H:%M")

# normalize status capitalization
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
    gigs.drop(columns=[c for c in ["id_venue"] if c in gigs.columns], inplace=True, errors="ignore")

# --- Join sound techs (simple + certain) ---
try:
    techs = sb.table("sound_techs").select("id, display_name, company").execute().data or []
except Exception:
    techs = []

if techs and "sound_tech_id" in gigs.columns:
    tdf = pd.DataFrame(techs).rename(columns={"id": "sound_tech_id"})
    gigs = gigs.merge(tdf, how="left", on="sound_tech_id", suffixes=("", "_tech"))

    def _mk_sound_tech(row):
        dn = row.get("display_name")
        co = row.get("company")
        if pd.notna(dn) and str(dn).strip():
            return f"{dn} ({co})" if pd.notna(co) and str(co).strip() else str(dn).strip()
        if pd.notna(co) and str(co).strip():
            return str(co).strip()
        stid = row.get("sound_tech_id")
        return f"{stid[:8]}…" if isinstance(stid, str) else None

    gigs["sound_tech"] = gigs.apply(_mk_sound_tech, axis=1)
    gigs.drop(columns=[c for c in ["display_name","company","id_tech"] if c in gigs.columns],
              inplace=True, errors="ignore")

# --- Apply filters ---
if "contract_status" in gigs.columns:
    gigs = gigs[gigs["contract_status"].isin(status_filter)]
if upcoming_only and "event_date" in gigs.columns:
    gigs = gigs[gigs["event_date"] >= pd.Timestamp.today().date()]

# --- De-duplicate by gig id ---
if "id" in gigs.columns:
    gigs = gigs.drop_duplicates(subset=["id"])

# --- Pretty times (12-hour) ---
def _fmt_time(series):
    try:
        out = pd.to_datetime(series, errors="coerce").dt.strftime("%I:%M %p")
        return out.str.lstrip("0")  # 09:00 PM -> 9:00 PM
    except Exception:
        return series

if "_start_dt" in gigs.columns:
    gigs["start_time"] = _fmt_time(gigs["_start_dt"])
if "_end_dt" in gigs.columns:
    gigs["end_time"] = _fmt_time(gigs["_end_dt"])

# --- Display: single, wide table; hide IDs ---
hide_cols = ["id","venue_id","created_at","sound_tech_id","agent_id","_start_dt","_end_dt"]
disp_cols = [c for c in gigs.columns if c not in hide_cols]

preferred = ["event_date","start_time","end_time","venue_name","city","sound_tech",
             "contract_status","fee","title","notes","updated_at"]
ordered = [c for c in preferred if c in disp_cols] + [c for c in disp_cols if c not in preferred]

if "fee" in gigs.columns:
    gigs["fee"] = pd.to_numeric(gigs["fee"], errors="coerce")

sort_cols = [c for c in ["event_date"] if c in gigs.columns]
df_show = gigs[ordered].sort_values(by=sort_cols, ascending=True) if sort_cols else gigs[ordered]

st.dataframe(df_show, use_container_width=True, hide_index=True)

# Optional summary
if "fee" in gigs.columns and not gigs.empty:
    st.metric("Total Fees (shown)", f"${gigs['fee'].fillna(0).sum():,.0f}")

