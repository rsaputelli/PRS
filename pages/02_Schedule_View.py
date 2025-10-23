# pages/02_Schedule_View.py
import os
from datetime import datetime, date, time, timedelta
import pandas as pd
import streamlit as st
from supabase import create_client, Client
from pathlib import Path
import streamlit as st

# --- Header with logo + title ---
logo_path = Path(__file__).parent.parent / "assets" / "prs_logo.png"

hdr1, hdr2 = st.columns([0.12, 0.88])
with hdr1:
    if logo_path.exists():
        st.image(str(logo_path), use_container_width=True)
with hdr2:
    st.markdown(
        "<h1 style='margin-bottom:0'>Schedule View</h1>",
        unsafe_allow_html=True
    )
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

# --- Normalize core types ---
if "event_date" in gigs.columns:
    gigs["event_date"] = pd.to_datetime(gigs["event_date"], errors="coerce").dt.date

def _to_time_obj(x) -> time | None:
    """Parse 'HH:MM[:SS]' or datetime/time-like into a time, or None."""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    try:
        # handle 'HH:MM:SS'/'HH:MM', pandas Timestamps, or Python time
        if isinstance(x, time):
            return x
        ts = pd.to_datetime(x, errors="coerce")
        if pd.isna(ts):
            return None
        return ts.time()
    except Exception:
        return None

def _compose_span(row):
    """Combine event_date + start/end time; roll end to next day if needed."""
    d = row.get("event_date")
    st_raw = row.get("start_time")
    en_raw = row.get("end_time")
    if pd.isna(d):
        return (pd.NaT, pd.NaT)

    d: date = d
    st_t = _to_time_obj(st_raw) or time(0, 0)
    en_t = _to_time_obj(en_raw) or st_t

    start_dt = datetime.combine(d, st_t)
    end_dt = datetime.combine(d, en_t)
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)
    return (pd.Timestamp(start_dt), pd.Timestamp(end_dt))

# build computed datetimes
spans = gigs.apply(_compose_span, axis=1, result_type="expand")
gigs["_start_dt"] = pd.to_datetime(spans[0], errors="coerce")
gigs["_end_dt"]   = pd.to_datetime(spans[1], errors="coerce")

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

# --- Formatters ---
def _fmt_time12(dt: pd.Timestamp) -> str:
    if pd.isna(dt):
        return ""
    try:
        return dt.strftime("%-I:%M %p")
    except Exception:
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

def _time_span_label(row) -> str:
    s = row.get("_start_dt")
    e = row.get("_end_dt")
    if pd.isna(s) and pd.isna(e):
        return ""
    s_txt = _fmt_time12(s) if not pd.isna(s) else ""
    e_txt = _fmt_time12(e) if not pd.isna(e) else ""
    # next-day hint
    next_day = (not pd.isna(s)) and (not pd.isna(e)) and (e.date() > s.date())
    hint = " (next day)" if next_day else ""
    if s_txt and e_txt:
        return f"{s_txt} – {e_txt}{hint}"
    return s_txt or e_txt

gigs["Time"] = gigs.apply(_time_span_label, axis=1)

# --- Venue (prefer text on gigs; fallback to venues.name via venue_id) ---
def _first_nonempty(row, keys):
    for k in keys:
        if k in row.index:
            v = row.get(k)
            if pd.notna(v) and str(v).strip():
                return str(v).strip()
    return ""

text_keys = [k for k in ["venue", "venue_name", "location"] if k in gigs.columns]
gigs["Venue"] = gigs.apply(lambda r: _first_nonempty(r, text_keys), axis=1)

# Pull venue name + address fields; rename to *_venue so they don't collide
if "venue_id" in gigs.columns:
    vdf = _select_df("venues", "id, name, address_line1, address_line2, city, state, postal_code")
    if not vdf.empty:
        vdf = vdf.rename(columns={
            "id": "venue_id",
            "name": "venue_name_text",
            "address_line1": "address_line1_venue",
            "address_line2": "address_line2_venue",
            "city": "city_venue",
            "state": "state_venue",
            "postal_code": "postal_code_venue",
        })
        # normalize ids as strings for join
        gigs["venue_id"] = gigs["venue_id"].astype(str)
        vdf["venue_id"] = vdf["venue_id"].astype(str)
        gigs = gigs.merge(vdf, how="left", on="venue_id")

        # If Venue still blank, fill from venue name
        if "venue_name_text" in gigs.columns:
            gigs["Venue"] = gigs["Venue"].where(
                gigs["Venue"].astype(str).str.strip().ne(""),
                gigs["venue_name_text"].astype(str)
            )

# --- Location: prefer gig address fields; then fall back to venue address fields ---
def _mk_address_block(row, prefix=""):
    # prefix "" -> gig fields; "_venue" -> venue fields
    def val(k):
        key = f"{k}{prefix}"
        if key in row.index:
            v = row.get(key)
            if pd.notna(v) and str(v).strip():
                return str(v).strip()
        return ""
    a1 = val("address_line1")
    a2 = val("address_line2")
    c  = val("city")
    s  = val("state")
    z  = val("postal_code")

    if not any([a1, a2, c, s, z]):
        return ""

    parts = []
    street = ", ".join([p for p in [a1, a2] if p])
    if street:
        parts.append(street)
    city_state_zip = ""
    if c:
        city_state_zip = c
    if s or z:
        tail = " ".join([p for p in [s, z] if p])
        city_state_zip = f"{city_state_zip}, {tail}" if city_state_zip else tail
    if city_state_zip:
        parts.append(city_state_zip)
    return ", ".join(parts)

# Build Location now:
loc_from_gig   = gigs.apply(lambda r: _mk_address_block(r, ""), axis=1)
loc_from_venue = gigs.apply(lambda r: _mk_address_block(r, "_venue"), axis=1)
gigs["Location"] = loc_from_gig.where(loc_from_gig.astype(str).str.strip().ne(""), loc_from_venue)

# --- Fee ---
if "fee" not in gigs.columns:
    gigs["fee"] = pd.NA
else:
    gigs["fee"] = pd.to_numeric(gigs["fee"], errors="coerce")

# --- Filters ---
if "contract_status" in gigs.columns and status_filter:
    gigs = gigs[gigs["contract_status"].isin(status_filter)]
if upcoming_only:
    # use computed start when available; else event_date
    if "_start_dt" in gigs.columns and gigs["_start_dt"].notna().any():
        gigs = gigs[gigs["_start_dt"].dt.date >= pd.Timestamp.today().date()]
    elif "event_date" in gigs.columns:
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
    + [c for c in [
        "id", "created_at", "updated_at", "event_date", "start_time", "end_time",
        "_start_dt", "_end_dt",
        # raw text columns we don't want to show
        "venue", "venue_name", "location",
        # hide venue address columns used only for Location
        "address_line1_venue", "address_line2_venue", "city_venue", "state_venue", "postal_code_venue",
        # if gig has its own raw address fields, we still prefer the combined Location column
        "address_line1", "address_line2", "city", "state", "postal_code",
        "venue_name_text",
    ] if c in gigs.columns]
)
disp_cols = [c for c in gigs.columns if c not in hide_cols]
preferred = [
    "Date", "Time",
    "title" if "title" in gigs.columns else None,
    "band_name" if "band_name" in gigs.columns else None,
    "Venue", "Location",
    "contract_status" if "contract_status" in gigs.columns else None,
    "fee",
    "sound_tech" if "sound_tech" in gigs.columns else None,
    "notes" if "notes" in gigs.columns else None,
]
preferred = [c for c in preferred if c]
ordered = [c for c in preferred if c in disp_cols]

df_show = gigs[ordered] if ordered else gigs[disp_cols]

# --- Sort by computed start (then venue for stability) ---
sort_cols = []
if "_start_dt" in gigs.columns:
    sort_cols.append("_start_dt")
if "venue_id" in gigs.columns:
    sort_cols.append("venue_id")
if sort_cols:
    gigs_sorted = gigs.sort_values(by=sort_cols, ascending=[True] * len(sort_cols))
    df_show = gigs_sorted[df_show.columns]
else:
    # fallback: event_date
    sort_keys = [c for c in ["event_date"] if c in gigs.columns]
    if sort_keys:
        gigs_sorted = gigs.sort_values(by=sort_keys, ascending=True)
        df_show = gigs_sorted[df_show.columns]

st.dataframe(df_show, use_container_width=True, hide_index=True)


