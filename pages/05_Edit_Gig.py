# =============================
# File: pages/05_Edit_Gig.py (PARITY PATCH — auth/header order fixed)
# =============================
import os
from datetime import datetime, date, time, timedelta
from typing import Optional, Dict, List, Set

import pandas as pd
import streamlit as st
from supabase import create_client, Client
from lib.ui_header import render_header
from lib.ui_format import format_currency

# -----------------------------
# Page config (safe to do first)
# -----------------------------
st.set_page_config(page_title="Edit Gig", page_icon="✏️", layout="wide")

# -----------------------------
# Secrets / Supabase init FIRST
# -----------------------------
def _get_secret(name, default=None, required=False):
    if hasattr(st, "secrets") and name in st.secrets:
        val = st.secrets[name]
    else:
        val = os.environ.get(name, default)
    if required and (val is None or str(val).strip() == ""):
        st.error(f"Missing required secret: {name}")
        st.stop()
    return val

SUPABASE_URL = _get_secret("SUPABASE_URL", required=True)
SUPABASE_ANON_KEY = _get_secret("SUPABASE_ANON_KEY", required=True)
sb: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# Attach Supabase session for RLS if present (must happen before any header that might enforce auth)
if st.session_state.get("sb_access_token") and st.session_state.get("sb_refresh_token"):
    try:
        sb.auth.set_session(
            access_token=st.session_state["sb_access_token"],
            refresh_token=st.session_state["sb_refresh_token"],
        )
    except Exception as e:
        st.warning(f"Could not attach session; proceeding with limited access. ({e})")

# -----------------------------
# Auth gate BEFORE header
# -----------------------------
if "user" not in st.session_state or not st.session_state["user"]:
    st.error("Please sign in from the Login page.")
    st.stop()

def _norm_admin_emails(raw):
    if raw is None:
        return set()
    if isinstance(raw, (list, tuple, set)):
        return {str(x).strip().lower() for x in raw}
    return {p.strip().lower() for p in str(raw).replace(";", ",").split(",") if p.strip()}

def _get_authed_user():
    u = st.session_state.get("user")
    if u:
        return u
    try:
        gu = sb.auth.get_user()
        return gu.user if getattr(gu, "user", None) else None
    except Exception:
        return None

def _profiles_admin_lookup(user_email: str, user_id: str) -> bool:
    try:
        for col in ["id", "user_id", "auth_user_id"]:
            try:
                res = sb.table("profiles").select("*").eq(col, user_id).limit(1).execute()
                rows = res.data or []
                if rows:
                    r = rows[0]
                    if r.get("is_admin") is True or r.get("admin") is True or str(r.get("role", "")).lower() == "admin":
                        return True
            except Exception:
                pass
        if user_email:
            res = sb.table("profiles").select("*").eq("email", user_email).limit(1).execute()
            rows = res.data or []
            if rows:
                r = rows[0]
                if r.get("is_admin") is True or r.get("admin") is True or str(r.get("role", "")).lower() == "admin":
                    return True
    except Exception:
        pass
    return False

user_obj   = _get_authed_user()
user_email = (user_obj.get("email") if isinstance(user_obj, dict) else getattr(user_obj, "email", None)) or ""
user_id    = (user_obj.get("id")    if isinstance(user_obj, dict) else getattr(user_obj, "id", None)) or ""

admin_from_session  = bool(st.session_state.get("is_admin", False))
admin_from_secrets  = user_email.lower() in _norm_admin_emails(getattr(st, "secrets", {}).get("ADMIN_EMAILS"))
admin_from_profiles = _profiles_admin_lookup(user_email.lower(), user_id)

IS_ADMIN = admin_from_session or admin_from_secrets or admin_from_profiles
if not IS_ADMIN:
    st.error("Only admins may edit gigs.")
    st.stop()

# -----------------------------
# Header AFTER auth/admin gate
# -----------------------------
render_header(title="Edit Gig", emoji="✏️")

# -----------------------------
# Data helpers (cached)
# -----------------------------
@st.cache_data(ttl=60)
def _select_df(table: str, select: str = "*", where_eq: Optional[Dict] = None, limit: Optional[int] = None) -> pd.DataFrame:
    try:
        q = sb.table(table).select(select)
        if where_eq:
            for k, v in where_eq.items():
                q = q.eq(k, v)
        if limit:
            q = q.limit(limit)
        data = q.execute().data or []
        return pd.DataFrame(data)
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=60)
def _table_columns(table: str) -> Set[str]:
    df = _select_df(table, "*", limit=1)
    return set(df.columns) if not df.empty else set()

def _table_exists(table: str) -> bool:
    try:
        sb.table(table).select("*").limit(1).execute()
        return True
    except Exception:
        return False

def _filter_to_schema(table: str, data: Dict) -> Dict:
    cols = _table_columns(table)
    if not cols:
        return data
    return {k: v for k, v in data.items() if k in cols}

def _robust_update(table: str, match: Dict, patch: Dict, max_attempts: int = 8) -> bool:
    data = dict(patch)
    for _ in range(max_attempts):
        try:
            q = sb.table(table).update(data)
            for k, v in match.items():
                q = q.eq(k, v)
            q.execute()
            return True
        except Exception as e:
            msg = str(e)
            marker = "Could not find the '"
            if "PGRST204" in msg and marker in msg:
                start = msg.find(marker) + len(marker)
                end = msg.find("'", start)
                bad_col = msg[start:end] if end > start else None
                if bad_col and bad_col in data:
                    data.pop(bad_col, None)
                    continue
            st.error(f"Update {table} failed: {e}")
            return False
    st.error(f"Update {table} failed after removing unknown columns: {list(data.keys())}")
    return False

def _robust_insert(table: str, payload: Dict, max_attempts: int = 8) -> Optional[Dict]:
    data = dict(payload)
    for _ in range(max_attempts):
        try:
            res = sb.table(table).insert(data).execute()
            rows = res.data or []
            return rows[0] if rows else None
        except Exception as e:
            msg = str(e)
            marker = "Could not find the '"
            if "PGRST204" in msg and marker in msg:
                start = msg.find(marker) + len(marker)
                end = msg.find("'", start)
                bad_col = msg[start:end] if end > start else None
                if bad_col and bad_col in data:
                    data.pop(bad_col, None)
                    continue
            st.error(f"Insert into {table} failed: {e}")
            return None
    st.error(f"Insert into {table} failed after removing unknown columns: {list(data.keys())}")
    return None

# -----------------------------
# Mini-creators for "Add New ..."
# -----------------------------
def _opt_label(val, fallback=""):
    return str(val) if pd.notna(val) and str(val).strip() else fallback

def _name_for_mus_row(r: pd.Series) -> str:
    stage = _opt_label(r.get("stage_name"), "")
    if stage:
        return stage
    full = " ".join([_opt_label(r.get("first_name"), ""), _opt_label(r.get("last_name"), "")]).strip()
    return full or _opt_label(r.get("display_name"), "") or "Unnamed Musician"

def _create_agent(name: str, company: str) -> Optional[str]:
    payload = _filter_to_schema("agents", {
        "name": name or None,
        "company": company or None,
        "first_name": None,
        "last_name": None,
    })
    row = _robust_insert("agents", payload)
    return str(row["id"]) if row and "id" in row else None

def _create_musician(first_name: str, last_name: str, instrument: str, stage_name: str) -> Optional[str]:
    payload = _filter_to_schema("musicians", {
        "first_name": first_name or None,
        "last_name": last_name or None,
        "stage_name": stage_name or None,
        "instrument": instrument or None,
        "active": True if "active" in _table_columns("musicians") else None,
    })
    row = _robust_insert("musicians", payload)
    return str(row["id"]) if row and "id" in row else None

def _create_soundtech(display_name: str, company: str, phone: str, email: str) -> Optional[str]:
    payload = _filter_to_schema("sound_techs", {
        "display_name": display_name or None,
        "company": company or None,
        "phone": phone or None,
        "email": email or None,
    })
    row = _robust_insert("sound_techs", payload)
    return str(row["id"]) if row and "id" in row else None

def _create_venue(name: str, address1: str, address2: str, city: str, state: str, postal_code: str, phone: str) -> Optional[str]:
    payload = _filter_to_schema("venues", {
        "name": name or None,
        "address_line1": address1 or None,
        "address_line2": address2 or None,
        "city": city or None,
        "state": state or None,
        "postal_code": postal_code or None,
        "phone": phone or None,
    })
    row = _robust_insert("venues", payload)
    return str(row["id"]) if row and "id" in row else None

# -----------------------------
# Reference data (lazy, cached)
# -----------------------------
venues_df = _select_df("venues", "*")
sound_df  = _select_df("sound_techs", "*")
mus_df    = _select_df("musicians", "*")
agents_df = _select_df("agents", "*")

# Labels
agent_labels: Dict[str, str] = {}
if not agents_df.empty and "id" in agents_df.columns:
    for _, r in agents_df.iterrows():
        if pd.notna(r.get("name")) and str(r.get("name")).strip():
            lbl = str(r["name"]).strip()
        else:
            fn = _opt_label(r.get("first_name"), "")
            ln = _opt_label(r.get("last_name"), "")
            if fn or ln:
                nm = (fn + " " + ln).strip()
                lbl = (nm + f" ({_opt_label(r.get('company'), '')})").strip().rstrip("()")
            else:
                lbl = _opt_label(r.get("company"), "Unnamed Agent")
        agent_labels[str(r["id"])] = lbl

venue_labels: Dict[str, str] = {}
if not venues_df.empty and "id" in venues_df.columns:
    for _, r in venues_df.iterrows():
        name = _opt_label(r.get("name"), "Unnamed Venue")
        city_state = " ".join([_opt_label(r.get("city"), ""), _opt_label(r.get("state"), "")]).strip()
        lbl = f"{name}, {city_state}" if city_state else name
        venue_labels[str(r["id"])] = lbl

sound_labels: Dict[str, str] = {}
if not sound_df.empty and "id" in sound_df.columns:
    for _, r in sound_df.iterrows():
        dn = _opt_label(r.get("display_name"), "Unnamed")
        co = _opt_label(r.get("company"), "")
        lbl = f"{dn} ({co})".strip().rstrip("()") if co else dn
        sound_labels[str(r["id"])] = lbl

mus_labels: Dict[str, str] = {}
if not mus_df.empty and "id" in mus_df.columns:
    if "active" in mus_df.columns:
        mus_df = mus_df.sort_values(by="active", ascending=False)
    for _, r in mus_df.iterrows():
        rid = str(r["id"])
        mus_labels[rid] = _name_for_mus_row(r)

ROLE_CHOICES = [
    "Male Vocals", "Female Vocals",
    "Keyboard", "Drums", "Guitar", "Bass",
    "Trumpet", "Saxophone", "Trombone",
]

# -----------------------------
# Gig selector
# -----------------------------
st.markdown("---")
st.subheader("Find a Gig")

@st.cache_data(ttl=30)
def _load_gigs() -> pd.DataFrame:
    df = _select_df("gigs", "*")
    if df.empty:
        return df
    if "event_date" in df.columns:
        df["event_date"] = pd.to_datetime(df["event_date"], errors="coerce").dt.date
    return df

gigs = _load_gigs()
if gigs.empty:
    st.info("No gigs found.")
    st.stop()

from datetime import timedelta as _td

def _to_time_obj(x) -> Optional[time]:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    try:
        if isinstance(x, time):
            return x
        ts = pd.to_datetime(x, errors="coerce")
        if pd.isna(ts):
            return None
        return ts.time()
    except Exception:
        return None

def _compose_span(row):
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
        end_dt += _td(days=1)
    return (pd.Timestamp(start_dt), pd.Timestamp(end_dt))

spans = gigs.apply(_compose_span, axis=1, result_type="expand")
gigs["_start_dt"] = pd.to_datetime(spans[0], errors="coerce")

def _label_row(r: pd.Series) -> str:
    dt_str = r.get("event_date")
    if pd.notna(dt_str):
        try:
            dt_str = pd.to_datetime(dt_str).strftime("%a %b %-d, %Y")
        except Exception:
            dt_str = str(r.get("event_date"))
    title = _opt_label(r.get("title"), "(untitled)")
    venue_txt = ""
    if pd.notna(r.get("venue_id")):
        vid = str(r.get("venue_id"))
        venue_txt = venue_labels.get(vid, "")
    fee_txt = format_currency(r.get("fee"))
    parts = [p for p in [dt_str, title, venue_txt, fee_txt] if p]
    return " | ".join(parts)

gigs = gigs.sort_values(by=["_start_dt"], ascending=[True])
opts = list(gigs.index)
labels = {idx: _label_row(gigs.loc[idx]) for idx in opts}
sel_idx = st.selectbox("Select a gig to edit", options=opts, format_func=lambda i: labels.get(i, str(i)))
row = gigs.loc[sel_idx]
gid = str(row.get("id") or f"edit-{sel_idx}")

# -----------------------------
# Edit form (parity with Enter)
# -----------------------------
st.markdown("---")
st.subheader("Edit Details")

def _ampm_time_input(label: str, default_time: Optional[time], key: str) -> time:
    def _hour_min_ampm(t: Optional[time]):
        if not t:
            return 7, 0, "PM"
        h = t.hour
        ampm = "AM" if h < 12 else "PM"
        h12 = h % 12 or 12
        return h12, (t.minute // 15) * 15, ampm

    h12, m15, ap = _hour_min_ampm(_to_time_obj(default_time))
    c1, c2, c3 = st.columns([1, 1, 1])
    with c1:
        hour_12 = st.selectbox(f"{label} Hour", list(range(1, 13)), index=(h12 - 1), key=f"{key}_hr")
    with c2:
        minute = st.selectbox(f"{label} Min", [0, 15, 30, 45], index=[0, 15, 30, 45].index(m15), key=f"{key}_min")
    with c3:
        ampm = st.selectbox("AM/PM", ["AM", "PM"], index=0 if ap == "AM" else 1, key=f"{key}_ampm")

    hour24 = (hour_12 % 12) + (12 if ampm == "PM" else 0)
    return time(hour24, minute)

# Basics
b1, b2, b3 = st.columns([1, 1, 1])
with b1:
    title = st.text_input("Title (optional)", value=_opt_label(row.get("title"), ""), key=f"title_{gid}")
    event_date = st.date_input("Date of Performance", value=row.get("event_date") or date.today(), key=f"event_date_{gid}")
with b2:
    contract_status = st.selectbox(
        "Status",
        ["Pending", "Hold", "Confirmed"],
        index=["Pending", "Hold", "Confirmed"].index(row.get("contract_status") or "Pending"),
        key=f"status_{gid}",
    )
    fee_val = float(row.get("fee")) if pd.notna(row.get("fee")) else 0.0
    fee = st.number_input("Contracted Fee ($)", min_value=0.0, step=50.0, format="%.2f", value=fee_val, key=f"fee_{gid}")
    start_time_in = _ampm_time_input("Start", _to_time_obj(row.get("start_time")), key=f"start_{gid}")
    end_time_in   = _ampm_time_input("End",   _to_time_obj(row.get("end_time")),   key=f"end_{gid}")
with b3:
    band_name = st.text_input("Band (optional)", value=_opt_label(row.get("band_name"), ""), key=f"band_{gid}")

# Agent + Venue & Sound
st.markdown("---")
st.subheader("Contacts & Venue / Sound")

# Agent select with “Add New”
AGENT_ADD = "__ADD_AGENT__"
agent_options = [""] + list(agent_labels.keys()) + [AGENT_ADD]
def agent_fmt(x: str) -> str:
    if x == "": return "(none)"
    if x == AGENT_ADD: return "(+ Add New Agent)"
    return agent_labels.get(x, x)
cur_agent = str(row.get("agent_id")) if pd.notna(row.get("agent_id")) else ""
agent_id_sel = st.selectbox(
    "Agent",
    options=agent_options,
    index=(agent_options.index(cur_agent) if cur_agent in agent_options else 0),
    format_func=agent_fmt,
    key=f"agent_sel_{gid}",
)
agent_add_box = st.empty()

# Venue select with “Add New”
VENUE_ADD = "__ADD_VENUE__"
venue_options_ids = [""] + list(venue_labels.keys()) + [VENUE_ADD]
def venue_fmt(x: str) -> str:
    if x == "": return "(select venue)"
    if x == VENUE_ADD: return "(+ Add New Venue)"
    return venue_labels.get(x, x)
cur_vid = str(row.get("venue_id")) if pd.notna(row.get("venue_id")) else ""
venue_id_sel = st.selectbox(
    "Venue",
    options=venue_options_ids,
    index=(venue_options_ids.index(cur_vid) if cur_vid in venue_options_ids else 0),
    format_func=venue_fmt,
    key=f"venue_sel_{gid}",
)
venue_add_box = st.empty()

# Private flag, eligibility toggles
c1, c2 = st.columns([1, 1])
with c1:
    is_private = st.checkbox("Private Event?", value=bool(row.get("is_private")), key=f"priv_{gid}")
with c2:
    eligible_1099 = st.checkbox("1099 Eligible", value=bool(row.get("eligible_1099", False)), key=f"elig1099_{gid}")

# Sound tech with “Add New” + venue-provided toggle + conditional fee
SOUND_ADD = "__ADD_SOUND__"
sound_options_ids = [""] + list(sound_labels.keys()) + [SOUND_ADD]
def sound_fmt(x: str) -> str:
    if x == "": return "(none)"
    if x == SOUND_ADD: return "(+ Add New Sound Tech)"
    return sound_labels.get(x, x)
cur_sid = str(row.get("sound_tech_id")) if pd.notna(row.get("sound_tech_id")) else ""
sound_provided = st.checkbox(
    "Venue provides sound?",
    value=bool(row.get("sound_provided")),
    key=f"edit_sound_provided_{gid}",
)

# If venue provides sound, suppress confirmed sound tech picker
if not sound_provided:
    sound_id_sel = st.selectbox(
        "Confirmed Sound Tech",
        options=sound_options_ids,
        index=(sound_options_ids.index(cur_sid) if cur_sid in sound_options_ids else 0),
        format_func=sound_fmt,
        key=f"sound_sel_{gid}",
    )
else:
    sound_id_sel = ""

sound_add_box = st.empty()

# Conditional Sound Fee when PRS provides sound
cur_sound_fee = float(row.get("sound_fee") or 0.0)
sound_fee = None
if not sound_provided:
    sound_fee = st.number_input(
        "Sound Fee ($)",
        min_value=0.0,
        step=25.0,
        format="%.2f",
        value=cur_sound_fee,
        key=f"edit_sound_fee_{gid}",
    )

# Venue-provided sound details (free text)
sv1, sv2 = st.columns([1, 1])
with sv1:
    sound_by_venue_name = st.text_input(
        "Venue Sound Company/Contact (optional)",
        value=_opt_label(row.get("sound_by_venue_name"), ""),
        key=f"edit_sound_vendor_name_{gid}",
    )
with sv2:
    sound_by_venue_phone = st.text_input(
        "Venue Sound Phone/Email (optional)",
        value=_opt_label(row.get("sound_by_venue_phone"), ""),
        key=f"edit_sound_vendor_phone_{gid}",
    )

# -----------------------------
# Lineup (Role Assignments)
# -----------------------------
st.markdown("---")
st.subheader("Lineup (Role Assignments)")

assigned_df = _select_df("gig_musicians", "*", where_eq={"gig_id": row.get("id")}) if _table_exists("gig_musicians") else pd.DataFrame()
cur_map: Dict[str, Optional[str]] = {}
if not assigned_df.empty:
    for _, r in assigned_df.iterrows():
        cur_map[str(r.get("role"))] = str(r.get("musician_id")) if pd.notna(r.get("musician_id")) else ""

import re
ROLE_INSTRUMENT_MAP = {
    "Keyboard":  {"substr": ["keyboard", "keys", "piano", "pianist", "synth"]},
    "Drums":     {"substr": ["drums", "drummer", "percussion"]},
    "Guitar":    {"substr": ["guitar", "guitarist"]},
    "Bass":      {"substr": ["bass guitar", "bass", "bassist"]},
    "Trumpet":   {"substr": ["trumpet", "trumpeter"]},
    "Saxophone": {"substr": ["saxophone", "sax", "alto sax", "tenor sax", "baritone sax", "saxophonist"]},
    "Trombone":  {"substr": ["trombone", "trombonist"]},
}
MALE_TOKENS   = re.compile(r"\b(male|m(?![a-z])|men|man|guy|dude)\b", re.I)
FEMALE_TOKENS = re.compile(r"\b(female|f(?![a-z])|women|woman|lady|girl)\b", re.I)
VOCAL_TOKENS  = re.compile(r"\b(vocal|vocals|singer|lead vocal|lead singer)\b", re.I)

def _norm(s: str) -> str:
    return (str(s or "").strip().lower())

def _matches_role(instr: str, role: str) -> bool:
    s = _norm(instr)
    if not s:
        return False
    if role == "Male Vocals":
        if s in ("male vocal", "male vocals"):
            return True
        return bool(VOCAL_TOKENS.search(s)) and bool(MALE_TOKENS.search(s)) and not FEMALE_TOKENS.search(s)
    if role == "Female Vocals":
        if s in ("female vocal", "female vocals"):
            return True
        return bool(VOCAL_TOKENS.search(s)) and bool(FEMALE_TOKENS.search(s)) and not MALE_TOKENS.search(s)
    cfg = ROLE_INSTRUMENT_MAP.get(role, {})
    return any(tok in s for tok in cfg.get("substr", []))

line_cols = st.columns(3)
lineup: List[Dict] = []
role_add_boxes: Dict[str, st.delta_generator.DeltaGenerator] = {}

for idx, role in enumerate(ROLE_CHOICES):
    with line_cols[idx % 3]:
        sentinel = f"__ADD_MUS__:{role}"

        role_df = mus_df.copy()
        if "instrument" in role_df.columns:
            role_df = role_df[role_df["instrument"].fillna("").apply(lambda x: _matches_role(x, role))]
        if role_df.empty:
            role_df = mus_df

        if "active" in role_df.columns:
            role_df = role_df.sort_values(by="active", ascending=False)

        role_labels: Dict[str, str] = {}
        if not role_df.empty and "id" in role_df.columns:
            for _, r in role_df.iterrows():
                rid = str(r["id"])
                role_labels[rid] = _name_for_mus_row(r)

        mus_options_ids = [""] + list(role_labels.keys()) + [sentinel]

        def mus_fmt(x: str, _role=role):
            if x == "":
                return "(unassigned)"
            if x.startswith("__ADD_MUS__"):
                return "(+ Add New Musician)"
            return role_labels.get(x, mus_labels.get(x, x))

        default_val = cur_map.get(role, "")
        sel = st.selectbox(role, options=mus_options_ids,
                           index=(mus_options_ids.index(default_val) if default_val in mus_options_ids else 0),
                           format_func=mus_fmt, key=f"edit_role_{role}")
        if sel and not sel.startswith("__ADD_MUS__"):
            lineup.append({"role": role, "musician_id": sel})

        role_add_boxes[role] = st.empty()

# Inline “Add New …” sub-forms (Agents/Venues/Sound/Musicians)

# Agent add
if agent_id_sel == "__ADD_AGENT__":
    agent_add_box.empty()
    with agent_add_box.container():
        st.markdown("**➕ Add New Agent**")
        a1, a2 = st.columns([1, 1])
        with a1:
            new_agent_name = st.text_input("Agent Name", key=f"new_agent_name_{gid}")
        with a2:
            new_agent_company = st.text_input("Company (optional)", key=f"new_agent_company_{gid}")
        if st.button("Create Agent", key=f"create_agent_btn_{gid}"):
            new_id = None
            if (new_agent_name or "").strip() or (new_agent_company or "").strip():
                new_id = _create_agent((new_agent_name or "").strip(), (new_agent_company or "").strip())
            if new_id:
                st.cache_data.clear()
                st.session_state[f"agent_sel_{gid}"] = new_id
                st.success("Agent created.")
                st.rerun()

# Venue add
if venue_id_sel == "__ADD_VENUE__":
    venue_add_box.empty()
    with venue_add_box.container():
        st.markdown("**➕ Add New Venue**")
        v1, v2 = st.columns([1, 1])
        with v1:
            new_v_name  = st.text_input("Venue Name", key=f"new_v_name_{gid}")
            new_v_addr1 = st.text_input("Address Line 1", key=f"new_v_addr1_{gid}")
            new_v_city  = st.text_input("City", key=f"new_v_city_{gid}")
            new_v_phone = st.text_input("Phone (optional)", key=f"new_v_phone_{gid}")
        with v2:
            new_v_addr2 = st.text_input("Address Line 2 (optional)", key=f"new_v_addr2_{gid}")
            new_v_state = st.text_input("State", key=f"new_v_state_{gid}")
            new_v_zip   = st.text_input("Postal Code", key=f"new_v_zip_{gid}")
        if st.button("Create Venue", key=f"create_venue_btn_{gid}"):
            new_id = None
            if (new_v_name or "").strip():
                new_id = _create_venue(
                    (new_v_name or "").strip(), (new_v_addr1 or "").strip(), (new_v_addr2 or "").strip(),
                    (new_v_city or "").strip(), (new_v_state or "").strip(), (new_v_zip or "").strip(), (new_v_phone or "").strip()
                )
            if new_id:
                st.cache_data.clear()
                st.session_state[f"venue_sel_{gid}"] = new_id
                st.success("Venue created.")
                st.rerun()

# Sound Tech add
if (not sound_provided) and (sound_id_sel == "__ADD_SOUND__"):
    sound_add_box.empty()
    with sound_add_box.container():
        st.markdown("**➕ Add New Sound Tech**")
        s1, s2 = st.columns([1, 1])
        with s1:
            new_st_name   = st.text_input("Display Name", key=f"new_st_name_{gid}")
            new_st_phone  = st.text_input("Phone (optional)", key=f"new_st_phone_{gid}")
        with s2:
            new_st_company = st.text_input("Company (optional)", key=f"new_st_company_{gid}")
            new_st_email   = st.text_input("Email (optional)", key=f"new_st_email_{gid}")
        if st.button("Create Sound Tech", key=f"create_sound_btn_{gid}"):
            new_id = None
            if (new_st_name or "").strip():
                new_id = _create_soundtech(
                    (new_st_name or "").strip(), (new_st_company or "").strip(),
                    (new_st_phone or "").strip(), (new_st_email or "").strip()
                )
            if new_id:
                st.cache_data.clear()
                st.session_state[f"sound_sel_{gid}"] = new_id
                st.success("Sound Tech created.")
                st.rerun()

# Musician add (role-specific)
for role in ROLE_CHOICES:
    sel = st.session_state.get(f"edit_role_{role}", "")
    sentinel = f"__ADD_MUS__:{role}"
    if sel == sentinel:
        role_add_boxes[role].empty()
        with role_add_boxes[role].container():
            st.markdown(f"**➕ Add New Musician for {role}**")
            m1, m2, m3, m4 = st.columns([1, 1, 1, 1])
            with m1:
                new_mus_fn = st.text_input("First Name", key=f"new_mus_fn_{role}_{gid}")
            with m2:
                new_mus_ln = st.text_input("Last Name", key=f"new_mus_ln_{role}_{gid}")
            with m3:
                default_instr = role
                new_mus_instr = st.text_input("Instrument", value=default_instr, key=f"new_mus_instr_{role}_{gid}")
            with m4:
                new_mus_stage = st.text_input("Stage Name (optional)", key=f"new_mus_stage_{role}_{gid}")
            c1, c2 = st.columns([1, 1])
            with c1:
                if st.button("Create Musician", key=f"create_mus_btn_{role}_{gid}"):
                    new_id = None
                    if (new_mus_fn or "").strip() or (new_mus_ln or "").strip() or (new_mus_stage or "").strip() or (new_mus_instr or "").strip():
                        new_id = _create_musician(
                            (new_mus_fn or "").strip(), (new_mus_ln or "").strip(),
                            (new_mus_instr or role or "").strip(), (new_mus_stage or "").strip()
                        )
                    if new_id:
                        st.cache_data.clear()
                        st.session_state[f"edit_role_{role}"] = new_id
                        st.success(f"Musician created and preselected for {role}.")
                        st.rerun()
            with c2:
                if st.button("Cancel", key=f"cancel_mus_btn_{role}_{gid}"):
                    st.session_state[f"edit_role_{role}"] = ""
                    st.rerun()

# -----------------------------
# Notes & Private details
# -----------------------------
st.markdown("---")
st.subheader("Notes")
notes = st.text_area("Notes / Special Instructions (optional)", height=100, value=_opt_label(row.get("notes"), ""), key=f"notes_{gid}")

private_vals = {}
if is_private:
    st.markdown("#### Private Event Details")
    p1, p2 = st.columns([1, 1])
    with p1:
        private_event_type = st.text_input("Type of Event", value=_opt_label(row.get("private_event_type"), ""), key=f"pet_{gid}")
        organizer = st.text_input("Organizer / Company", value=_opt_label(row.get("organizer"), ""), key=f"org_{gid}")
        guest_of_honor = st.text_input("Guest(s) of Honor / Bride/Groom", value=_opt_label(row.get("guest_of_honor"), ""), key=f"goh_{gid}")
    with p2:
        private_contact = st.text_input("Primary Contact (name)", value=_opt_label(row.get("private_contact"), ""), key=f"pc_{gid}")
        private_contact_info = st.text_input("Contact Info (email/phone)", value=_opt_label(row.get("private_contact_info"), ""), key=f"pci_{gid}")
        additional_services = st.text_input("Additional Musicians/Services (optional)", value=_opt_label(row.get("additional_services"), ""), key=f"adds_{gid}")
    private_vals = {
        "private_event_type": private_event_type,
        "organizer": organizer,
        "guest_of_honor": guest_of_honor,
        "private_contact": private_contact,
        "private_contact_info": private_contact_info,
        "additional_services": additional_services,
    }

# -----------------------------
# Deposits (Admin)
# -----------------------------
dep_rows: List[Dict] = []
if _table_exists("gig_deposits"):
    existing_deps = _select_df("gig_deposits", "*", where_eq={"gig_id": row.get("id")})
    existing_deps = existing_deps.sort_values(by="sequence") if not existing_deps.empty else existing_deps
    st.markdown("---")
    st.subheader("Finance (Deposits)")
    cur_n = len(existing_deps) if not existing_deps.empty else 0
    n = st.number_input("Number of deposits (0–4)", min_value=0, max_value=4, step=1, value=cur_n, key=f"deps_n_{gid}")
    for i in range(int(n)):
        cdl, cda, cdm = st.columns([1, 1, 1])
        with cdl:
            due_default = (existing_deps.iloc[i]["due_date"] if (not existing_deps.empty and i < len(existing_deps) and pd.notna(existing_deps.iloc[i].get("due_date"))) else date.today())
            due = st.date_input(f"Deposit {i+1} due", value=pd.to_datetime(due_default).date() if pd.notna(due_default) else date.today(), key=f"edit_dep_due_{i}_{gid}")
        with cda:
            amt_default = float(existing_deps.iloc[i].get("amount", 0.0)) if (not existing_deps.empty and i < len(existing_deps)) else 0.0
            amt = st.number_input(f"Deposit {i+1} amount", min_value=0.0, step=50.0, format="%.2f", value=amt_default, key=f"edit_dep_amt_{i}_{gid}")
        with cdm:
            pct_default = bool(existing_deps.iloc[i].get("is_percentage", False)) if (not existing_deps.empty and i < len(existing_deps)) else False
            is_pct = st.checkbox(f"Deposit {i+1} is % of fee", value=pct_default, key=f"edit_dep_pct_{i}_{gid}")
        dep_rows.append({"sequence": i + 1, "due_date": due, "amount": amt, "is_percentage": is_pct})

# -----------------------------
# SAVE CHANGES
# -----------------------------
if st.button("💾 Save Changes", type="primary", key=f"save_{gid}"):
    def _compose_datetimes(event_dt: date, start_t: time, end_t: time):
        start_dt = datetime.combine(event_dt, start_t)
        end_dt = datetime.combine(event_dt, end_t)
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)
        return start_dt, end_dt

    start_dt, end_dt = _compose_datetimes(event_date, start_time_in, end_time_in)
    if end_dt.date() > start_dt.date():
        st.info(f"This gig ends next day ({end_dt.strftime('%Y-%m-%d %I:%M %p')}). We'll keep event_date as the start date.")

    # If venue provides sound, force sound tech to None
    sound_tech_id_val = (None if sound_provided else (sound_id_sel if sound_id_sel not in ("", "__ADD_SOUND__") else None))
    agent_id_val = agent_id_sel if agent_id_sel not in ("", "__ADD_AGENT__") else None
    venue_id_val = venue_id_sel if venue_id_sel not in ("", "__ADD_VENUE__") else None

    payload = {
        "title": (title or None),
        "event_date": event_date.isoformat() if isinstance(event_date, (date, datetime)) else event_date,
        "start_time": start_time_in.strftime("%H:%M:%S"),
        "end_time": end_time_in.strftime("%H:%M:%S"),
        "contract_status": contract_status,
        "fee": float(fee) if fee else None,
        "band_name": (band_name or None),
        "agent_id": agent_id_val,
        "venue_id": venue_id_val,
        "sound_tech_id": sound_tech_id_val,
        "is_private": bool(is_private),
        "notes": (notes or None),
        "sound_by_venue_name": (sound_by_venue_name or None),
        "sound_by_venue_phone": (sound_by_venue_phone or None),
        "sound_provided": bool(sound_provided),
        "sound_fee": (float(sound_fee) if (sound_fee is not None) else None),
        "eligible_1099": bool(eligible_1099) if "eligible_1099" in _table_columns("gigs") else None,
    }
    payload = _filter_to_schema("gigs", payload)

    ok = _robust_update("gigs", {"id": row.get("id")}, payload)
    if not ok:
        st.stop()

    # Replace lineup rows
    if _table_exists("gig_musicians"):
        try:
            sb.table("gig_musicians").delete().eq("gig_id", row.get("id")).execute()
        except Exception as e:
            st.warning(f"Could not clear existing lineup: {e}")
        if lineup:
            rows: List[Dict] = []
            for r in lineup:
                rows.append(_filter_to_schema("gig_musicians", {
                    "gig_id": row.get("id"),
                    "role": r["role"],
                    "musician_id": r["musician_id"],
                }))
            if rows:
                sb.table("gig_musicians").insert(rows).execute()

    # Replace deposits
    if dep_rows and _table_exists("gig_deposits"):
        try:
            sb.table("gig_deposits").delete().eq("gig_id", row.get("id")).execute()
        except Exception as e:
            st.warning(f"Could not clear existing deposits: {e}")
        rows: List[Dict] = []
        for d in dep_rows:
            rows.append(_filter_to_schema("gig_deposits", {
                "gig_id": row.get("id"),
                "sequence": d["sequence"],
                "due_date": d["due_date"].isoformat() if isinstance(d["due_date"], (date, datetime)) else d["due_date"],
                "amount": float(d["amount"] or 0.0),
                "is_percentage": bool(d["is_percentage"]),
            }))
        if rows:
            sb.table("gig_deposits").insert(rows).execute()

    st.success("Gig updated successfully ✅")
    st.write({
        "id": row.get("id"),
        "event_date": str(event_date),
        "time": f"{start_time_in.strftime('%I:%M %p').lstrip('0')} – {end_time_in.strftime('%I:%M %p').lstrip('0')}",
        "status": contract_status,
        "fee": format_currency(fee),
        "sound_provided": bool(sound_provided),
        "sound_tech_id": sound_tech_id_val or "(none)",
        "sound_fee": (None if sound_fee is None else format_currency(sound_fee)),
    })
