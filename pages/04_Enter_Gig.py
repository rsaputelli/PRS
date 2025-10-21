# pages/04_Enter_Gig.py
import os
from datetime import datetime, date, time
import pandas as pd
import streamlit as st
from supabase import create_client, Client
from typing import Optional, Dict, List, Set, Tuple

st.set_page_config(page_title="Enter Gig", page_icon="📝", layout="wide")
st.title("📝 Enter Gig")

# -----------------------------
# Supabase helpers
# -----------------------------
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

# Auth gate
if "user" not in st.session_state or not st.session_state["user"]:
    st.error("Please sign in from the Login page.")
    st.stop()

# Reattach session for RLS
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

# 12-hour time helpers
def _time_from_parts(hour12: int, minute: int, ampm: str) -> time:
    hour24 = (hour12 % 12) + (12 if ampm.upper() == "PM" else 0)
    return time(hour24, minute)

def _format_12h(t: time) -> str:
    dt = datetime(2000,1,1, t.hour, t.minute)
    s = dt.strftime("%I:%M %p")
    return s.lstrip("0")

# -----------------------------
# Reference data (dropdowns)
# -----------------------------
venues_df = _select_df("venues", "*")
sound_df  = _select_df("sound_techs", "*")
mus_df    = _select_df("musicians", "*")
agents_df = _select_df("agents", "*")

venue_options: List[Tuple[str, str]] = []
if not venues_df.empty and "id" in venues_df.columns:
    venues_df["__label"] = venues_df.apply(
        lambda r: ", ".join([x for x in [
            _opt_label(r.get("name"), "Unnamed Venue"),
            " ".join([_opt_label(r.get("city"), ""), _opt_label(r.get("state"), "")]).strip()
        ] if x]),
        axis=1,
    )
    venue_options = [(row["__label"], str(row["id"])) for _, row in venues_df.iterrows()]

sound_options: List[Tuple[str, str]] = []
if not sound_df.empty and "id" in sound_df.columns:
    sound_df["__label"] = sound_df.apply(
        lambda r: " ".join([_opt_label(r.get("display_name"), "Unnamed"), f"({_opt_label(r.get('company'), '').strip()})"]).strip().rstrip("()"),
        axis=1,
    )
    sound_options = [(row["__label"], str(row["id"])) for _, row in sound_df.iterrows()]

agent_options: List[Tuple[str, str]] = []
if not agents_df.empty and "id" in agents_df.columns:
    def _agent_label(r):
        if pd.notna(r.get("name")) and str(r.get("name")).strip():
            return str(r.get("name")).strip()
        fn = _opt_label(r.get("first_name"), "")
        ln = _opt_label(r.get("last_name"), "")
        if fn or ln:
            nm = (fn + " " + ln).strip()
            return (nm + f" ({_opt_label(r.get('company'), '')})").strip().rstrip("()")
        return _opt_label(r.get("company"), "Unnamed Agent")
    agents_df["__label"] = agents_df.apply(_agent_label, axis=1)
    agent_options = [(row["__label"], str(row["id"])) for _, row in agents_df.iterrows()]

mus_options: List[Tuple[str, str]] = []
if not mus_df.empty and "id" in mus_df.columns:
    mus_df["__name"] = mus_df.apply(
        lambda r: (" ".join([_opt_label(r.get("first_name"), ""), _opt_label(r.get("last_name"), "")]).strip()
                   or _opt_label(r.get("stage_name"), "") or "Unnamed Musician"),
        axis=1,
    )
    if "active" in mus_df.columns:
        mus_df = mus_df.sort_values(by="active", ascending=False)
    mus_options = [(row["__name"], str(row["id"])) for _, row in mus_df.iterrows()]

ROLE_CHOICES = [
    "Male Vocals", "Female Vocals",
    "Keyboard", "Drums", "Guitar", "Bass",
    "Trumpet", "Saxophone", "Trombone",
]

IS_ADMIN = bool(st.session_state.get("is_admin", True))

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

def _find_index_by_id(options: List[Tuple[str,str]], target_id: Optional[str]) -> int:
    if not target_id:
        return 0
    for i, (_, oid) in enumerate(options):
        if oid == target_id:
            return i
    return 0

# -----------------------------
# Session defaults
# -----------------------------
st.session_state.setdefault("preselect_agent_id", None)
st.session_state.setdefault("preselect_venue_id", None)
st.session_state.setdefault("preselect_sound_id", None)
st.session_state.setdefault("preselect_role_ids", {})  # role -> musician_id
st.session_state.setdefault("show_add_musician", False)
st.session_state.setdefault("add_mus_role", None)

# -----------------------------
# Event Basics
# -----------------------------
st.subheader("Event Basics")

eb1, eb2, eb3 = st.columns([1,1,1])
with eb1:
    title = st.text_input("Title (optional)", placeholder="e.g., Spring Gala")
    event_date = st.date_input("Date of Performance", value=date.today())

# Start/End grouped side-by-side
with eb2:
    st.markdown("**Start Time**")
    srow1, srow2 = st.columns([1,1])
    start_hour = srow1.selectbox("Hour", list(range(1,13)), index=7, key="start_hour")
    start_min  = srow2.selectbox("Min", [0, 15, 30, 45], index=0, key="start_min")
    start_ampm = st.selectbox("AM/PM", ["PM","AM"], index=0, key="start_ampm")

with eb3:
    st.markdown("**End Time**")
    erow1, erow2 = st.columns([1,1])
    end_hour = erow1.selectbox("Hour ", list(range(1,13)), index=10, key="end_hour")
    end_min  = erow2.selectbox("Min ", [0, 15, 30, 45], index=0, key="end_min")
    end_ampm = st.selectbox("AM/PM ", ["PM","AM"], index=0, key="end_ampm")
    contract_status = st.selectbox("Status", options=["Pending", "Hold", "Confirmed"], index=0)
    fee = st.number_input("Fee", min_value=0.0, step=100.0, format="%.2f")

ag_col, band_col = st.columns([1,1])
with band_col:
    band_name = st.text_input("Band (optional)", placeholder="PRS")
with ag_col:
    agent_choices = [("(none)", "")] + agent_options + [("(+ Add New Agent)", "__ADD__")]
    agent_index = 1 + _find_index_by_id(agent_options, st.session_state["preselect_agent_id"])
    agent_raw = st.selectbox("Agent", options=agent_choices, index=agent_index, format_func=lambda x: x[0] if isinstance(x, tuple) else x, key="agent_sel")
agent_add_box = st.empty()

# -----------------------------
# Venue & Sound
# -----------------------------
st.markdown("---")
st.subheader("Venue & Sound")

vs1, vs2 = st.columns([1,1])
with vs1:
    venue_choices = [("(select venue)", "")] + venue_options + [("(+ Add New Venue)", "__ADD__")]
    venue_index = 1 + _find_index_by_id(venue_options, st.session_state["preselect_venue_id"])
    venue_sel = st.selectbox("Venue", options=venue_choices, index=venue_index, format_func=lambda x: x[0] if isinstance(x, tuple) else x, key="venue_sel")
    is_private = st.checkbox("Private Event?", value=False)
    eligible_1099 = st.checkbox("1099 Eligible", value=False)
venue_add_box = st.empty()

with vs2:
    sound_choices = [("(none)", "")] + sound_options + [("(+ Add New Sound Tech)", "__ADD__")]
    sound_index = 1 + _find_index_by_id(sound_options, st.session_state["preselect_sound_id"])
    sound_tech_sel = st.selectbox("Confirmed Sound Tech", options=sound_choices, index=sound_index, format_func=lambda x: x[0] if isinstance(x, tuple) else x, key="sound_sel")
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
# Lineup (Role Assignments)
# -----------------------------
st.markdown("---")
st.subheader("Lineup (Role Assignments)")

lineup_cols = st.columns(3)
lineup_selections: List[Tuple[str, str]] = []
preselect_roles: Dict[str, Optional[str]] = st.session_state.get("preselect_role_ids", {})
role_add_boxes: Dict[str, st.delta_generator.DeltaGenerator] = {}

for idx, role in enumerate(ROLE_CHOICES):
    with lineup_cols[idx % 3]:
        mus_choices = [("(unassigned)", "")] + mus_options + [("(+ Add New Musician)", "__ADD__")]
        pre_id = preselect_roles.get(role)
        if pre_id:
            base_idx = _find_index_by_id(mus_options, pre_id)
            sel_index = base_idx + 1
        else:
            sel_index = 0
        sel = st.selectbox(role, options=mus_choices, index=sel_index, key=f"role_{role}",
                           format_func=lambda x: x[0] if isinstance(x, tuple) else x)
        if isinstance(sel, tuple) and sel[1] == "__ADD__":
            st.session_state["show_add_musician"] = True
            st.session_state["add_mus_role"] = role
        elif isinstance(sel, tuple) and sel[1]:
            lineup_selections.append((role, sel[1]))
        role_add_boxes[role] = st.empty()

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
# Sub-forms NEAR triggers
# -----------------------------
# Agent add
if isinstance(agent_raw, tuple) and agent_raw[1] == "__ADD__":
    with st.container():
        st.markdown("**➕ Add New Agent**")
        a1, a2 = st.columns([1,1])
        with a1:
            new_agent_name = st.text_input("Agent Name", key="new_agent_name")
        with a2:
            new_agent_company = st.text_input("Company (optional)", key="new_agent_company")
        if st.button("Create Agent", key="create_agent_btn"):
            new_id = _create_agent((new_agent_name or "").strip(), (new_agent_company or "").strip())
            if new_id:
                st.cache_data.clear()
                st.session_state["preselect_agent_id"] = new_id
                st.success("Agent created.")
                st.rerun()

# Venue add
if isinstance(venue_sel, tuple) and venue_sel[1] == "__ADD__":
    with st.container():
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
if isinstance(sound_tech_sel, tuple) and sound_tech_sel[1] == "__ADD__":
    with st.container():
        st.markdown("**➕ Add New Sound Tech**")
        s1, s2 = st.columns([1,1])
        with s1:
            new_st_name   = st.text_input("Display Name", key="new_st_name")
            new_st_phone  = st.text_input("Phone (optional)", key="new_st_phone")
        with s2:
            new_st_company = st.text_input("Company (optional)", key="new_st_company")
            new_st_email   = st.text_input("Email (optional)", key="new_st_email")
        if st.button("Create Sound Tech", key="create_sound_btn"):
            new_id = _create_soundtech(
                (new_st_name or "").strip(), (new_st_company or "").strip(),
                (new_st_phone or "").strip(), (new_st_email or "").strip()
            )
            if new_id:
                st.cache_data.clear()
                st.session_state["preselect_sound_id"] = new_id
                st.success("Sound Tech created.")
                st.rerun()

# Musician add (near the role that triggered it) — PRE-FILLED INSTRUMENT
if st.session_state.get("show_add_musician"):
    role = st.session_state.get("add_mus_role")
    if role:
        with st.container():
            st.markdown(f"**➕ Add New Musician for {role}**")
            m1, m2, m3, m4 = st.columns([1,1,1,1])
            with m1:
                new_mus_fn = st.text_input("First Name", key="new_mus_fn")
            with m2:
                new_mus_ln = st.text_input("Last Name", key="new_mus_ln")
            with m3:
                # Pre-fill instrument with the role that triggered the add
                default_instr = st.session_state.get("new_mus_instr", role)
                new_mus_instr = st.text_input("Instrument", value=default_instr, key="new_mus_instr")
            with m4:
                new_mus_stage = st.text_input("Stage Name (optional)", key="new_mus_stage")
            colc1, colc2 = st.columns([1,1])
            with colc1:
                if st.button("Create Musician", key="create_mus_btn"):
                    new_id = _create_musician(
                        (new_mus_fn or "").strip(), (new_mus_ln or "").strip(),
                        (new_mus_instr or role or "").strip(), (new_mus_stage or "").strip()
                    )
                    if new_id:
                        st.cache_data.clear()
                        pre = st.session_state.get("preselect_role_ids", {})
                        pre[role] = new_id  # preselect for this role
                        st.session_state["preselect_role_ids"] = pre
                        st.session_state["show_add_musician"] = False
                        st.success(f"Musician created and preselected for {role}.")
                        st.rerun()
            with colc2:
                if st.button("Cancel", key="cancel_mus_btn"):
                    st.session_state["show_add_musician"] = False
                    st.session_state["add_mus_role"] = None
                    st.rerun()

# -----------------------------
# SAVE button
# -----------------------------
if st.button("💾 Save Gig", type="primary"):
    start_t = _time_from_parts(st.session_state["start_hour"], st.session_state["start_min"], st.session_state["start_ampm"])
    end_t   = _time_from_parts(st.session_state["end_hour"],   st.session_state["end_min"],   st.session_state["end_ampm"])

    def _to_time_str(t: time) -> str:
        return f"{t.hour:02d}:{t.minute:02d}:00"

    start_time_str = _to_time_str(start_t)
    end_time_str   = _to_time_str(end_t)

    agent_id_val = agent_raw[1] if isinstance(agent_raw, tuple) and agent_raw[1] not in ("", "__ADD__") else None
    venue_id_val = venue_sel[1] if isinstance(venue_sel, tuple) and venue_sel[1] not in ("", "__ADD__") else None
    sound_tech_id_val = (sound_tech_sel[1] if isinstance(sound_tech_sel, tuple) and sound_tech_sel[1] not in ("", "__ADD__") and not sound_by_venue else None)

    gig_payload = {
        "title": (title or None),
        "band_name": (band_name or None),
        "event_date": event_date.isoformat() if isinstance(event_date, (date, datetime)) else event_date,
        "start_time": start_time_str,
        "end_time": end_time_str,
        "contract_status": contract_status,
        "fee": float(fee) if fee else None,
        "agent_id": agent_id_val,
        "commission_pct": None if "commission_pct" not in locals() else float(commission_pct) if commission_pct else None,
        "venue_id": venue_id_val,
        "sound_tech_id": sound_tech_id_val,
        "is_private": bool(is_private),
        "notes": (notes or None),
        "sound_by_venue_name": (sound_by_venue_name or None),
        "sound_by_venue_phone": (sound_by_venue_phone or None),
    }
    for k, v in (private_vals or {}).items():
        gig_payload[k] = v or None
    gig_payload = _filter_to_schema("gigs", gig_payload)

    new_gig = _insert_row("gigs", gig_payload)
    if not new_gig:
        st.stop()

    gig_id = str(new_gig.get("id", ""))

    # gig_musicians
    if _table_exists("gig_musicians"):
        gm_rows: List[Dict] = []
        for role, musician_id in lineup_selections:
            gm_rows.append(_filter_to_schema("gig_musicians", {
                "gig_id": gig_id,
                "role": role,
                "musician_id": musician_id,
            }))
        if gm_rows:
            _insert_rows("gig_musicians", gm_rows)

    # gig_deposits
    if IS_ADMIN and _table_exists("gig_deposits"):
        rows: List[Dict] = []
        n = int(st.session_state.get("num_deposits", 0))
        for i in range(n):
            due = st.session_state.get(f"dep_due_{i}", date.today())
            amt = float(st.session_state.get(f"dep_amt_{i}", 0.0) or 0.0)
            is_pct = bool(st.session_state.get(f"dep_pct_{i}", False))
            rows.append(_filter_to_schema("gig_deposits", {
                "gig_id": gig_id,
                "sequence": i+1,
                "due_date": due.isoformat() if isinstance(due, (date, datetime)) else due,
                "amount": amt,
                "is_percentage": is_pct,
            }))
        if rows:
            _insert_rows("gig_deposits", rows)

    st.success("Gig saved successfully ✅")
    st.write({
        "title": new_gig.get("title"),
        "event_date": new_gig.get("event_date"),
        "start_time (12-hr)": _format_12h(start_t),
        "end_time (12-hr)": _format_12h(end_t),
        "status": new_gig.get("contract_status"),
        "fee": new_gig.get("fee"),
    })
    st.info("Open the Schedule View to verify the new gig appears with Venue / Location / Sound.")
