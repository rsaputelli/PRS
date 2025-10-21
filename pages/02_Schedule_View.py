# pages/02_Schedule_View.py
import os
import pandas as pd
import streamlit as st
from supabase import create_client, Client

st.title("📅 Schedule View")
st.markdown("<style>.block-container{max-width:1400px;}</style>", unsafe_allow_html=True)

# --- Supabase helper ---
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

# --- Auth ---
if "user" not in st.session_state or not st.session_state["user"]:
    st.error("Please sign in from the Login page.")
    st.stop()

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

# --- Helper: safe select to DataFrame ---
def _select_df(table: str, select: str = "*", where_eq: dict | None = None, limit: int | None = None) -> pd.DataFrame:
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

# --- Fetch gigs ---
gigs_df = _select_df("gigs", "*")
if gigs_df.empty:
    st.info("No gigs found.")
    st.stop()
gigs = gigs_df.copy()

# --- Normalize types ---
if "event_date" in gigs.columns:
    gigs["event_date"] = pd.to_datetime(gigs["event_date"], errors="coerce").dt.date

def _series_from(colname: str):
    if colname in gigs.columns:
        return pd.to_datetime(gigs[colname], errors="coerce")
    return pd.Series([pd.NaT] * len(gigs), index=gigs.index, dtype="datetime64[ns]")

gigs["_start_dt"] = _series_from("start_time")
gigs["_end_dt"] = _series_from("end_time")

# --- Join sound techs ---
techs = _select_df("sound_techs", "id, display_name, company")
if not techs.empty and "sound_tech_id" in gigs.columns:
    gigs["sound_tech_id"] = gigs["sound_tech_id"].astype(str)
    techs["id"] = techs["id"].astype(str)
    techs = techs.rename(columns={"id": "sound_tech_id"})
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
    for c in ["display_name", "company"]:
        if c in gigs.columns:
            gigs.drop(columns=[c], inplace=True)

# --- Formatting helpers ---
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
        return str(d)

# --- Pretty columns ---
gigs["Date"] = gigs["event_date"].apply(_fmt_date) if "event_date" in gigs.columns else ""
gigs["Time"] = gigs.apply(lambda r: f"{_fmt_time(r.get('_start_dt'))} – {_fmt_time(r.get('_end_dt'))}".strip(" –"), axis=1)

# --- Location ---
loc_candidates = [k for k in ["venue", "venue_name", "location"] if k in gigs.columns]
addr_bits = [k for k in ["address", "city", "state"] if k in gigs.columns]
def _mk_loc(r):
    parts = []
    for k in loc_candidates:
        v = r.get(k)
        if pd.notna(v) and str(v).strip():
            parts.append(str(v).strip())
            break
    for k in addr_bits:
        v = r.get(k)
        if pd.notna(v) and str(v).strip():
            parts.append(str(v).strip())
    return ", ".join(parts)
gigs["Location"] = gigs.apply(_mk_loc, axis=1)

# --- Venue (safe, no display_name dependency) ---
def _first_nonempty(row, keys):
    for k in keys:
        if k in row.index:
            v = row.get(k)
            if pd.notna(v) and str(v).strip():
                return str(v).strip()
    return ""

text_keys = [k for k in ["venue", "venue_name", "location"] if k in gigs.columns]
gigs["Venue"] = gigs.apply(lambda r: _first_nonempty(r, text_keys), axis=1)

if ("venue_id" in gigs.columns) and (gigs["Venue"].eq("").any()):
    vdf = _select_df("venues", "id, name")  # no display_name
    if not vdf.empty:
        vdf = vdf.rename(columns={"id": "venue_id"})
        gigs["venue_id"] = gigs["venue_id"].astype(str)
        vdf["venue_id"] = vdf["venue_id"].astype(str)
        gigs = gigs.merge(vdf, how="left", on="venue_id", suffixes=("", "_venue"))
        if "name" in gigs.columns:
            gigs["Venue"] = gigs["Venue"].where(gigs["Venue"].str.len().fillna(0) > 0, gigs["name"].astype(str))

# --- Fee ---
if "fee" not in gigs.columns:
    gigs["fee"] = pd.NA
else:
    gigs["fee"] = pd.to_numeric(gigs["fee"], errors="coerce")

# --- Filters ---
if "contract_status" in gigs.columns and status_filter:
    gigs = gigs[gigs["contract_status"].isin(status_filter)]
if upcoming_only and "event_date" in gigs.columns:
    gigs = gigs[gigs["event_date"] >= pd.Timestamp.today().date()]
if search_txt.strip():
    s = search_txt.strip().lower()
    def _row_has_text(r):
        for k in ("title", "notes", "Location", "Venue", "band_name", "sound_tech"):
            if k in r.index:
                val = r.get(k)
                if pd.notna(val) and s in str(val).lower():
                    return True
        return False
    gigs = gigs[gigs.apply(_row_has_text, axis=1)]

if "id" in gigs.columns:
    gigs = gigs.drop_duplicates(subset=["id"])

# --- Display columns ---
hide_cols = set(
    [c for c in gigs.columns if c.endswith("_id")]
    + [c for c in ["id", "created_at", "updated_at", "event_date", "start_time", "end_time",
                   "_start_dt", "_end_dt", "address", "city", "state", "venue", "venue_name", "location"]
       if c in gigs.columns]
)
disp_cols = [c for c in gigs.columns if c not in hide_cols]
preferred = [
    "Date", "Time",
    "title" if "title" in gigs.columns else None,
    "band_name" if "band_name" in gigs.columns else None,
    "Venue", "Location",
    "contract_status" if "contract_status" in gigs.columns else None,
    "fee", "sound_tech" if "sound_tech" in gigs.columns else None,
    "notes" if "notes" in gigs.columns else None,
]
preferred = [c for c in preferred if c]
ordered = [c for c in preferred if c in disp_cols]

df_show = gigs[ordered] if ordered else gigs[disp_cols]
sort_keys = [c for c in ["event_date", "_start_dt"] if c in df_show.columns]
if sort_keys:
    df_show = df_show.sort_values(by=sort_keys, ascending=True)
else:
    sort_keys_alt = [c for c in ["event_date", "_start_dt"] if c in gigs.columns]
    if sort_keys_alt:
        gigs_sorted = gigs.sort_values(by=sort_keys_alt, ascending=True)
        df_show = gigs_sorted[df_show.columns]

st.dataframe(df_show, use_container_width=True, hide_index=True)

# (Removed Total Fees metric per request)

