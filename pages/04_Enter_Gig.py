# pages/04_Enter_Gig.py
import os
from datetime import date, time, datetime, timedelta
import pandas as pd
import streamlit as st
import datetime as dt
from supabase import create_client, Client
from typing import Optional, Dict, List, Set
from pathlib import Path
from lib.ui_header import render_header
from lib.ui_format import format_currency  # kept import; may be used elsewhere

st.set_page_config(page_title="Enter Gig", page_icon="📝", layout="wide")

# Auth gate
if "user" not in st.session_state or not st.session_state["user"]:
    st.error("Please sign in from the Login page.")
    st.stop()

render_header(title="Enter Gig", emoji="")
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
    stage = _opt_label(r.get("stage_name"), "")
    if stage:
        return stage
    full = " ".join([_opt_label(r.get("first_name"), ""),
                     _opt_label(r.get("last_name"), "")]).strip()
    return full or _opt_label(r.get("display_name"), "") or "Unnamed Musician"

def _format_12h(t: time) -> str:
    dtx = datetime(2000, 1, 1, t.hour, t.minute)
    s = dtx.strftime("%I:%M %p")
    return s.lstrip("0")

# Robust insert that auto-drops unknown columns (PGRST204)
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
# Reference data (dropdowns)
# -----------------------------
venues_df = _select_df("venues", "*")
sound_df  = _select_df("sound_techs", "*")
mus_df    = _select_df("musicians", "*")
agents_df = _select_df("agents", "*")

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
        name = (_opt_label(r.get("stage_name"), "")
                or " ".join([_opt_label(r.get("first_name"), ""), _opt_label(r.get("last_name"), "")]).strip()
                or _opt_label(r.get("display_name"), "")
                or "Unnamed Musician")
        mus_labels[str(r["id"])] = name

ROLE_CHOICES = [
    "Male Vocals", "Female Vocals",
    "Keyboard", "Drums", "Guitar", "Bass",
    "Trumpet", "Saxophone", "Trombone",
]

IS_ADMIN = bool(st.session_state.get("is_admin", True))

# -----------------------------
# Session defaults / flags
# -----------------------------
st.session_state.setdefault("preselect_agent_id", None)
st.session_state.setdefault("preselect_venue_id", None)
st.session_state.setdefault("preselect_sound_id", None)
st.session_state.setdefault("preselect_role_ids", {})  # role -> musician_id
st.session_state.setdefault("show_add_agent", False)
st.session_state.setdefault("show_add_venue", False)
st.session_state.setdefault("show_add_sound", False)
for _r in ROLE_CHOICES:
    st.session_state.setdefault(f"show_add_mus__{_r}", False)

# =============================
# MAIN FORM
# =============================
with st.form("enter_gig_form", clear_on_submit=False):
    st.subheader("Event Basics")
    eb1, eb2, eb3 = st.columns([1, 1, 1])

    with eb1:
        title = st.text_input("Title (optional)", placeholder="e.g., Spring Gala", key="title_in")
        event_date = st.date_input("Date of Performance", value=date.today(), key="event_date_in")

    with eb2:
        contract_status = st.selectbox("Status", ["Pending", "Hold", "Confirmed"], index=0, key="status_in")
        fee = st.number_input("Contracted Fee ($)", min_value=0.0, step=50.0, format="%.2f", key="fee_in")

        # Simple 12h time inputs
        def _ampm_time_input(label: str, default_hour: int, default_min: int = 0, key: str = "") -> dt.time:
            col1, col2, col3 = st.columns([1, 1, 1])
            with col1:
                hour_12 = st.selectbox(f"{label} Hour", list(range(1, 13)), index=(default_hour % 12) - 1, key=f"{key}_hr")
            with col2:
                minute = st.selectbox(f"{label} Min", [0, 15, 30, 45], index=default_min // 15, key=f"{key}_min")
            with col3:
                ampm = st.selectbox("AM/PM", ["AM", "PM"], index=0 if default_hour < 12 else 1, key=f"{key}_ampm")
            hour_24 = hour_12 % 12 + (12 if ampm == "PM" else 0)
            return dt.time(hour_24, minute)

        start_time_in = _ampm_time_input("Start", 9, 0, key="start_time_in")
        end_time_in   = _ampm_time_input("End",   1, 0, key="end_time_in")

    with eb3:
        band_name = st.text_input("Band (optional)", placeholder="PRS", key="band_name_in")

    # Agent
    st.markdown("---")
    ag_col, band_col = st.columns([1, 1])
    with ag_col:
        AGENT_ADD = "__ADD_AGENT__"
        _pending = st.session_state.pop("agent_sel_pending", None)
        if _pending:
            st.session_state["agent_sel"] = _pending

        agent_options = [""] + list(agent_labels.keys()) + [AGENT_ADD]
        if _pending and _pending not in agent_options:
            agent_options.insert(1, _pending)
            agent_labels.setdefault(_pending, "New agent")

        def agent_fmt(x: str) -> str:
            if x == "":
                return "(none)"
            if x == AGENT_ADD:
                return "(+ Add New Agent)"
            return agent_labels.get(x, x)

        agent_id_sel = st.selectbox(
            "Agent",
            options=agent_options,
            format_func=agent_fmt,
            key="agent_sel",
        )

    with band_col:
        st.empty()

     # Venue & Sound
    st.markdown("---")
    st.subheader("Venue & Sound")

    vs1, vs2 = st.columns([1, 1])
    with vs1:
        VENUE_ADD = "__ADD_VENUE__"
        venue_options_ids = [""] + list(venue_labels.keys()) + [VENUE_ADD]

        def venue_fmt(x: str):
            if x == "": return "(select venue)"
            if x == VENUE_ADD: return "(+ Add New Venue)"
            return venue_labels.get(x, x)

        # ✅ NEW: apply pending preselect BEFORE the selectbox
        _pending_v = st.session_state.pop("venue_sel_pending", None)
        if _pending_v:
            st.session_state["venue_sel"] = _pending_v

        # (Remove the old preselect_venue_id/index calc)
        venue_id_sel = st.selectbox(
            "Venue",
            options=venue_options_ids,
            format_func=venue_fmt,
            key="venue_sel",
        )

        is_private = st.checkbox("Private Event?", value=False, key="is_private_in")
        eligible_1099 = st.checkbox("1099 Eligible", value=False, key="eligible_1099_in")

    with vs2:
        SOUND_ADD = "__ADD_SOUND__"
        sound_options_ids = [""] + list(sound_labels.keys()) + [SOUND_ADD]

        def sound_fmt(x: str) -> str:
            if x == "": 
                return "(none)"
            if x == SOUND_ADD: 
                return "(+ Add New Sound Tech)"
            return sound_labels.get(x, x)

        # ✅ NEW: apply pending preselect BEFORE the selectbox
        _pending_s = st.session_state.pop("sound_sel_pending", None)
        if _pending_s:
            st.session_state["sound_sel"] = _pending_s

        # (Remove the old preselect_sound_id/index logic)
        sound_id_sel = st.selectbox(
            "Confirmed Sound Tech",
            options=sound_options_ids,
            format_func=sound_fmt,
            key="sound_sel",
        )

        sound_by_venue = st.checkbox("Sound provided by venue?", value=False, key="sound_by_venue_in")


    if sound_by_venue:
        sv1, sv2 = st.columns([1, 1])
        with sv1:
            sound_by_venue_name = st.text_input("Venue Sound Company/Contact (optional)", key="sv_name_in")
        with sv2:
            sound_by_venue_phone = st.text_input("Venue Sound Phone/Email (optional)", key="sv_phone_in")
    else:
        sound_by_venue_name = ""
        sound_by_venue_phone = ""

    # Lineup
    st.markdown("---")
    st.subheader("Lineup (Role Assignments)")

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
            if s in ("male vocal", "male vocals"): return True
            return bool(VOCAL_TOKENS.search(s)) and bool(MALE_TOKENS.search(s)) and not FEMALE_TOKENS.search(s)
        if role == "Female Vocals":
            if s in ("female vocal", "female vocals"): return True
            return bool(VOCAL_TOKENS.search(s)) and bool(FEMALE_TOKENS.search(s)) and not MALE_TOKENS.search(s)
        cfg = ROLE_INSTRUMENT_MAP.get(role, {})
        return any(tok in s for tok in cfg.get("substr", []))

    lineup_cols = st.columns(3)
    lineup_selections: List[Dict] = []
    preselect_roles: Dict[str, Optional[str]] = st.session_state.get("preselect_role_ids", {})

    for idx, role in enumerate(ROLE_CHOICES):
        with lineup_cols[idx % 3]:
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

            pre_id = preselect_roles.get(role) or ""
            sel_index = mus_options_ids.index(pre_id) if pre_id in mus_options_ids else 0
            st.selectbox(
                role,
                options=mus_options_ids,
                index=sel_index,
                format_func=mus_fmt,
                key=f"mus_sel_{role}",
            )

            sel_id = st.session_state.get(f"mus_sel_{role}", "")
            if sel_id and not str(sel_id).startswith("__ADD_MUS__"):
                lineup_selections.append({"role": role, "musician_id": sel_id})

    # Notes & Private details
    st.markdown("---")
    st.subheader("Notes")
    notes = st.text_area("Notes / Special Instructions (optional)",
                         height=100,
                         placeholder="Load-in details, parking, dress code, etc.",
                         key="notes_in")

    private_vals = {}
    if st.session_state.get("is_private_in", False):
        st.markdown("#### Private Event Details")
        p1, p2 = st.columns([1, 1])
        with p1:
            private_event_type = st.text_input("Type of Event (e.g., Wedding, Corporate, Birthday)", key="priv_type_in")
            organizer = st.text_input("Organizer / Company", key="priv_org_in")
            guest_of_honor = st.text_input("Guest(s) of Honor / Bride/Groom)", key="priv_guest_in")
        with p2:
            private_contact = st.text_input("Primary Contact (name)", key="priv_contact_in")
            private_contact_info = st.text_input("Contact Info (email/phone)", key="priv_contact_info_in")
            additional_services = st.text_input("Additional Musicians/Services (optional)", key="priv_addl_in")
        private_vals = {
            "private_event_type": private_event_type,
            "organizer": organizer,
            "guest_of_honor": guest_of_honor,
            "private_contact": private_contact,
            "private_contact_info": private_contact_info,
            "additional_services": additional_services,
        }

    # Finance (Admin Only)
    deposit_rows_form: List[Dict] = []
    autoc_send_agent_on_create = False
    autoc_send_st_on_create = False
    autoc_send_players_on_create = False

    if IS_ADMIN:
        st.markdown("---")
        st.subheader("Finance (Admin Only)")
        add_deps = st.number_input("Number of deposits (0–4)", min_value=0, max_value=4, step=1,
                                   value=int(st.session_state.get("num_deposits", 0)),
                                   key="num_deposits")
        for i in range(int(add_deps)):
            cdl, cda, cdm = st.columns([1, 1, 1])
            with cdl:
                due = st.date_input(f"Deposit {i+1} due", value=date.today(), key=f"dep_due_{i}")
            with cda:
                amt = st.number_input(f"Deposit {i+1} amount", min_value=0.0, step=50.0, format="%.2f", key=f"dep_amt_{i}")
            with cdm:
                is_pct = st.checkbox(f"Deposit {i+1} is % of fee", value=False, key=f"dep_pct_{i}")
            deposit_rows_form.append({"sequence": i+1, "due_date": due, "amount": amt, "is_percentage": is_pct})

        if "autoc_send_agent_on_create" not in st.session_state:
            st.session_state["autoc_send_agent_on_create"] = True
        if not st.session_state.get("sound_by_venue_in", False) and "autoc_send_st_on_create" not in st.session_state:
            st.session_state["autoc_send_st_on_create"] = True
        if lineup_selections and "autoc_send_players_on_create" not in st.session_state:
            st.session_state["autoc_send_players_on_create"] = True

        autoc_send_agent_on_create = st.checkbox(
            "Auto-send Agent Confirmation on Create",
            value=st.session_state.get("autoc_send_agent_on_create", True),
            key="autoc_send_agent_on_create",
        )

        if not st.session_state.get("sound_by_venue_in", False):
            autoc_send_st_on_create = st.checkbox(
                "Auto-send Sound Tech Confirmation on Create",
                value=st.session_state.get("autoc_send_st_on_create", True),
                key="autoc_send_st_on_create",
            )

        if lineup_selections:
            autoc_send_players_on_create = st.checkbox(
                "Auto-send Player Confirmations on Create",
                value=st.session_state.get("autoc_send_players_on_create", True),
                key="autoc_send_players_on_create",
            )

    # The one and only submit button (inside the form)
    submit = st.form_submit_button("💾 Save Gig", key="enter_save_gig_btn")
# --- Add-New toolbar (outside the form) ---
t1, t2, t3, t4 = st.columns([1,1,1,4])

with t1:
    if st.button("➕ Add New Agent", key="btn_add_agent"):
        st.session_state["show_add_agent"] = True
        st.rerun()

with t2:
    if st.button("➕ Add New Venue", key="btn_add_venue"):
        st.session_state["show_add_venue"] = True
        st.rerun()

with t3:
    if st.button("➕ Add New Sound Tech", key="btn_add_sound"):
        st.session_state["show_add_sound"] = True
        st.rerun()

with t4:
    st.caption("Tip: use these buttons to create new records without submitting the form.")


# =============================
# SUB-FORMS (OUTSIDE MAIN FORM)
# =============================
agent_add_box = st.empty()
venue_add_box = st.empty()
sound_add_box = st.empty()

# Agent add sub-form
if st.session_state.get("show_add_agent"):
    with agent_add_box.container():
        st.markdown("**➕ Add New Agent**")
        a1, a2 = st.columns([1, 1])
        with a1:
            ag_first = st.text_input("First name", key="new_agent_first")
            ag_company = st.text_input("Company (optional)", key="new_agent_company")
            ag_phone = st.text_input("Phone (optional)", key="new_agent_phone")
        with a2:
            ag_last = st.text_input("Last name", key="new_agent_last")
            ag_email = st.text_input("Email", key="new_agent_email", help="Required; unique (case-insensitive)")
            ag_website = st.text_input("Website (optional)", key="new_agent_website")

        c1, c2 = st.columns([1, 3])
        with c1:
            save_new = st.button("Save Agent", key="save_new_agent_btn")
        with c2:
            st.caption("Tip: display name is built by a DB trigger from name/company.")

        if save_new:
            email_val = (ag_email or "").strip()
            if not email_val:
                st.error("Email is required to create an agent.")
                st.stop()
            payload = {
                "first_name": (ag_first or "").strip() or None,
                "last_name": (ag_last or "").strip() or None,
                "company": (ag_company or "").strip() or None,
                "phone": (ag_phone or "").strip() or None,
                "email": email_val,
                "website": (ag_website or "").strip() or None,
                "active": True,
            }
            try:
                res = sb.table("agents").insert(payload).execute()
                new_agent_id = str(res.data[0]["id"])
                st.success("Agent created ✅")
                st.session_state["show_add_agent"] = False
                st.session_state["agent_sel_pending"] = new_agent_id
                st.rerun()
            except Exception as e:
                err_text = str(e).lower()
                if "duplicate" in err_text or "unique" in err_text:
                    try:
                        existing = (
                            sb.table("agents")
                            .select("id, email, display_name, first_name, last_name, company")
                            .ilike("email", email_val)
                            .limit(1)
                            .execute()
                        )
                        if existing.data:
                            existing_id = str(existing.data[0]["id"])
                            st.info("Agent with that email already exists; selecting existing record.")
                            st.session_state["show_add_agent"] = False
                            st.session_state["agent_sel_pending"] = existing_id
                            st.rerun()
                        else:
                            st.warning("Duplicate email reported but existing record not found.")
                    except Exception as e2:
                        st.error(f"Could not select existing agent: {e2}")
                else:
                    st.error(f"Could not create agent: {e}")

# Venue add sub-form
if st.session_state.get("show_add_venue"):
    with venue_add_box.container():
        st.markdown("**➕ Add New Venue**")
        v1, v2 = st.columns([1, 1])
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
                payload = {
                    "name": (new_v_name or "").strip(),
                    "address_line1": (new_v_addr1 or "").strip(),
                    "address_line2": (st.session_state.get("new_v_addr2") or "").strip(),
                    "city": (new_v_city or "").strip(),
                    "state": (st.session_state.get("new_v_state") or "").strip(),
                    "postal_code": (st.session_state.get("new_v_zip") or "").strip(),
                    "phone": (new_v_phone or "").strip(),
                }
                new_row = _insert_row("venues", _filter_to_schema("venues", payload))
                new_id = str(new_row["id"]) if new_row and "id" in new_row else None
            if new_id:
                st.cache_data.clear()
                st.session_state["show_add_venue"] = False
                st.session_state["venue_sel_pending"] = new_id
                st.success("Venue created.")
                st.rerun()

# Sound Tech add sub-form
if st.session_state.get("show_add_sound"):
    with sound_add_box.container():
        st.markdown("**➕ Add New Sound Tech**")
        s1, s2 = st.columns([1, 1])
        with s1:
            new_st_name   = st.text_input("Display Name", key="new_st_name")
            new_st_phone  = st.text_input("Phone (optional)", key="new_st_phone")
        with s2:
            new_st_company = st.text_input("Company (optional)", key="new_st_company")
            new_st_email   = st.text_input("Email (optional)", key="new_st_email")
        if st.button("Create Sound Tech", key="create_sound_btn"):
            new_id = None
            if (new_st_name or "").strip():
                payload = {
                    "display_name": (new_st_name or "").strip(),
                    "company": (new_st_company or "").strip(),
                    "phone": (new_st_phone or "").strip(),
                    "email": (new_st_email or "").strip(),
                }
                new_row = _insert_row("sound_techs", _filter_to_schema("sound_techs", payload))
                new_id = str(new_row["id"]) if new_row and "id" in new_row else None
            if new_id:
                st.cache_data.clear()
                st.session_state["show_add_sound"] = False
                st.session_state["sound_sel_pending"] = new_id
                st.success("Sound Tech created.")
                st.rerun()

# Musician add (role-specific)
for role in ROLE_CHOICES:
    if st.session_state.get(f"show_add_mus__{role}", False):
        box = st.empty()
        with box.container():
            st.markdown(f"**➕ Add New Musician for {role}**")
            m1, m2, m3, m4 = st.columns([1, 1, 1, 1])
            with m1:
                new_mus_fn = st.text_input("First Name", key=f"new_mus_fn_{role}")
            with m2:
                new_mus_ln = st.text_input("Last Name", key=f"new_mus_ln_{role}")
            with m3:
                default_instr = role
                new_mus_instr = st.text_input("Instrument", value=default_instr, key=f"new_mus_instr_{role}")
            with m4:
                new_mus_stage = st.text_input("Stage Name (optional)", key=f"new_mus_stage_{role}")
            c1, c2 = st.columns([1, 1])
            with c1:
                if st.button("Create Musician", key=f"create_mus_btn_{role}"):
                    payload = {
                        "first_name": (new_mus_fn or "").strip() or None,
                        "last_name": (new_mus_ln or "").strip() or None,
                        "stage_name": (new_mus_stage or "").strip() or None,
                        "instrument": (new_mus_instr or role or "").strip() or None,
                        "active": True if "active" in _table_columns("musicians") else None,
                    }
                    new_row = _insert_row("musicians", _filter_to_schema("musicians", payload))
                    new_id = str(new_row["id"]) if new_row and "id" in new_row else None
                    if new_id:
                        st.cache_data.clear()
                        pre = st.session_state.get("preselect_role_ids", {})
                        pre[role] = new_id
                        st.session_state["preselect_role_ids"] = pre
                        st.session_state[f"show_add_mus__{role}"] = False
                        st.success(f"Musician created and preselected for {role}.")
                        st.rerun()
            with c2:
                if st.button("Cancel", key=f"cancel_mus_btn_{role}"):
                    st.session_state[f"show_add_mus__{role}"] = False
                    st.session_state[f"mus_sel_{role}"] = ""
                    st.rerun()

# =============================
# SAVE PATH (after submit)
# =============================
if submit:
    def _compose_datetimes(event_dt: date, start_t: time, end_t: time):
        start_dt = datetime.combine(event_dt, start_t)
        end_dt = datetime.combine(event_dt, end_t)
        if end_dt <= start_dt:  # ends after midnight
            end_dt += timedelta(days=1)
        return start_dt, end_dt

    # Use local vars from the form: event_date, start_time_in, end_time_in
    start_dt, end_dt = _compose_datetimes(event_date, start_time_in, end_time_in)
    if end_dt.date() > start_dt.date():
        st.info(
            f"This gig ends next day ({end_dt.strftime('%Y-%m-%d %I:%M %p')}). "
            "We’ll save event_date as the start date and keep your end time as entered."
        )

    agent_id_val = st.session_state.get("agent_sel")
    agent_id_val = agent_id_val if agent_id_val not in ("", "__ADD_AGENT__") else None
    venue_id_val = st.session_state.get("venue_sel")
    venue_id_val = venue_id_val if venue_id_val not in ("", "__ADD_VENUE__") else None
    sound_sel_val = st.session_state.get("sound_sel")
    sound_tech_id_val = (sound_sel_val if (sound_sel_val not in ("", "__ADD_SOUND__") and not st.session_state.get("sound_by_venue_in")) else None)

    gig_payload = {
        "title": (st.session_state.get("title_in") or None),
        "event_date": event_date.isoformat(),
        "start_time": start_time_in.strftime("%H:%M:%S"),
        "end_time":   end_time_in.strftime("%H:%M:%S"),
        "contract_status": st.session_state.get("status_in"),
        "fee": float(st.session_state.get("fee_in") or 0.0),
        "agent_id": agent_id_val,
        "venue_id": venue_id_val,
        "sound_tech_id": sound_tech_id_val,
        "is_private": bool(st.session_state.get("is_private_in", False)),
        "notes": (st.session_state.get("notes_in") or None),
        "sound_by_venue_name": (st.session_state.get("sv_name_in") or None),
        "sound_by_venue_phone": (st.session_state.get("sv_phone_in") or None),
    }

    for k in ("private_event_type", "organizer", "guest_of_honor", "private_contact", "private_contact_info", "additional_services"):
        if k in locals().get("private_vals", {}):
            gig_payload[k] = private_vals.get(k) or None

    gig_payload = _filter_to_schema("gigs", gig_payload)
    new_gig = _robust_insert("gigs", gig_payload)
    if not new_gig:
        st.stop()

    gig_id = str(new_gig.get("id", ""))

    if _table_exists("gig_musicians"):
        gm_rows: List[Dict] = []
        for role in ROLE_CHOICES:
            mid = st.session_state.get(f"mus_sel_{role}", "")
            if mid and not str(mid).startswith("__ADD_MUS__"):
                gm_rows.append(_filter_to_schema("gig_musicians", {
                    "gig_id": gig_id,
                    "role": role,
                    "musician_id": mid,
                }))
        if gm_rows:
            _insert_rows("gig_musicians", gm_rows)

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

    # Auto-sends
    try:
        if IS_ADMIN and st.session_state.get("autoc_send_agent_on_create", False) and agent_id_val:
            from tools.send_agent_confirm import send_agent_confirm
            with st.status("Emailing agent…", state="running") as s:
                send_agent_confirm(gig_id)
                s.update(label="Agent confirmation sent", state="complete")
            st.toast("📧 Agent emailed.", icon="📧")
    except Exception as e:
        st.warning(f"Agent auto-send failed: {e}")

    try:
        if IS_ADMIN and (not st.session_state.get("sound_by_venue_in", False)) and st.session_state.get("autoc_send_st_on_create", False) and sound_tech_id_val:
            from tools.send_soundtech_confirm import send_soundtech_confirm
            with st.status("Sending sound-tech confirmation…", state="running") as s:
                send_soundtech_confirm(gig_id)
                s.update(label="Sound-tech confirmation sent", state="complete")
            st.toast("📧 Sound-tech emailed.", icon="📧")
    except Exception as e:
        st.warning(f"Sound-tech auto-send failed: {e}")

    try:
        if IS_ADMIN and st.session_state.get("autoc_send_players_on_create", False):
            from tools.send_player_confirms import send_player_confirms
            with st.status("Emailing players…", state="running") as s:
                send_player_confirms(gig_id)
                s.update(label="Player confirmations sent", state="complete")
            st.toast("📧 Players emailed.", icon="📧")
    except Exception as e:
        st.warning(f"Player auto-send failed: {e}")
