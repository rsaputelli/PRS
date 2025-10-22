# pages/04_Enter_Gig.py
import os
from datetime import date, time, datetime, timedelta
import pandas as pd
import streamlit as st
import datetime as dt
from supabase import create_client, Client
from typing import Optional, Dict, List, Set
from pathlib import Path

# -----------------------------
# Page config + Header
# -----------------------------
st.set_page_config(page_title="Enter Gig", page_icon="📝", layout="wide")

# Auth gate
if "user" not in st.session_state or not st.session_state["user"]:
    st.error("Please sign in from the Login page.")
    st.stop()

# Path to logo
logo_path = Path(__file__).parent.parent / "assets" / "prs_logo.png"

# --- Header: logo + title only ---
hdr1, hdr2 = st.columns([0.12, 0.88])
with hdr1:
    if logo_path.exists():
        st.image(str(logo_path), use_container_width=True)
with hdr2:
    st.markdown(
        "<h1 style='margin-bottom:0'>Enter Gig</h1>",        
        unsafe_allow_html=True
    )

# --- Divider below header ---
st.markdown("---")

# -----------------------------
# Supabase helpers
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


# Attach session for RLS (auth.uid())
if st.session_state.get("sb_access_token") and st.session_state.get("sb_refresh_token"):
    try:
        sb.auth.set_session(
            access_token=st.session_state["sb_access_token"],
            refresh_token=st.session_state["sb_refresh_token"],
        )
    except Exception as e:
        st.warning(f"Could not attach session; proceeding with limited access. ({e})")

# -----------------------------
# Cacheable fetch utilities
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

def _insert_row(table: str, payload: Dict) -> Optional[Dict]:
    try:
        res = sb.table(table).insert(payload).execute()
        rows = res.data or []
        return rows[0] if rows else None
    except Exception as e:
        st.error(f"Inserting into {table} failed: {e}")
        return None

def _insert_rows(table: str, rows: List[Dict]) -> bool:
    if not rows:
        return True
    try:
        sb.table(table).insert(rows).execute()
        return True
    except Exception as e:
        st.warning(f"Bulk insert into {table} failed: {e}")
        return False

def _filter_to_schema(table: str, data: Dict) -> Dict:
    cols = _table_columns(table)
    if not cols:
        return data
    return {k: v for k, v in data.items() if k in cols}

def _opt_label(val, fallback=""):
    return str(val) if pd.notna(val) and str(val).strip() else fallback
    
def _name_for_mus_row(r: pd.Series) -> str:
    # Build a friendly display name for a musician row
    full = " ".join([_opt_label(r.get("first_name"), ""),
                     _opt_label(r.get("last_name"), "")]).strip()
    return full or _opt_label(r.get("stage_name"), "") or "Unnamed Musician"
    

# 12-hour time helpers
def _time_from_parts(hour12: int, minute: int, ampm: str) -> time:
    hour24 = (hour12 % 12) + (12 if ampm.upper() == "PM" else 0)
    return time(hour24, minute)

def _format_12h(t: time) -> str:
    dt = datetime(2000,1,1, t.hour, t.minute)
    s = dt.strftime("%I:%M %p")
    return s.lstrip("0")

# ---------------------------------------
# Mini-creators for "Add New ..."
# ---------------------------------------
def _create_agent(name: str, company: str) -> Optional[str]:
    payload = _filter_to_schema("agents", {
        "name": name or None,
        "company": company or None,
        "first_name": None,
        "last_name": None,
    })
    row = _insert_row("agents", payload)
    return str(row["id"]) if row and "id" in row else None

def _create_musician(first_name: str, last_name: str, instrument: str, stage_name: str) -> Optional[str]:
    payload = _filter_to_schema("musicians", {
        "first_name": first_name or None,
        "last_name": last_name or None,
        "stage_name": stage_name or None,
        "instrument": instrument or None,
        "active": True if "active" in _table_columns("musicians") else None,
    })
    row = _insert_row("musicians", payload)
    return str(row["id"]) if row and "id" in row else None

def _create_soundtech(display_name: str, company: str, phone: str, email: str) -> Optional[str]:
    payload = _filter_to_schema("sound_techs", {
        "display_name": display_name or None,
        "company": company or None,
        "phone": phone or None,
        "email": email or None,
    })
    row = _insert_row("sound_techs", payload)
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
    row = _insert_row("venues", payload)
    return str(row["id"]) if row and "id" in row else None

# -----------------------------
# Reference data (dropdowns)
# -----------------------------
venues_df = _select_df("venues", "*")
sound_df  = _select_df("sound_techs", "*")
mus_df    = _select_df("musicians", "*")
agents_df = _select_df("agents", "*")

# Build id->label maps (general)
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

# A base lookup for any fallback needs
mus_labels: Dict[str, str] = {}
if not mus_df.empty and "id" in mus_df.columns:
    if "active" in mus_df.columns:
        mus_df = mus_df.sort_values(by="active", ascending=False)
    for _, r in mus_df.iterrows():
        name = (" ".join([_opt_label(r.get("first_name"), ""), _opt_label(r.get("last_name"), "")]).strip()
                or _opt_label(r.get("stage_name"), "") or "Unnamed Musician")
        mus_labels[str(r["id"])] = name

ROLE_CHOICES = [
    "Male Vocals", "Female Vocals",
    "Keyboard", "Drums", "Guitar", "Bass",
    "Trumpet", "Saxophone", "Trombone",
]

IS_ADMIN = bool(st.session_state.get("is_admin", True))

# -----------------------------
# Session defaults
# -----------------------------
st.session_state.setdefault("preselect_agent_id", None)
st.session_state.setdefault("preselect_venue_id", None)
st.session_state.setdefault("preselect_sound_id", None)
st.session_state.setdefault("preselect_role_ids", {})  # role -> musician_id

# -----------------------------
# Event Basics
# -----------------------------
st.subheader("Event Basics")

eb1, eb2, eb3 = st.columns([1,1,1])

# Left column: Title + Date + Performance Time row
with eb1:
    title = st.text_input("Title (optional)", placeholder="e.g., Spring Gala")
    event_date = st.date_input("Date of Performance", value=date.today())
    
with eb2:
    contract_status = st.selectbox("Status", ["Pending", "Hold", "Confirmed"], index=0)
    fee = st.number_input("Contracted Fee ($)", min_value=0.0, step=50.0, format="%.2f")

    # --- AM/PM-style time inputs (visual wrapper) ---
    import datetime as dt

    def _ampm_time_input(label: str, default_hour: int, default_min: int = 0, key: str = "") -> dt.time:
        """Simple AM/PM visual wrapper that returns a real datetime.time object."""
        col1, col2, col3 = st.columns([1, 1, 1])
        with col1:
            hour_12 = st.selectbox(f"{label} Hour", list(range(1, 13)), index=(default_hour % 12) - 1, key=f"{key}_hr")
        with col2:
            minute = st.selectbox(f"{label} Min", [0, 15, 30, 45], index=default_min // 15, key=f"{key}_min")
        with col3:
            ampm = st.selectbox("AM/PM", ["AM", "PM"], index=0 if default_hour < 12 else 1, key=f"{key}_ampm")
        # Convert to 24-hour
        hour_24 = hour_12 % 12 + (12 if ampm == "PM" else 0)
        return dt.time(hour_24, minute)

    # Replace the standard time_input controls
    start_time_in = _ampm_time_input("Start", 9, 0, key="start_time_in")
    end_time_in   = _ampm_time_input("End", 1, 0, key="end_time_in")

# Right side of Event Basics: Agent + Band
ag_col, band_col = st.columns([1,1])
with band_col:
    band_name = st.text_input("Band (optional)", placeholder="PRS")
with ag_col:
    AGENT_ADD = "__ADD_AGENT__"
    agent_options = [""] + list(agent_labels.keys()) + [AGENT_ADD]
    def agent_fmt(x: str) -> str:
        if x == "": return "(none)"
        if x == AGENT_ADD: return "(+ Add New Agent)"
        return agent_labels.get(x, x)
    agent_default = st.session_state.get("preselect_agent_id") or ""
    agent_index = agent_options.index(agent_default) if agent_default in agent_options else 0
    agent_id_sel = st.selectbox("Agent", options=agent_options, index=agent_index, format_func=agent_fmt, key="agent_sel")

agent_add_box = st.empty()


# -----------------------------
# Venue & Sound
# -----------------------------
st.markdown("---")
st.subheader("Venue & Sound")

vs1, vs2 = st.columns([1,1])
with vs1:
    VENUE_ADD = "__ADD_VENUE__"
    venue_options_ids = [""] + list(venue_labels.keys()) + [VENUE_ADD]  # "" = (select venue)
    def venue_fmt(x: str) -> str:
        if x == "": return "(select venue)"
        if x == VENUE_ADD: return "(+ Add New Venue)"
        return venue_labels.get(x, x)
    venue_default = st.session_state.get("preselect_venue_id") or ""
    venue_index = venue_options_ids.index(venue_default) if venue_default in venue_options_ids else 0
    venue_id_sel = st.selectbox("Venue", options=venue_options_ids, index=venue_index, format_func=venue_fmt, key="venue_sel")
    is_private = st.checkbox("Private Event?", value=False)
    eligible_1099 = st.checkbox("1099 Eligible", value=False)
venue_add_box = st.empty()

with vs2:
    SOUND_ADD = "__ADD_SOUND__"
    sound_options_ids = [""] + list(sound_labels.keys()) + [SOUND_ADD]  # "" = (none)
    def sound_fmt(x: str) -> str:
        if x == "": return "(none)"
        if x == SOUND_ADD: return "(+ Add New Sound Tech)"
        return sound_labels.get(x, x)
    sound_default = st.session_state.get("preselect_sound_id") or ""
    sound_index = sound_options_ids.index(sound_default) if sound_default in sound_options_ids else 0
    sound_id_sel = st.selectbox("Confirmed Sound Tech", options=sound_options_ids, index=sound_index, format_func=sound_fmt, key="sound_sel")
    sound_by_venue = st.checkbox("Sound provided by venue?", value=False)
sound_add_box = st.empty()

if sound_by_venue:
    sv1, sv2 = st.columns([1,1])
    with sv1:
        sound_by_venue_name = st.text_input("Venue Sound Company/Contact (optional)")
    with sv2:
        sound_by_venue_phone = st.text_input("Venue Sound Phone/Email (optional)")
else:
    sound_by_venue_name = ""
    sound_by_venue_phone = ""

# -----------------------------
# Lineup (Role Assignments) — FILTERED
# -----------------------------
st.markdown("---")
st.subheader("Lineup (Role Assignments)")

import re

# Canonical substrings for non-gendered instruments
ROLE_INSTRUMENT_MAP = {
    "Keyboard":  {"substr": ["keyboard", "keys", "piano", "pianist", "synth"]},
    "Drums":     {"substr": ["drums", "drummer", "percussion"]},
    "Guitar":    {"substr": ["guitar", "guitarist"]},
    "Bass":      {"substr": ["bass guitar", "bass", "bassist"]},
    "Trumpet":   {"substr": ["trumpet", "trumpeter"]},
    "Saxophone": {"substr": ["saxophone", "sax", "alto sax", "tenor sax", "baritone sax", "saxophonist"]},
    "Trombone":  {"substr": ["trombone", "trombonist"]},
}

# Regex tokens for gender and generic vocal words
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
        # Exact canonical values accepted immediately
        if s in ("male vocal", "male vocals"):
            return True
        # Must look like vocals AND include a male cue, while not containing a female cue
        return bool(VOCAL_TOKENS.search(s)) and bool(MALE_TOKENS.search(s)) and not FEMALE_TOKENS.search(s)

    if role == "Female Vocals":
        if s in ("female vocal", "female vocals"):
            return True
        return bool(VOCAL_TOKENS.search(s)) and bool(FEMALE_TOKENS.search(s)) and not MALE_TOKENS.search(s)

    # Other instruments: simple substring test
    cfg = ROLE_INSTRUMENT_MAP.get(role, {})
    return any(tok in s for tok in cfg.get("substr", []))



lineup_cols = st.columns(3)
lineup_selections: List[Dict] = []
preselect_roles: Dict[str, Optional[str]] = st.session_state.get("preselect_role_ids", {})
role_add_boxes: Dict[str, st.delta_generator.DeltaGenerator] = {}

for idx, role in enumerate(ROLE_CHOICES):
    with lineup_cols[idx % 3]:
        sentinel = f"__ADD_MUS__:{role}"

        # Build a role-specific filtered list from mus_df
        role_df = mus_df.copy()
        if "instrument" in role_df.columns:
            role_df = role_df[
                role_df["instrument"].fillna("").apply(lambda x: _matches_role(x, role))
            ]
        # Fallback: if filter leaves nothing, fall back to full list
        if role_df.empty:
            role_df = mus_df

        # Sort active first if available
        if "active" in role_df.columns:
            role_df = role_df.sort_values(by="active", ascending=False)

        # Build labels for this role only
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
            return role_labels.get(x, mus_labels.get(x, x))  # graceful fallback

        pre_id = preselect_roles.get(role) or ""
        sel_index = mus_options_ids.index(pre_id) if pre_id in mus_options_ids else 0
        sel_id = st.selectbox(role, options=mus_options_ids, index=sel_index,
                              format_func=mus_fmt, key=f"mus_sel_{role}")

        if sel_id and not sel_id.startswith("__ADD_MUS__"):
            lineup_selections.append({"role": role, "musician_id": sel_id})

        role_add_boxes[role] = st.empty()
        
        def _robust_insert(table: str, payload: Dict, max_attempts: int = 8) -> Optional[Dict]:
            """Insert while auto-dropping unknown columns reported by PostgREST PGRST204 errors."""
            data = dict(payload)  # copy
            for _ in range(max_attempts):
                try:
                    res = sb.table(table).insert(data).execute()
                    rows = res.data or []
                    return rows[0] if rows else None
                except Exception as e:
                    msg = str(e)
                    # Look for: "Could not find the 'X' column of 'table' in the schema cache"
                    marker = "Could not find the '"
                    if "PGRST204" in msg and marker in msg:
                        start = msg.find(marker) + len(marker)
                        end = msg.find("'", start)
                        bad_col = msg[start:end] if end > start else None
                        if bad_col and bad_col in data:
                            # Drop the offending column and retry
                            data.pop(bad_col, None)
                            continue
                    # Not an unknown-column case → surface error and stop
                    st.error(f"Insert into {table} failed: {e}")
                    return None
            st.error(f"Insert into {table} failed after removing unknown columns: {data.keys()}")
            return None


# -----------------------------
# Notes & Private details
# -----------------------------
st.markdown("---")
st.subheader("Notes")
notes = st.text_area("Notes / Special Instructions (optional)", height=100, placeholder="Load-in details, parking, dress code, etc.")

private_vals = {}
if is_private:
    st.markdown("#### Private Event Details")
    p1, p2 = st.columns([1,1])
    with p1:
        private_event_type = st.text_input("Type of Event (e.g., Wedding, Corporate, Birthday)")
        organizer = st.text_input("Organizer / Company")
        guest_of_honor = st.text_input("Guest(s) of Honor / Bride/Groom")
    with p2:
        private_contact = st.text_input("Primary Contact (name)")
        private_contact_info = st.text_input("Contact Info (email/phone)")
        additional_services = st.text_input("Additional Musicians/Services (optional)")
    private_vals = {
        "private_event_type": private_event_type,
        "organizer": organizer,
        "guest_of_honor": guest_of_honor,
        "private_contact": private_contact,
        "private_contact_info": private_contact_info,
        "additional_services": additional_services,
    }

# -----------------------------
# Finance (Admin)
# -----------------------------
deposit_rows: List[Dict] = []
if IS_ADMIN:
    st.markdown("---")
    st.subheader("Finance (Admin Only)")
    add_deps = st.number_input("Number of deposits (0–4)", min_value=0, max_value=4, step=1, value=int(st.session_state.get("num_deposits", 0)), key="num_deposits")
    for i in range(int(add_deps)):
        cdl, cda, cdm = st.columns([1,1,1])
        with cdl:
            due = st.date_input(f"Deposit {i+1} due", value=date.today(), key=f"dep_due_{i}")
        with cda:
            amt = st.number_input(f"Deposit {i+1} amount", min_value=0.0, step=50.0, format="%.2f", key=f"dep_amt_{i}")
        with cdm:
            is_pct = st.checkbox(f"Deposit {i+1} is % of fee", value=False, key=f"dep_pct_{i}")
        deposit_rows.append({"sequence": i+1, "due_date": due, "amount": amt, "is_percentage": is_pct})

# -----------------------------
# Sub-forms NEAR triggers (ID-based sentinels)
# -----------------------------
# Agent add
if agent_id_sel == "__ADD_AGENT__":
    agent_add_box.empty()
    with agent_add_box.container():
        st.markdown("**➕ Add New Agent**")
        a1, a2 = st.columns([1,1])
        with a1:
            new_agent_name = st.text_input("Agent Name", key="new_agent_name")
        with a2:
            new_agent_company = st.text_input("Company (optional)", key="new_agent_company")
        if st.button("Create Agent", key="create_agent_btn"):
            new_id = None
            if (new_agent_name or "").strip() or (new_agent_company or "").strip():
                new_id = _create_agent((new_agent_name or "").strip(), (new_agent_company or "").strip())
            if new_id:
                st.cache_data.clear()
                st.session_state["preselect_agent_id"] = new_id
                st.success("Agent created.")
                st.rerun()

# Venue add
if venue_id_sel == "__ADD_VENUE__":
    venue_add_box.empty()
    with venue_add_box.container():
        st.markdown("**➕ Add New Venue**")
        v1, v2 = st.columns([1,1])
        with v1:
            new_v_name  = st.text_input("Venue Name", key="new_v_name")
            new_v_addr1 = st.text_input("Address Line 1", key="new_v_addr1")
            new_v_city  = st.text_input("City", key="new_v_city")
            new_v_phone = st.text_input("Phone (optional)", key="new_v_phone")
        with v2:
            new_v_addr2 = st.text_input("Address Line 2 (optional)", key="new_v_addr2")
            new_v_state = st.text_input("State", key="new_v_state")
            new_v_zip   = st.text_input("Postal Code", key="new_v_zip")
        if st.button("Create Venue", key="create_venue_btn"):
            new_id = None
            if (new_v_name or "").strip():
                new_id = _create_venue(
                    (new_v_name or "").strip(), (new_v_addr1 or "").strip(), (new_v_addr2 or "").strip(),
                    (new_v_city or "").strip(), (new_v_state or "").strip(), (new_v_zip or "").strip(), (new_v_phone or "").strip()
                )
            if new_id:
                st.cache_data.clear()
                st.session_state["preselect_venue_id"] = new_id
                st.success("Venue created.")
                st.rerun()

# Sound Tech add
if sound_id_sel == "__ADD_SOUND__":
    sound_add_box.empty()
    with sound_add_box.container():
        st.markdown("**➕ Add New Sound Tech**")
        s1, s2 = st.columns([1,1])
        with s1:
            new_st_name   = st.text_input("Display Name", key="new_st_name")
            new_st_phone  = st.text_input("Phone (optional)", key="new_st_phone")
        with s2:
            new_st_company = st.text_input("Company (optional)", key="new_st_company")
            new_st_email   = st.text_input("Email (optional)", key="new_st_email")
        if st.button("Create Sound Tech", key="create_sound_btn"):
            new_id = None
            if (new_st_name or "").strip():
                new_id = _create_soundtech(
                    (new_st_name or "").strip(), (new_st_company or "").strip(),
                    (new_st_phone or "").strip(), (new_st_email or "").strip()
                )
            if new_id:
                st.cache_data.clear()
                st.session_state["preselect_sound_id"] = new_id
                st.success("Sound Tech created.")
                st.rerun()

# Musician add (role-specific sentinel; pre-fill instrument with role)
for role in ROLE_CHOICES:
    sel_id = st.session_state.get(f"mus_sel_{role}", "")
    sentinel = f"__ADD_MUS__:{role}"
    if sel_id == sentinel:
        role_add_boxes[role].empty()
        with role_add_boxes[role].container():
            st.markdown(f"**➕ Add New Musician for {role}**")
            m1, m2, m3, m4 = st.columns([1,1,1,1])
            with m1:
                new_mus_fn = st.text_input("First Name", key=f"new_mus_fn_{role}")
            with m2:
                new_mus_ln = st.text_input("Last Name", key=f"new_mus_ln_{role}")
            with m3:
                default_instr = role
                new_mus_instr = st.text_input("Instrument", value=default_instr, key=f"new_mus_instr_{role}")
            with m4:
                new_mus_stage = st.text_input("Stage Name (optional)", key=f"new_mus_stage_{role}")
            c1, c2 = st.columns([1,1])
            with c1:
                if st.button("Create Musician", key=f"create_mus_btn_{role}"):
                    new_id = None
                    if (new_mus_fn or "").strip() or (new_mus_ln or "").strip() or (new_mus_stage or "").strip() or (new_mus_instr or "").strip():
                        new_id = _create_musician(
                            (new_mus_fn or "").strip(), (new_mus_ln or "").strip(),
                            (new_mus_instr or role or "").strip(), (new_mus_stage or "").strip()
                        )
                    if new_id:
                        st.cache_data.clear()
                        pre = st.session_state.get("preselect_role_ids", {})
                        pre[role] = new_id
                        st.session_state["preselect_role_ids"] = pre
                        st.success(f"Musician created and preselected for {role}.")
                        st.rerun()
            with c2:
                if st.button("Cancel", key=f"cancel_mus_btn_{role}"):
                    st.session_state[f"mus_sel_{role}"] = ""
                    st.rerun()

# -----------------------------
# SAVE button
# -----------------------------
if st.button("💾 Save Gig", type="primary"):
    def _compose_datetimes(event_dt: date, start_t: time, end_t: time):
        start_dt = datetime.combine(event_dt, start_t)
        end_dt = datetime.combine(event_dt, end_t)
        if end_dt <= start_dt:  # ends after midnight
            end_dt += timedelta(days=1)
        return start_dt, end_dt

    start_dt, end_dt = _compose_datetimes(event_date, start_time_in, end_time_in)

    # Optional heads-up for the user
    if end_dt.date() > start_dt.date():
        st.info(
            f"This gig ends next day ({end_dt.strftime('%Y-%m-%d %I:%M %p')}). "
            "We’ll save event_date as the start date and keep your end time as entered."
        )

    # Resolve IDs (ignore add sentinels)
    agent_id_val = agent_id_sel if agent_id_sel not in ("", "__ADD_AGENT__") else None
    venue_id_val = venue_id_sel if venue_id_sel not in ("", "__ADD_VENUE__") else None
    sound_tech_id_val = (sound_id_sel if (sound_id_sel not in ("", "__ADD_SOUND__") and not sound_by_venue) else None)

    # 🚫 NO band_name here
    gig_payload = {
        "title": (title or None),
        "event_date": event_date.isoformat() if isinstance(event_date, (date, datetime)) else event_date,
        "start_time": start_time_in.strftime("%H:%M:%S"),
        "end_time":   end_time_in.strftime("%H:%M:%S"),
        "contract_status": contract_status,
        "fee": float(fee) if fee else None,
        "agent_id": agent_id_val,
        "venue_id": venue_id_val,
        "sound_tech_id": sound_tech_id_val,
        "is_private": bool(is_private),
        "notes": (notes or None),
        "sound_by_venue_name": (sound_by_venue_name or None),
        "sound_by_venue_phone": (sound_by_venue_phone or None),
    }
    # Merge private fields (if any)
    for k, v in (private_vals or {}).items():
        gig_payload[k] = v or None

    # Trim if we can (works when SELECT policy exists), but robust insert will still guard us
    gig_payload = _filter_to_schema("gigs", gig_payload)

    # Insert ONCE with auto-drop of unknown columns
    new_gig = _robust_insert("gigs", gig_payload)
    if not new_gig:
        st.stop()

    gig_id = str(new_gig.get("id", ""))


    # (rest of your save logic: gig_musicians, gig_deposits, success message, etc.)

    # gig_musicians
    if _table_exists("gig_musicians"):
        gm_rows: List[Dict] = []
        for row in lineup_selections:
            gm_rows.append(_filter_to_schema("gig_musicians", {
                "gig_id": gig_id,
                "role": row["role"],
                "musician_id": row["musician_id"],
            }))
        if gm_rows:
            _insert_rows("gig_musicians", gm_rows)

    # gig_deposits (admin only)
    if IS_ADMIN and _table_exists("gig_deposits"):
        rows: List[Dict] = []
        n = int(st.session_state.get("num_deposits", 0))
        for i in range(n):
            due = st.session_state.get(f"dep_due_{i}", date.today())
            amt = float(st.session_state.get(f"dep_amt_{i}", 0.0) or 0.0)
            is_pct = bool(st.session_state.get(f"dep_pct_{i}", False))
            rows.append(_filter_to_schema("gig_deposits", {
                "gig_id": gig_id,
                "sequence": i + 1,
                "due_date": due.isoformat() if isinstance(due, (date, datetime)) else due,
                "amount": amt,
                "is_percentage": is_pct,
            }))
        if rows:
            _insert_rows("gig_deposits", rows)

    # Success summary (12-hour times)
    st.success("Gig saved successfully ✅")
    st.write({
        "id": gig_id,
        "title": new_gig.get("title"),
        "event_date": new_gig.get("event_date"),
        "start_time (12-hr)": _format_12h(start_time_in),
        "end_time (12-hr)": _format_12h(end_time_in),
        "status": new_gig.get("contract_status"),
        "fee": new_gig.get("fee"),
    })

    st.info("Open the Schedule View to verify the new gig appears with Venue / Location / Sound.")

