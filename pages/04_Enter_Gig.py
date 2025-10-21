# pages/04_Enter_Gig.py
import os
from datetime import datetime, date, time, timedelta
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

# Auth gate (mirrors your other pages)
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
    except Exception as e:
        st.warning(f"{table} query failed: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=60)
def _table_columns(table: str) -> Set[str]:
    # Probe by selecting one row; infer columns
    df = _select_df(table, "*", limit=1)
    return set(df.columns) if not df.empty else set()

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
        # If we couldn't infer columns, try best-effort insert
        return data
    return {k: v for k, v in data.items() if k in cols}

# -----------------------------
# Reference data (dropdowns)
# -----------------------------
venues_df = _select_df("venues", "id, name, address_line1, address_line2, city, state, postal_code")
sound_df  = _select_df("sound_techs", "id, display_name, company")
mus_df    = _select_df("musicians", "id, first_name, last_name, instrument, is_active")
agents_df = _select_df("agents", "id, name, company")

def _opt_label(val, fallback=""):
    return str(val) if pd.notna(val) and str(val).strip() else fallback

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
    agents_df["__label"] = agents_df.apply(
        lambda r: " ".join([_opt_label(r.get("name"), "Unnamed Agent"), f"({_opt_label(r.get('company'), '').strip()})"]).strip().rstrip("()"),
        axis=1,
    )
    agent_options = [(row["__label"], str(row["id"])) for _, row in agents_df.iterrows()]

mus_options: List[Tuple[str, str]] = []
if not mus_df.empty and "id" in mus_df.columns:
    mus_df["__name"] = mus_df.apply(
        lambda r: " ".join([_opt_label(r.get("first_name"), ""), _opt_label(r.get("last_name"), "")]).strip() or "Unnamed Musician",
        axis=1,
    )
    # Prefer active musicians first
    mus_df["__is_active"] = mus_df.get("is_active", True)
    mus_df = mus_df.sort_values(by="__is_active", ascending=False)
    mus_options = [(row["__name"], str(row["id"])) for _, row in mus_df.iterrows()]

# Roles per wireframe (adjust later without touching DB)
ROLE_CHOICES = [
    "Male Vocals", "Female Vocals",
    "Keyboard", "Drums", "Guitar", "Bass",
    "Trumpet", "Saxophone", "Trombone",
]

# Admin gate (optional)
IS_ADMIN = bool(st.session_state.get("is_admin", True))  # default True so you can see controls now

# -----------------------------
# Form UI
# -----------------------------
with st.form("enter_gig_form", clear_on_submit=False):  # removed border=True for compatibility
    st.subheader("Event Basics")

    c1, c2, c3 = st.columns([1,1,1])
    with c1:
        title = st.text_input("Title (optional)", placeholder="e.g., Spring Gala")
        event_date = st.date_input("Date of Performance", value=date.today())
        start_t = st.time_input("Start Time", value=time(20, 0))
    with c2:
        end_t   = st.time_input("End Time", value=time(23, 0))
        contract_status = st.selectbox("Status", options=["Pending", "Hold", "Confirmed"], index=0)
        fee = st.number_input("Fee", min_value=0.0, step=100.0, format="%.2f")
    with c3:
        band_name = st.text_input("Band (optional)", placeholder="PRS")
        agent_id = st.selectbox("Agent (optional)", options=[("(none)", "")] + agent_options, format_func=lambda x: x[0] if isinstance(x, tuple) else "(none)")
        commission_pct = st.number_input("Commission % (optional)", min_value=0.0, max_value=100.0, step=0.5, format="%.1f")

    st.markdown("---")
    st.subheader("Venue & Sound")

    v1, v2 = st.columns([1,1])
    with v1:
        venue_id = st.selectbox("Venue", options=[("(select venue)", "")] + venue_options, index=0, format_func=lambda x: x[0] if isinstance(x, tuple) else "(select venue)")
        is_private = st.checkbox("Private Event?", value=False)
        eligible_1099 = st.checkbox("1099 Eligible", value=False)
    with v2:
        sound_by_venue = st.checkbox("Sound provided by venue?", value=False)
        sound_tech_sel = st.selectbox(
            "Confirmed Sound Tech",
            options=[("(none)", "")] + sound_options,
            index=0,
            disabled=sound_by_venue,
            format_func=lambda x: x[0] if isinstance(x, tuple) else "(none)",
        )

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
    lineup_selections = []  # type: List[Tuple[str, str]]
    for idx, role in enumerate(ROLE_CHOICES):
        with lineup_cols[idx % 3]:
            sel = st.selectbox(
                role,
                options=[("(unassigned)", "")] + mus_options,
                index=0,
                format_func=lambda x: x[0] if isinstance(x, tuple) else "(unassigned)",
                key=f"role_{role}",
            )
            if isinstance(sel, tuple) and sel[1]:
                lineup_selections.append((role, sel[1]))

    st.markdown("---")
    st.subheader("Notes")
    notes = st.text_area("Notes / Special Instructions (optional)", height=100, placeholder="Load-in details, parking, dress code, etc.")

    # Private fields (shown only if is_private)
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

    # Admin-only finance widgets (keep lightweight)
    deposit_rows = []  # type: List[Dict]
    if IS_ADMIN:
        st.markdown("---")
        st.subheader("Finance (Admin Only)")
        add_deps = st.number_input("Number of deposits (0–4)", min_value=0, max_value=4, step=1, value=0)
        for i in range(int(add_deps)):
            cdl, cda, cdm = st.columns([1,1,1])
            with cdl:
                due = st.date_input(f"Deposit {i+1} due", value=event_date, key=f"dep_due_{i}")
            with cda:
                amt = st.number_input(f"Deposit {i+1} amount", min_value=0.0, step=50.0, format="%.2f", key=f"dep_amt_{i}")
            with cdm:
                is_pct = st.checkbox(f"Deposit {i+1} is % of fee", value=False, key=f"dep_pct_{i}")
            deposit_rows.append({"sequence": i+1, "due_date": due, "amount": amt, "is_percentage": is_pct})

    submitted = st.form_submit_button("Save Gig", type="primary")

# -----------------------------
# Submit handler
# -----------------------------
if submitted:
    # Build start/end for storage; many schemas store TIME only, so keep as "HH:MM:SS"
    def _to_time_str(t: time) -> str:
        return f"{t.hour:02d}:{t.minute:02d}:00"

    start_time_str = _to_time_str(start_t)
    end_time_str = _to_time_str(end_t)

    # Build main gig payload
    agent_id_val = agent_id[1] if isinstance(agent_id, tuple) else ""
    sound_tech_id_val = sound_tech_sel[1] if (isinstance(sound_tech_sel, tuple) and not sound_by_venue) else ""

    gig_payload = {
        "title": title or None,
        "band_name": band_name or None,
        "event_date": event_date.isoformat() if isinstance(event_date, (date, datetime)) else event_date,
        "start_time": start_time_str,
        "end_time": end_time_str,
        "contract_status": contract_status,
        "fee": float(fee) if fee else None,
        "agent_id": agent_id_val or None,
        "commission_pct": float(commission_pct) if commission_pct else None,
        "venue_id": (venue_id[1] if isinstance(venue_id, tuple) else "") or None,
        "sound_tech_id": sound_tech_id_val or None,
        "is_private": bool(is_private),
        "notes": notes or None,
        # Sound by venue (optional columns)
        "sound_by_venue_name": sound_by_venue_name or None,
        "sound_by_venue_phone": sound_by_venue_phone or None,
        # 1099 eligibility (optional column)
        "eligible_1099": bool(eligible_1099),
        # Private fields (optional columns)
        **({k: (v or None) for k, v in private_vals.items()} if is_private else {}),
    }

    # Filter payload to actual columns in your gigs table
    gig_payload = _filter_to_schema("gigs", gig_payload)

    # Insert the gig
    new_gig = _insert_row("gigs", gig_payload)
    if not new_gig:
        st.stop()

    gig_id = str(new_gig.get("id", ""))

    # Insert lineup -> gig_musicians (if table exists)
    gm_cols = _table_columns("gig_musicians")
    if gm_cols:
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
    gd_cols = _table_columns("gig_deposits")
    if gd_cols and IS_ADMIN and deposit_rows:
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

    # Success message + quick summary
    st.success("Gig saved successfully ✅")
    with st.expander("View saved details"):
        # Re-fetch joined summary bits for display
        _g = pd.DataFrame([new_gig])
        try:
            if "venue_id" in _g.columns and _g.at[0, "venue_id"]:
                v = venues_df[venues_df["id"].astype(str) == str(_g.at[0, "venue_id"])]
            else:
                v = pd.DataFrame()
            if not sound_df.empty and "sound_tech_id" in _g.columns and _g.at[0, "sound_tech_id"]:
                s = sound_df[sound_df["id"].astype(str) == str(_g.at[0, "sound_tech_id"])]
            else:
                s = pd.DataFrame()
        except Exception:
            v, s = pd.DataFrame(), pd.DataFrame()

        st.write({
            "title": new_gig.get("title"),
            "band_name": new_gig.get("band_name"),
            "event_date": new_gig.get("event_date"),
            "start_time": new_gig.get("start_time"),
            "end_time": new_gig.get("end_time"),
            "status": new_gig.get("contract_status"),
            "fee": new_gig.get("fee"),
            "venue": (v.iloc[0]["name"] if not v.empty and "name" in v.columns else None),
            "sound_tech": (s.iloc[0]["display_name"] if not s.empty and "display_name" in s.columns else None),
            "notes": new_gig.get("notes"),
        })

    st.info("Tip: Open the Schedule View to verify the new gig appears with Venue / Location / Sound.")

# -----------------------------
# Small style polish
# -----------------------------
st.markdown("""
<style>
/* Tighter spacing for form sections */
section[data-testid="stForm"] .st-emotion-cache-ue6h4q { gap: 0.5rem; }
</style>
""", unsafe_allow_html=True)
