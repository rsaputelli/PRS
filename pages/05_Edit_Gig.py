# =============================
# File: pages/05_Edit_Gig.py
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
# Page config + Header
# -----------------------------
st.set_page_config(page_title="Edit Gig", page_icon="✏️", layout="wide")
render_header(title="Edit Gig", emoji="✏️")

# -----------------------------
# Auth gate (logged-in check only)
# -----------------------------
if "user" not in st.session_state or not st.session_state["user"]:
    st.error("Please sign in from the Login page.")
    st.stop()

# -----------------------------
# Supabase helpers (aligned with other pages)
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

if st.session_state.get("sb_access_token") and st.session_state.get("sb_refresh_token"):
    try:
        sb.auth.set_session(
            access_token=st.session_state["sb_access_token"],
            refresh_token=st.session_state["sb_refresh_token"],
        )
    except Exception as e:
        st.warning(f"Could not attach session; proceeding with limited access. ({e})")

# -----------------------------
# Admin gate (robust)
# -----------------------------
def _norm_admin_emails(raw):
    if raw is None:
        return set()
    if isinstance(raw, (list, tuple, set)):
        return {str(x).strip().lower() for x in raw}
    # comma/semicolon/space separated accepted
    return {p.strip().lower() for p in str(raw).replace(";", ",").split(",") if p.strip()}

def _get_authed_user():
    # Prefer your login flow's session user
    u = st.session_state.get("user")
    if u:
        return u
    # Fallback to Supabase auth if available
    try:
        gu = sb.auth.get_user()
        return gu.user if getattr(gu, "user", None) else None
    except Exception:
        return None

def _profiles_admin_lookup(user_email: str, user_id: str) -> bool:
    """True if profiles table marks this user as admin."""
    try:
        # Try by user id-ish fields
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

        # Fallback by email
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

# Debug expander removed after admin check verification
# with st.expander("Admin check (debug)"):
    # st.write({
        # "email": user_email,
        # "session_state.is_admin": admin_from_session,
        # "secrets ADMIN_EMAILS hit": admin_from_secrets,
        # "profiles says admin": admin_from_profiles,
        # "IS_ADMIN (final)": IS_ADMIN,
    # })

if not IS_ADMIN:
    st.error("Only admins may edit gigs.")
    st.stop()

# -----------------------------
# Data helpers
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
# Reference data
# -----------------------------
venues_df = _select_df("venues", "*")
sound_df  = _select_df("sound_techs", "*")
mus_df    = _select_df("musicians", "*")

# Label helpers
def _opt_label(val, fallback=""):
    return str(val) if pd.notna(val) and str(val).strip() else fallback

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

# Roles
ROLE_CHOICES = [
    "Male Vocals", "Female Vocals",
    "Keyboard", "Drums", "Guitar", "Bass",
    "Trumpet", "Saxophone", "Trombone",
]

# -----------------------------
# Select a gig to edit
# -----------------------------
st.markdown("---")
st.subheader("Find a Gig")

@st.cache_data(ttl=30)
def _load_gigs() -> pd.DataFrame:
    df = _select_df("gigs", "*")
    if df.empty:
        return df
    # Normalize types
    if "event_date" in df.columns:
        df["event_date"] = pd.to_datetime(df["event_date"], errors="coerce").dt.date
    return df

gigs = _load_gigs()
if gigs.empty:
    st.info("No gigs found.")
    st.stop()

# Build sort keys
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

# Render selector list label
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

# Dropdown of all gigs (upcoming first)
gigs = gigs.sort_values(by=["_start_dt"], ascending=[True])
opts = list(gigs.index)
labels = {idx: _label_row(gigs.loc[idx]) for idx in opts}
sel_idx = st.selectbox("Select a gig to edit", options=opts, format_func=lambda i: labels.get(i, str(i)))
row = gigs.loc[sel_idx]

# -----------------------------
# Prefilled form
# -----------------------------
st.markdown("---")
st.subheader("Edit Details")

# AM/PM-style time selectors — reuse visual pattern
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
    title = st.text_input("Title (optional)", value=_opt_label(row.get("title"), ""))
    event_date = st.date_input("Date of Performance", value=row.get("event_date") or date.today())
with b2:
    contract_status = st.selectbox("Status", ["Pending", "Hold", "Confirmed"], index=["Pending", "Hold", "Confirmed"].index(row.get("contract_status") or "Pending"))
    fee_val = float(row.get("fee")) if pd.notna(row.get("fee")) else 0.0
    fee = st.number_input("Contracted Fee ($)", min_value=0.0, step=50.0, format="%.2f", value=fee_val)
    start_time_in = _ampm_time_input("Start", _to_time_obj(row.get("start_time")), key="edit_start")
    end_time_in   = _ampm_time_input("End",   _to_time_obj(row.get("end_time")),   key="edit_end")
with b3:
    band_name = st.text_input("Band (optional)", value=_opt_label(row.get("band_name"), ""))

# Venue & Sound
st.markdown("---")
st.subheader("Venue & Sound")
vs1, vs2 = st.columns([1, 1])

with vs1:
    # Venue
    venue_options = ["(none)"] + list(venue_labels.keys())
    def venue_fmt(x: str) -> str:
        if x == "(none)":
            return "(select venue)"
        return venue_labels.get(x, x)
    cur_vid = str(row.get("venue_id")) if pd.notna(row.get("venue_id")) else "(none)"
    venue_id_sel = st.selectbox("Venue", options=venue_options,
                                index=(venue_options.index(cur_vid) if cur_vid in venue_options else 0),
                                format_func=venue_fmt)

    # Private flag
    is_private = st.checkbox("Private Event?", value=bool(row.get("is_private")))

    # NEW: Sound provided toggle (venue-provided vs PRS-provided)
    sound_provided = st.checkbox("Venue provides sound?", value=bool(row.get("sound_provided")))

with vs2:
    # Confirmed sound tech (directory)
    sound_options = ["(none)"] + list(sound_labels.keys())
    def sound_fmt(x: str) -> str:
        if x == "(none)":
            return "(none)"
        return sound_labels.get(x, x)
    cur_sid = str(row.get("sound_tech_id")) if pd.notna(row.get("sound_tech_id")) else "(none)"
    sound_id_sel = st.selectbox("Confirmed Sound Tech", options=sound_options,
                                index=(sound_options.index(cur_sid) if cur_sid in sound_options else 0),
                                format_func=sound_fmt)

    # NEW: Sound fee only when PRS provides sound (i.e., venue does NOT provide)
    cur_fee = float(row.get("sound_fee") or 0.0)
    sound_fee = None
    if not sound_provided:
        sound_fee = st.number_input("Sound Fee ($)", min_value=0.0, step=25.0, format="%.2f", value=cur_fee)

# Optional free-text vendor (unchanged)
sv1, sv2 = st.columns([1, 1])
with sv1:
    sound_by_venue_name = st.text_input("Venue Sound Company/Contact (optional)",
                                        value=_opt_label(row.get("sound_by_venue_name"), ""))
with sv2:
    sound_by_venue_phone = st.text_input("Venue Sound Phone/Email (optional)",
                                         value=_opt_label(row.get("sound_by_venue_phone"), ""))


# Sound by venue (stored as text fields in gigs schema)
sv1, sv2 = st.columns([1, 1])
with sv1:
    sound_by_venue_name = st.text_input("Venue Sound Company/Contact (optional)", value=_opt_label(row.get("sound_by_venue_name"), ""))
with sv2:
    sound_by_venue_phone = st.text_input("Venue Sound Phone/Email (optional)", value=_opt_label(row.get("sound_by_venue_phone"), ""))

# Lineup (assignments)
st.markdown("---")
st.subheader("Lineup (Role Assignments)")

# Load current assignments
assigned_df = _select_df("gig_musicians", "*", where_eq={"gig_id": row.get("id")}) if _table_exists("gig_musicians") else pd.DataFrame()
cur_map: Dict[str, Optional[str]] = {}
if not assigned_df.empty:
    for _, r in assigned_df.iterrows():
        cur_map[str(r.get("role"))] = str(r.get("musician_id")) if pd.notna(r.get("musician_id")) else ""

# Build musician labels (active first if available)
if not mus_df.empty:
    if "active" in mus_df.columns:
        mus_df = mus_df.sort_values(by="active", ascending=False)

mus_labels: Dict[str, str] = {}
if not mus_df.empty and "id" in mus_df.columns:
    for _, r in mus_df.iterrows():
        rid = str(r["id"]) if pd.notna(r.get("id")) else None
        if not rid:
            continue
        nm = (
            _opt_label(r.get("stage_name"), "")
            or (" ".join([_opt_label(r.get("first_name"), ""), _opt_label(r.get("last_name"), "")]).strip())
            or _opt_label(r.get("display_name"), "")
            or "Unnamed Musician"
        )
        mus_labels[rid] = nm

line_cols = st.columns(3)
lineup: List[Dict] = []
for idx, role in enumerate(ROLE_CHOICES):
    with line_cols[idx % 3]:
        opts = ["(unassigned)"] + list(mus_labels.keys())
        def mfmt(x: str) -> str:
            if x == "(unassigned)":
                return "(unassigned)"
            return mus_labels.get(x, x)
        default_val = cur_map.get(role, "(unassigned)")
        sel = st.selectbox(role, options=opts, index=(opts.index(default_val) if default_val in opts else 0), format_func=mfmt, key=f"edit_role_{role}")
        if sel != "(unassigned)":
            lineup.append({"role": role, "musician_id": sel})

# Notes & Private details
st.markdown("---")
st.subheader("Notes")
notes = st.text_area("Notes / Special Instructions (optional)", height=100, value=_opt_label(row.get("notes"), ""))

private_vals = {}
if is_private:
    st.markdown("#### Private Event Details")
    p1, p2 = st.columns([1, 1])
    with p1:
        private_event_type = st.text_input("Type of Event", value=_opt_label(row.get("private_event_type"), ""))
        organizer = st.text_input("Organizer / Company", value=_opt_label(row.get("organizer"), ""))
        guest_of_honor = st.text_input("Guest(s) of Honor / Bride/Groom", value=_opt_label(row.get("guest_of_honor"), ""))
    with p2:
        private_contact = st.text_input("Primary Contact (name)", value=_opt_label(row.get("private_contact"), ""))
        private_contact_info = st.text_input("Contact Info (email/phone)", value=_opt_label(row.get("private_contact_info"), ""))
        additional_services = st.text_input("Additional Musicians/Services (optional)", value=_opt_label(row.get("additional_services"), ""))
    private_vals = {
        "private_event_type": private_event_type,
        "organizer": organizer,
        "guest_of_honor": guest_of_honor,
        "private_contact": private_contact,
        "private_contact_info": private_contact_info,
        "additional_services": additional_services,
    }

# Deposits (Admin)
dep_rows: List[Dict] = []
if _table_exists("gig_deposits"):
    existing_deps = _select_df("gig_deposits", "*", where_eq={"gig_id": row.get("id")})
    existing_deps = existing_deps.sort_values(by="sequence") if not existing_deps.empty else existing_deps
    st.markdown("---")
    st.subheader("Finance (Deposits)")
    cur_n = len(existing_deps) if not existing_deps.empty else 0
    n = st.number_input("Number of deposits (0–4)", min_value=0, max_value=4, step=1, value=cur_n)
    for i in range(int(n)):
        cdl, cda, cdm = st.columns([1, 1, 1])
        with cdl:
            due_default = (existing_deps.iloc[i]["due_date"] if (not existing_deps.empty and i < len(existing_deps) and pd.notna(existing_deps.iloc[i].get("due_date"))) else date.today())
            due = st.date_input(f"Deposit {i+1} due", value=pd.to_datetime(due_default).date() if pd.notna(due_default) else date.today(), key=f"edit_dep_due_{i}")
        with cda:
            amt_default = float(existing_deps.iloc[i].get("amount", 0.0)) if (not existing_deps.empty and i < len(existing_deps)) else 0.0
            amt = st.number_input(f"Deposit {i+1} amount", min_value=0.0, step=50.0, format="%.2f", value=amt_default, key=f"edit_dep_amt_{i}")
        with cdm:
            pct_default = bool(existing_deps.iloc[i].get("is_percentage", False)) if (not existing_deps.empty and i < len(existing_deps)) else False
            is_pct = st.checkbox(f"Deposit {i+1} is % of fee", value=pct_default, key=f"edit_dep_pct_{i}")
        dep_rows.append({"sequence": i + 1, "due_date": due, "amount": amt, "is_percentage": is_pct})

# -----------------------------
# SAVE CHANGES
# -----------------------------
if st.button("💾 Save Changes", type="primary"):
    def _compose_datetimes(event_dt: date, start_t: time, end_t: time):
        start_dt = datetime.combine(event_dt, start_t)
        end_dt = datetime.combine(event_dt, end_t)
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)
        return start_dt, end_dt

    start_dt, end_dt = _compose_datetimes(event_date, start_time_in, end_time_in)
    if end_dt.date() > start_dt.date():
        st.info(
            f"This gig ends next day ({end_dt.strftime('%Y-%m-%d %I:%M %p')}). We'll keep event_date as the start date."
        )

    payload = {
        "title": (title or None),
        "event_date": event_date.isoformat() if isinstance(event_date, (date, datetime)) else event_date,
        "start_time": start_time_in.strftime("%H:%M:%S"),
        "end_time": end_time_in.strftime("%H:%M:%S"),
        "contract_status": contract_status,
        "fee": float(fee) if fee else None,
        "band_name": (band_name or None),
        "venue_id": None if venue_id_sel == "(none)" else venue_id_sel,
        "sound_tech_id": None if sound_id_sel == "(none)" else sound_id_sel,
        "is_private": bool(is_private),
        "notes": (notes or None),
        "sound_by_venue_name": (sound_by_venue_name or None),
        "sound_by_venue_phone": (sound_by_venue_phone or None),
        "sound_provided": bool(sound_provided),
        "sound_fee": (float(sound_fee) if (sound_fee is not None) else None),
    }
    for k, v in (private_vals or {}).items():
        payload[k] = v or None

    payload = _filter_to_schema("gigs", payload)
    ok = _robust_update("gigs", {"id": row.get("id")}, payload)
    if not ok:
        st.stop()

    # Replace lineup rows (simple + reliable)
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
                # Use bulk insert directly
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
    })
