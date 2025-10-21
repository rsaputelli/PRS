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
        val = os.environ.get(name, default)
    if required and (val is None or str(val).strip() == ""):
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
colf1, colf2, colf3 = st.columns([1, 1, 2])
with colf1:
    status_filter = st.multiselect(
        "Contract status", ["Pending", "Hold", "Confirmed"],
        default=["Pending", "Hold", "Confirmed"],
    )
with colf2:
    upcoming_only = st.toggle("Upcoming only", value=True)
with colf3:
    search_txt = st.text_input("Search (title/venue/notes)", "")

# --- Helper: safe Supabase select to DataFrame ---
def _select_df(table: str, select: str, where_eq: dict | None = None, limit: int | None = None) -> pd.DataFrame:
    try:
        q = sb.table(table).select(select)
        if where_eq:
            for k, v in where_eq.items():
                q = q.eq(k, v)
        if limit:
            q = q.limit(limit)
        data = q.execute().data or []
        return pd.DataFrame(data)
    except Exception as e:
        st.warning(f"{table} query failed: {e}")
        return pd.DataFrame()

# --- Fetch gigs (keep fields broad so we don't paint ourselves into a corner) ---
gigs_cols = ",".join([
    "id",
    "title",
    "event_date",
    "start_time",
    "end_time",
    "venue",
    "address",
    "city",
    "state",
    "fee",
    "notes",
    "contract_status",
    "sound_tech_id",
    "band_name",
])
gigs_df = _select_df("gigs", gigs_cols)

if gigs_df.empty:
    st.info("No gigs found.")
    st.stop()

gigs = gigs_df.copy()

# --- TEMP DEBUG: see what columns we have (safe; no Series truthiness) ---
with st.expander("🔎 Debug (temporary)"):
    st.write("gigs columns:", list(gigs.columns))
    sample_ids = gigs["sound_tech_id"].head(5).astype(str).tolist() if "sound_tech_id" in gigs.columns else []
    st.write("sample sound_tech_id:", sample_ids)
    _tech_preview = _select_df("sound_techs", "id, display_name, company", limit=3)
    if not _tech_preview.empty:
        st.write("sound_techs sample rows:", _tech_preview)

# --- Normalize types BEFORE filtering ---
if "event_date" in gigs.columns:
    gigs["event_date"] = pd.to_datetime(gigs["event_date"], errors="coerce").dt.date

# preserve dt for later formatting
gigs["_start_dt"] = pd.to_datetime(gigs["start_time"], errors="coerce") if "start_time" in gigs.columns else pd.NaT
gigs["_end_dt"]   = pd.to_datetime(gigs["end_time"], errors="coerce") if "end_time"   in gigs.columns else pd.NaT

# --- Join sound techs (type-normalized) ---
techs = _select_df("sound_techs", "id, display_name, company")
if not techs.empty and "sound_tech_id" in gigs.columns:
    # ensure string on both sides for join
    gigs["sound_tech_id"] = gigs["sound_tech_id"].astype(str)
    techs = techs.rename(columns={"id": "sound_tech_id"}).copy()
    techs["sound_tech_id"] = techs["sound_tech_id"].astype(str)

    gigs = gigs.merge(techs, how="left", on="sound_tech_id", suffixes=("", "_tech"))

    def _mk_sound_tech(row):
        dn = row.get("display_name")
        co = row.get("company")
        if pd.notna(dn) and str(dn).strip():
            return f"{dn} ({co})" if pd.notna(co) and str(co).strip() else str(dn).strip()
        if pd.notna(co) and str(co).strip():
            return str(co).strip()
        stid = row.get("sound_tech_id")
        return f"{stid[:8]}…" if isinstance(stid, str) and len(stid) >= 8 else (stid or None)

    gigs["sound_tech"] = gigs.apply(_mk_sound_tech, axis=1)
    # tidy columns—leave the joined fields out of the display
    for c in ["display_name", "company"]:
        if c in gigs.columns:
            gigs.drop(columns=[c], inplace=True)

# --- Quick display helpers ---
def _fmt_time(dt):
    if pd.isna(dt):
        return ""
    try:
        return pd.to_datetime(dt).strftime("%-I:%M %p")
    except Exception:
        return ""

def _fmt_date(d):
    if pd.isna(d):
        return ""
    try:
        return pd.to_datetime(d).strftime("%a %b %-d, %Y")
    except Exception:
        try:
            # if already date
            return d.strftime("%a %b %-d, %Y")
        except Exception:
            return str(d)

# --- Derived / pretty columns ---
gigs["Date"] = gigs["event_date"].apply(_fmt_date) if "event_date" in gigs.columns else ""
gigs["Time"] = gigs.apply(lambda r: f"{_fmt_time(r.get('_start_dt'))} – {_fmt_time(r.get('_end_dt'))}".strip(" –"),
                          axis=1)
gigs["Location"] = gigs.apply(
    lambda r: ", ".join([str(x) for x in [r.get("venue"), r.get("city"), r.get("state")] if pd.notna(x) and str(x).strip()]),
    axis=1,
)

# --- Apply filters ---
if "contract_status" in gigs.columns and status_filter:
    gigs = gigs[gigs["contract_status"].isin(status_filter)]

if upcoming_only and "event_date" in gigs.columns:
    gigs = gigs[gigs["event_date"] >= pd.Timestamp.today().date()]

# Text search across a few fields
if search_txt.strip():
    s = search_txt.strip().lower()
    def _row_has_text(r):
        for k in ("title", "venue", "notes", "Location", "band_name", "sound_tech"):
            val = r.get(k)
            if pd.notna(val) and s in str(val).lower():
                return True
        return False
    gigs = gigs[gigs.apply(_row_has_text, axis=1)]

# --- De-duplicate by gig id ---
if "id" in gigs.columns:
    gigs = gigs.drop_duplicates(subset=["id"])

# --- Choose display columns (prefer these if available) ---
preferred = [
    "Date", "Time", "title", "band_name", "Location",
    "contract_status", "fee", "sound_tech", "notes",
]
disp_cols = [c for c in gigs.columns if c not in ("_start_dt", "_end_dt")]
ordered = [c for c in preferred if c in disp_cols] + [c for c in disp_cols if c not in preferred]

# Fee numeric + format
if "fee" in gigs.columns:
    gigs["fee"] = pd.to_numeric(gigs["fee"], errors="coerce")

# --- Final table ---
sort_cols = [c for c in ["event_date", "_start_dt"] if c in gigs.columns]
df_show = gigs[ordered].sort_values(by=sort_cols, ascending=True) if sort_cols else gigs[ordered]

st.dataframe(df_show, use_container_width=True, hide_index=True)

# --- Optional summary ---
if "fee" in gigs.columns and not gigs.empty:
    st.metric("Total Fees (shown)", f"${gigs['fee'].fillna(0).sum():,.0f}")
