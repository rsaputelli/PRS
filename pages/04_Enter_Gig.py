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

IS_ADMIN = bool(st.session_state.get("is_admin", True))  # keep admin visible now

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

def _id_index(options: List[Tuple[str,str]], target_id: Optional[str]) -> int:
    if not target_id:
        return 0
    for i, (_, oid) in enumerate([("(none)","")] + options + [("(+ Add New)", "__ADD__")]):
        if oid == target_id:
            return i
    return 0

# -----------------------------
# Defaults for preselect after add
# -----------------------------
default_agent_id  = st.session_state.get("preselect_agent_id")
default_venue_id  = st.session_state.get("preselect_venue_id")
default_sound_id  = st.session_state.get("preselect_sound_id")
# musician preselects are per role; keep simple for now

# -----------------------------
# Form UI (gig details only)
# -----------------------------
with st.form("enter_gig_form", clear_on_submit=False):
    st.subheader("Event Basics")

    # 12-hour time pickers (hour, minute, am/pm)
    c1, c2, c3 = st.columns([1,1,1])
    with c1:
        title = st.text_input("Title (optional)", placeholder="e.g., Spring Gala")
        event_date = st.date_input("Date of Performance", value=date.today())
        h1, m1, a1 = st.columns([1,1,1])
        start_hour = h1.selectbox("Start Hour", list(range(1,13)), index=7)
        start_min  = m1.selectbox("Start Min", [0, 15, 30, 45], index=0)
        start_ampm = a1.selectbox("AM/PM", ["PM","AM"], index=0)  # default 8 PM
    with c2:
        h2, m2, a2 = st.columns([1,1,1])
        end_hour = h2.selectbox("End Hour", list(range(1,13)), index=10)  # default 11
        end_min  = m2.selectbox("End Min", [0, 15, 30, 45], index=0)
        end_ampm = a2.selectbox("AM/PM ", ["PM","AM"], index=0)
        contract_status = st.selectbox("Status", options=["Pending", "Hold", "Confirmed"], index=0)
        fee = st.number_input("Fee", min_value=0.0, step=100.0, format="%.2f")
    with c3:
        band_name = st.text_input("Band (optional)", placeholder="PRS")
        agent_choices = [("(none)", "")] + agent_options + [("(+ Add New Agent)", "__ADD__")]
        agent_index = _id_index(agent_options, default_agent_id)
        agent_raw = st.selectbox("Agent", options=agent_choices, index=agent_index, format_func=lambda x: x[0] if isinstance(x, tuple) else x)
        commission_pct = st.number_input("Commission % (optional)", min_value=0.0, max_value=100.0, step=0.5, format="%.1f")

    st.markdown("---")
    st.subheader("Venue & Sound")

    v1, v2 = st.columns([1,1])
    with v1:
        venue_choices = [("(select venue)", "")] + venue_options + [("(+ Add New Venue)", "__ADD__")]
        venue_index = _id_index(venue_options, default_venue_id)
        venue_sel = st.selectbox("Venue", options=venue_choices, index=venue_index, format_func=lambda x: x[0] if isinstance(x, tuple) else x)
        is_private = st.checkbox("Private Event?", value=False)
        eligible_1099 = st.checkbox("1099 Eligible", value=False)
    with v2:
        sound_choices = [("(none)", "")] + sound_options + [("(+ Add New Sound Tech)", "__ADD__")]
        sound_index = _id_index(sound_options, default_sound_id)
        sound_tech_sel = st.selectbox("Confirmed Sound Tech", options=sound_choices, index=sound_index,
                                      format_func=lambda x: x[0] if isinstance(x, tuple) else x)
        sound_by_venue = st.checkbox("Sound provided by venue?", value=False)

    if sound_by_venue:
        sv1, sv2 = st.columns([1,1])
        with sv1:
            sound_by_venue_name = st.text_input("Venue Sound Company/Contact (optional)")
        with sv2:
            sound_by_venue_phone = st.text_input("Venue Sound Phone/Email (optional)")
    else:
        sound_by_venue_name = ""
        sound_by_venue_phone = ""

    st.markdown("---")
    st.subheader("Lineup (Role Assignments)")
    lineup_cols = st.columns(3)
    lineup_selections: List[Tuple[str, str]] = []
    for idx, role in enumerate(ROLE_CHOICES):
        with lineup_cols[idx % 3]:
            mus_choices = [("(unassigned)", "")] + mus_options + [("(+ Add New Musician)", "__ADD__")]
            sel = st.selectbox(role, options=mus_choices, index=0, format_func=lambda x: x[0] if isinstance(x, tuple) else x, key=f"role_{role}")
            if isinstance(sel, tuple) and sel[1] == "__ADD__":
                st.session_state["show_add_musician"] = True
                st.session_state["add_musician_for_role"] = role
            elif isinstance(sel, tuple) and sel[1]:
                lineup_selections.append((role, sel[1]))

    st.markdown("---")
    st.subheader("Notes")
    notes = st.text_area("Notes / Special Instructions (optional)", height=100, placeholder="Load-in details, parking, dress code, etc.")

    # Private fields
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

    submitted = st.form_submit_button("Save Gig", type="primary")

# -----------------------------
# Finance (Admin) OUTSIDE form
# -----------------------------
deposit_rows: List[Dict] = []
if IS_ADMIN:
    st.markdown("---")
    st.subheader("Finance (Admin Only)")
    add_deps = st.number_input("Number of deposits (0–4)", min_value=0, max_value=4, step=1, value=0, key="num_deposits")
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
# Sub-forms (OUTSIDE main form)
# These actually fire and insert rows.
# -----------------------------
# Add Agent
if isinstance(agent_raw, tuple) and agent_raw[1] == "__ADD__":
    st.markdown("### ➕ Add New Agent")
    a1, a2 = st.columns([1,1])
    with a1:
        new_agent_name = st.text_input("Agent Name")
    with a2:
        new_agent_company = st.text_input("Company (optional)")
    if st.button("Create Agent"):
        new_id = _create_agent(new_agent_name.strip(), new_agent_company.strip())
        if new_id:
            st.cache_data.clear()
            st.session_state["preselect_agent_id"] = new_id
            st.success("Agent created.")
            st.rerun()

# Add Musician
if st.session_state.get("show_add_musician"):
    st.markdown("### ➕ Add New Musician")
    m1, m2, m3, m4 = st.columns([1,1,1,1])
    with m1:
        new_mus_fn = st.text_input("First Name")
    with m2:
        new_mus_ln = st.text_input("Last Name")
    with m3:
        new_mus_instr = st.text_input("Instrument")
    with m4:
        new_mus_stage = st.text_input("Stage Name (optional)")
    if st.button("Create Musician"):
        new_id = _create_musician(new_mus_fn.strip(), new_mus_ln.strip(), new_mus_instr.strip(), new_mus_stage.strip())
        if new_id:
            st.cache_data.clear()
            # We could preselect per-role later if needed
            st.session_state["show_add_musician"] = False
            st.success("Musician created.")
            st.rerun()

# Add Sound Tech
if isinstance(sound_tech_sel, tuple) and sound_tech_sel[1] == "__ADD__":
    st.markdown("### ➕ Add New Sound Tech")
    s1, s2 = st.columns([1,1])
    with s1:
        new_st_name = st.text_input("Display Name")
        new_st_phone = st.text_input("Phone (optional)")
    with s2:
        new_st_company = st.text_input("Company (optional)")
        new_st_email = st.text_input("Email (optional)")
    if st.button("Create Sound Tech"):
        new_id = _create_soundtech(new_st_name.strip(), new_st_company.strip(), new_st_phone.strip(), new_st_email.strip())
        if new_id:
            st.cache_data.clear()
            st.session_state["preselect_sound_id"] = new_id
            st.success("Sound Tech created.")
            st.rerun()

# Add Venue
if isinstance(venue_sel, tuple) and venue_sel[1] == "__ADD__":
    st.markdown("### ➕ Add New Venue")
    v1, v2 = st.columns([1,1])
    with v1:
        new_v_name = st.text_input("Venue Name")
        new_v_addr1 = st.text_input("Address Line 1")
        new_v_city = st.text_input("City")
        new_v_phone = st.text_input("Phone (optional)")
    with v2:
        new_v_addr2 = st.text_input("Address Line 2 (optional)")
        new_v_state = st.text_input("State")
        new_v_zip = st.text_input("Postal Code")
    if st.button("Create Venue"):
        new_id = _create_venue(new_v_name.strip(), new_v_addr1.strip(), new_v_addr2.strip(), new_v_city.strip(), new_v_state.strip(), new_v_zip.strip(), new_v_phone.strip())
        if new_id:
            st.cache_data.clear()
            st.session_state["preselect_venue_id"] = new_id
            st.success("Venue created.")
            st.rerun()

# -----------------------------
# Submit handler
# -----------------------------
if submitted:
    # Build start/end (store as HH:MM:SS)
    start_t = _time_from_parts(start_hour, start_min, start_ampm)
    end_t   = _time_from_parts(end_hour, end_min, end_ampm)

    def _to_time_str(t: time) -> str:
        return f"{t.hour:02d}:{t.minute:02d}:00"

    start_time_str = _to_time_str(start_t)
    end_time_str = _to_time_str(end_t)

    # Resolve IDs
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
        "commission_pct": float(commission_pct) if commission_pct else None,
        "venue_id": venue_id_val,
        "sound_tech_id": sound_tech_id_val,
        "is_private": bool(is_private),
        "notes": (notes or None),
        "sound_by_venue_name": (sound_by_venue_name or None),
        "sound_by_venue_phone": (sound_by_venue_phone or None),
        "eligible_1099": bool(eligible_1099),
        # Private fields (optional)
        **({k: (v or None) for k, v in private_vals.items()} if is_private else {}),
    }
    gig_payload = _filter_to_schema("gigs", gig_payload)

    new_gig = _insert_row("gigs", gig_payload)
    if not new_gig:
        st.stop()

    gig_id = str(new_gig.get("id", ""))

    # Insert lineup -> gig_musicians (if table exists)
    if _table_exists("gig_musicians") and lineup_selections:
        gm_rows: List[Dict] = []
        for role, musician_id in lineup_selections:
            gm_rows.append(_filter_to_schema("gig_musicians", {
                "gig_id": gig_id,
                "role": role,
                "musician_id": musician_id,
            }))
        if gm_rows:
            _insert_rows("gig_musicians", gm_rows)

    # Insert deposits -> gig_deposits (if table exists)
    if IS_ADMIN and _table_exists("gig_deposits") and deposit_rows:
        rows: List[Dict] = []
        for dep in deposit_rows:
            due_date_val = dep["due_date"]
            rows.append(_filter_to_schema("gig_deposits", {
                "gig_id": gig_id,
                "sequence": dep["sequence"],
                "due_date": due_date_val.isoformat() if isinstance(due_date_val, (date, datetime)) else due_date_val,
                "amount": float(dep["amount"]) if dep["amount"] else 0.0,
                "is_percentage": bool(dep["is_percentage"]),
            }))
        _insert_rows("gig_deposits", rows)

    # Success message + quick summary (12-hr times)
    st.success("Gig saved successfully ✅")
    st.write({
        "title": new_gig.get("title"),
        "band_name": new_gig.get("band_name"),
        "event_date": new_gig.get("event_date"),
        "start_time (12-hr)": _format_12h(start_t),
        "end_time (12-hr)": _format_12h(end_t),
        "status": new_gig.get("contract_status"),
        "fee": new_gig.get("fee"),
        "notes": new_gig.get("notes"),
    })
    st.info("Open the Schedule View to verify the new gig appears with Venue / Location / Sound.")
