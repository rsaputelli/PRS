# =============================
# File: pages/05_Edit_Gig.py (PARITY + auth/header order + sound tech attach safety)
# =============================
import os
from datetime import datetime, date, time, timedelta
from typing import Optional, Dict, List, Set
import random
import traceback

import pandas as pd
import streamlit as st
from supabase import create_client, Client
from lib.ui_header import render_header
from lib.ui_format import format_currency
from lib.calendar_utils import upsert_band_calendar_event

# -----------------------------
# Page config
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

# Attach Supabase session for RLS (must happen before any header that could enforce auth)
if st.session_state.get("sb_access_token") and st.session_state.get("sb_refresh_token"):
    try:
        sb.auth.set_session(
            access_token=st.session_state["sb_access_token"],
            refresh_token=st.session_state["sb_refresh_token"],
        )
    except Exception as e:
        st.warning(f"Could not attach Supabase session. ({e})")
        
# ---- Process pending calendar upsert (rerun-proof) ----
try:
    from lib.calendar_utils import upsert_band_calendar_event as _upsert_band_calendar_event_for_queue
    _pending = st.session_state.pop("pending_cal_upsert", None)
    if _pending:
        res = _upsert_band_calendar_event_for_queue(
            _pending["gig_id"],
            sb,
            _pending.get("calendar_name", "Philly Rock and Soul"),
        )
        if isinstance(res, dict) and res.get("error"):
            st.error(f"Calendar upsert (queued) failed: {res.get('error')} (stage: {res.get('stage')})")
        else:
            action = (res or {}).get("action", "updated")
            st.info(f"Queued calendar event {action} for gig {_pending['gig_id']}.")
except Exception as e:
    st.error(f"Calendar upsert processor error: {e}")
        
# -----------------------------
# Auth/admin gate BEFORE header
# -----------------------------
from lib.auth import is_logged_in, current_user, IS_ADMIN

# Require login first
if not is_logged_in():
    st.error("Please sign in from the Login page.")
    st.stop()

USER = current_user()

# Require admin next
if not IS_ADMIN():
    st.error("You do not have permission to edit gigs.")
    st.stop()

# -----------------------------
# Header AFTER gate
# -----------------------------
render_header(title="Edit Gig", emoji="✏️")


# === Email autosend toggles — init keys (shared with Enter Gig) ===
for _k, _default in [
    ("autoc_send_st_on_create", True),
    ("autoc_send_agent_on_create", True),
    ("autoc_send_players_on_create", True),
]:
    if _k not in st.session_state:
        st.session_state[_k] = _default

def _IS_ADMIN() -> bool:
    # Reuse the existing admin flag for this page
    return bool(IS_ADMIN)

# === Email autosend toggles UI (admin-only) ===
if _IS_ADMIN():
    st.markdown("### Auto-send on Save")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.checkbox("Sound tech", key="autoc_send_st_on_create", help="Email the sound tech on save")
    with c2:
        st.checkbox("Agent", key="autoc_send_agent_on_create", help="Email the agent on save")
    with c3:
        st.checkbox("Players", key="autoc_send_players_on_create", help="Email all assigned players on save")
# non-admins just don’t see the toggles; no extra caption needed

# ---- Persisted auto-send log (renders every run; always visible) ----
st.session_state.setdefault("autosend_log", [])      # list[dict]
st.session_state.setdefault("autosend_last", None)   # last entry dict
st.session_state.setdefault("__last_trace", "")      # last raw traceback text
st.session_state.setdefault("autosend_queue", [])    # queue of gig_id strings

def _autosend_log_add(entry: dict):
    try:
        st.session_state["autosend_log"].append(entry)
        if len(st.session_state["autosend_log"]) > 50:
            st.session_state["autosend_log"] = st.session_state["autosend_log"][-50:]
        st.session_state["autosend_last"] = entry
        if entry.get("trace"):
            st.session_state["__last_trace"] = entry["trace"]
    except Exception as _e:
        st.write(f"Autosend logger failed: {_e!s}")

def _log(channel: str, msg: str, trace: str | None = None):
    _autosend_log_add({
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "run_id": f"{random.randint(0, 2**32-1):08x}",
        "channel": channel,
        "msg": msg,
        "trace": trace,
    })

with st.expander("Auto-send log (persists across reruns)", expanded=True):
    log = st.session_state["autosend_log"]
    if not log:
        st.markdown("_No entries yet in this session._")
    else:
        for i, e in enumerate(log, 1):
            ts   = e.get("ts","")
            rid  = e.get("run_id","-")
            chan = e.get("channel","-")
            msg  = e.get("msg","")
            st.markdown(f"**{i}. {ts} [{rid}] {chan}** — {msg}")
            if e.get("trace"):
                st.code(e["trace"])

# ---------- Autosend helpers ----------
def _safe_rerun(why: str = ""):
    """Prefer st.rerun(); never call experimental_rerun."""
    try:
        st.rerun()
    except Exception:
        # If rerun genuinely fails (rare), just continue this run.
        # The queue runner at top will still process on the next user action.
        pass

def _autosend_once(stage: str, gig_id: str) -> bool:
    """True the first time per (stage,gig) for this session; False thereafter."""
    k = f"autosend_once::{stage}::{gig_id}"
    if st.session_state.get(k):
        return False
    st.session_state[k] = True
    return True

# ===== AUTOSEND RUNTIME (queue + resumable per-channel) =====
def _autosend_run_for(gig_id_str: str):
    # Snapshot (for log)
    snap = {
        "is_admin": bool(_IS_ADMIN()),
        "toggles": {
            "agent": bool(st.session_state.get("autoc_send_agent_on_create", False)),
            "soundtech": bool(
                st.session_state.get("autoc_send_st_on_create", False)
                or st.session_state.get(f"edit_autoc_send_now_{gig_id_str}", False)
            ),
            "players": bool(st.session_state.get("autoc_send_players_on_create", False)),
        },
        "gig_id_str": gig_id_str,
    }
    _log("snapshot", f"{snap}")

    # Per-gig progress
    prog_key = f"autosend_progress__{gig_id_str}"
    prog = st.session_state.get(prog_key)
    if not isinstance(prog, dict):
        prog = {"st": False, "agent": False, "players": False}
    st.session_state[prog_key] = prog

    def _need_more() -> bool:
        return not all(prog.values())

    def _mark_done(channel: str):
        prog[channel] = True
        st.session_state[prog_key] = prog

    def _bump_and_rerun(reason: str):
        _log("system", f"Bump+rerun: {reason}")
        _safe_rerun(reason)

    # === 1) Sound tech ===
    _enabled_st = (
        _IS_ADMIN()
        and (st.session_state.get("autoc_send_st_on_create", False)
             or st.session_state.get(f"edit_autoc_send_now_{gig_id_str}", False))
        and not st.session_state.get("sound_by_venue_in", False)
    )
    if not _enabled_st:
        _log(
            "Sound-tech confirmation",
            f"SKIPPED: is_admin={_IS_ADMIN()}, "
            f"toggle={st.session_state.get('autoc_send_st_on_create', False)}, "
            f"edit_flag={st.session_state.get(f'edit_autoc_send_now_{gig_id_str}', False)}, "
            f"sound_by_venue={st.session_state.get('sound_by_venue_in', False)}",
        )
        _mark_done("st")
    elif not prog["st"] and _autosend_once("soundtech", gig_id_str):
        _log("Sound-tech confirmation", "Calling sender...")
        try:
            from tools.send_soundtech_confirm import send_soundtech_confirm
            send_soundtech_confirm(gig_id_str)
            st.toast("📧 Sound-tech emailed.", icon="📧")
            _log("Sound-tech confirmation", "Sent OK.")
        except Exception:
            tr = traceback.format_exc()
            _log("Sound-tech confirmation", "Send failed", tr)
            st.error("Sound-tech autosend failed — see log above.")
            st.code(tr)
        finally:
            _mark_done("st")
            if _need_more():
                _bump_and_rerun("advance to agent")
                return

    # === 2) Agent ===
    _enabled_agent = _IS_ADMIN() and st.session_state.get("autoc_send_agent_on_create", False)
    if not _enabled_agent:
        _log(
            "Agent confirmation",
            f"SKIPPED: is_admin={_IS_ADMIN()}, toggle={st.session_state.get('autoc_send_agent_on_create', False)}",
        )
        _mark_done("agent")
    elif not prog["agent"] and _autosend_once("agent", gig_id_str):
        _log("Agent confirmation", "Calling sender...")
        try:
            from tools.send_agent_confirm import send_agent_confirm
            send_agent_confirm(gig_id_str)
            st.toast("📧 Agent emailed.", icon="📧")
            _log("Agent confirmation", "Sent OK.")
        except Exception:
            tr = traceback.format_exc()
            _log("Agent confirmation", "Send failed", tr)
            st.error("Agent autosend failed — see log above.")
            st.code(tr)
        finally:
            _mark_done("agent")
            if _need_more():
                _bump_and_rerun("advance to players")
                return

    # === 3) Players (PATCHED: only newly added players get emails) ===
    _enabled_players = _IS_ADMIN() and st.session_state.get("autoc_send_players_on_create", False)
    if not _enabled_players:
        _log(
            "Player confirmations",
            f"SKIPPED: is_admin={_IS_ADMIN()}, toggle={st.session_state.get('autoc_send_players_on_create', False)}",
        )
        _mark_done("players")
    elif not prog["players"] and _autosend_once("players", gig_id_str):
        _log("Player confirmations", "Calling sender (patched for added players)...")
        try:
            # Retrieve added players diff from earlier patch
            added_ids = st.session_state.get(f"added_players_{gig_id_str}", set()) or set()

            if added_ids:
                from tools.send_player_confirms import send_player_confirms
                send_player_confirms(gig_id_str, only_players=list(added_ids))
                st.toast(f"📧 Player emails sent to newly added players only.", icon="📧")
                _log("Player confirmations", f"Sent to added players: {added_ids}")
            else:
                _log("Player confirmations", "No newly added players — skipping email send.")
        except Exception:
            tr = traceback.format_exc()
            _log("Player confirmations", "Send failed", tr)
            st.error("Player autosend failed — see log above.")
            st.code(tr)
        finally:
            _mark_done("players")
            if _need_more():
                _bump_and_rerun("advance to done")
                return

    # Done: pop from queue and set a guard for this gig (prevents dupes)
    if not _need_more():
        _log("system", "Autosend complete (all channels).")
        st.session_state[f"autosend_guard__{gig_id_str}"] = True
        if (
            st.session_state["autosend_queue"]
            and st.session_state["autosend_queue"][0] == gig_id_str
        ):
            st.session_state["autosend_queue"] = st.session_state["autosend_queue"][1:]

# Run the queue head (if any) every run (independent of form state)
if st.session_state["autosend_queue"]:
    _autosend_run_for(st.session_state["autosend_queue"][0])

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

# ==============================
# Closeout Status Filter (NEW)
# ==============================
st.markdown("### Closeout Filter")

closeout_filter = st.radio(
    "Show gigs:",
    ["Open only", "Closed only", "Open + Closed"],
    horizontal=True,
    index=0,
)

# PATCH: Contract Status Filter
st.markdown("### Filter Gigs")
status_filter = st.multiselect(
    "Show gigs with status:",
    ["Pending", "Hold", "Confirmed"],
    default=["Pending", "Hold", "Confirmed"],
)

gigs = _load_gigs()
if gigs.empty:
    st.info("No gigs found.")
    st.stop()

# Apply closeout filter
if "closeout_status" in gigs.columns:
    if closeout_filter == "Open only":
        gigs = gigs[gigs["closeout_status"].fillna("open") == "open"]
    elif closeout_filter == "Closed only":
        gigs = gigs[gigs["closeout_status"] == "closed"]

# PATCH: Apply status filter
if "contract_status" in gigs.columns:
    gigs = gigs[gigs["contract_status"].isin(status_filter)]

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

# ======================================================
# Safe span expansion (fixes KeyError on empty DataFrame)
# ======================================================
if gigs.empty:
    gigs["_start_dt"] = pd.NaT
    gigs["_end_dt"]   = pd.NaT
else:
    spans = gigs.apply(
        lambda r: pd.Series(_compose_span(r), index=["_start_dt_raw", "_end_dt_raw"]),
        axis=1
    )

    gigs["_start_dt"] = pd.to_datetime(spans["_start_dt_raw"], errors="coerce")
    gigs["_end_dt"]   = pd.to_datetime(spans["_end_dt_raw"], errors="coerce")


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

# Sort for display, but use gig ID (stable) as the selectbox value
gigs = gigs.sort_values(by=["_start_dt"], ascending=[True])

# Ensure ID is string so it's stable across reloads
if "id" not in gigs.columns:
    st.error("Gig table missing 'id' column; cannot continue.")
    st.stop()
gigs["id"] = gigs["id"].astype(str)

# Build labels keyed by gig_id
labels = {}
for _, r in gigs.iterrows():
    gid_val = r["id"]
    labels[gid_val] = _label_row(r)

opts = list(labels.keys())

sel_gid = st.selectbox(
    "Select a gig to edit",
    options=opts,
    format_func=lambda g: labels.get(g, str(g)),
)

# Row for the selected gig (by ID, not by position)
row = gigs[gigs["id"] == sel_gid].iloc[0]
gid = str(sel_gid)
gid_str = str(sel_gid)

# If we just saved this gig on the previous run, drop any per-gig widget state
# so all widgets rehydrate from the current DB row instead of stale values.
just_saved_gid = st.session_state.pop("_edit_just_saved_gid", None)
if just_saved_gid == gid:
    for key in list(st.session_state.keys()):
        if key.endswith(f"_{gid}"):
            del st.session_state[key]

# ------------------------------------------------------------------
# Per-gig widget keys + gig-switch cleanup (place this right here)
# ------------------------------------------------------------------

# Key helper: namespace all widget keys to this gig
def k(name: str) -> str:
    return f"{name}_{gid}"

prev_gid = st.session_state.get("_edit_prev_gid")
just_saved_gid = st.session_state.pop("_edit_just_saved_gid", None)

def _clear_per_gig_state(target_gid: str):
    """Remove all widget keys for a specific gig so widgets rehydrate from DB."""
    if not target_gid:
        return
    for key in list(st.session_state.keys()):
        # All per-gig widgets use the pattern f"{name}_{gid}"
        if key.endswith(f"_{target_gid}"):
            st.session_state.pop(key, None)

# If we switched gigs, clear state for the previous gig
if prev_gid is not None and prev_gid != gid:
    _clear_per_gig_state(prev_gid)

# If we just saved this gig, clear its widget state so it rehydrates from the DB row
if just_saved_gid == gid:
    _clear_per_gig_state(gid)

st.session_state["_edit_prev_gid"] = gid


# -----------------------------
# Edit form
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

# -----------------------------
# Contacts & Venue / Sound
# -----------------------------
st.markdown("---")
st.subheader("Contacts & Venue / Sound")

# ----- Agent (with Add New) + session_state override
AGENT_ADD = "__ADD_AGENT__"
agent_options = [""] + list(agent_labels.keys()) + [AGENT_ADD]
def _fmt_agent(x: str) -> str:
    if x == "": return "(none)"
    if x == AGENT_ADD: return "(+ Add New Agent)"
    return agent_labels.get(x, x)
cur_agent = str(row.get("agent_id")) if pd.notna(row.get("agent_id")) else ""
cur_agent = st.session_state.get(f"agent_sel_{gid}", cur_agent)
agent_id_sel = st.selectbox("Agent", options=agent_options,
                             index=(agent_options.index(cur_agent) if cur_agent in agent_options else 0),
                             format_func=_fmt_agent, key=f"agent_sel_{gid}")
agent_add_box = st.empty()

# ----- Venue (with Add New) + session_state override
VENUE_ADD = "__ADD_VENUE__"
venue_options_ids = [""] + list(venue_labels.keys()) + [VENUE_ADD]
def _fmt_venue(x: str) -> str:
    if x == "": return "(select venue)"
    if x == VENUE_ADD: return "(+ Add New Venue)"
    return venue_labels.get(x, x)
cur_vid = str(row.get("venue_id")) if pd.notna(row.get("venue_id")) else ""
cur_vid = st.session_state.get(f"venue_sel_{gid}", cur_vid)
venue_id_sel = st.selectbox("Venue", options=venue_options_ids,
                            index=(venue_options_ids.index(cur_vid) if cur_vid in venue_options_ids else 0),
                            format_func=_fmt_venue, key=f"venue_sel_{gid}")
venue_add_box = st.empty()

# Private flag, eligibility
c1, c2 = st.columns([1, 1])
with c1:
    # Use private_flag as the canonical DB column; fall back to any legacy is_private value if present
    initial_private = bool(row.get("private_flag") or row.get("is_private"))
    is_private = st.checkbox("Private Event?", value=initial_private, key=f"priv_{gid}")
with c2:
    eligible_1099 = st.checkbox(
        "1099 Eligible",
        value=bool(row.get("eligible_1099", False)),
        key=f"elig1099_{gid}",
    )


# Preselect newly created tech (set in previous run)
_pre_key = f"preselect_sound_{gid}"
_pre_id = st.session_state.pop(_pre_key, None)
if _pre_id:
    st.session_state[f"sound_sel_{gid}"] = _pre_id

# ----- Sound tech (with Add New) + session_state override
SOUND_ADD = "__ADD_SOUND__"
sound_options_ids = [""] + list(sound_labels.keys()) + [SOUND_ADD]
def _fmt_sound(x: str) -> str:
    if x == "": return "(none)"
    if x == SOUND_ADD: return "(+ Add New Sound Tech)"
    return sound_labels.get(x, x)
sound_provided = st.checkbox("Venue provides sound?", value=bool(row.get("sound_provided")), key=f"edit_sound_provided_{gid}")
# Keep this in sync with Enter Gig's `sound_by_venue_in` flag
st.session_state["sound_by_venue_in"] = bool(sound_provided)

cur_sid = str(row.get("sound_tech_id")) if pd.notna(row.get("sound_tech_id")) else ""
cur_sid = st.session_state.get(f"sound_sel_{gid}", cur_sid)

# Remember the original sound tech selection for change detection
orig_sound_tech_id = str(row.get("sound_tech_id") or "")


if not sound_provided:
    sound_id_sel = st.selectbox("Confirmed Sound Tech",
                                options=sound_options_ids,
                                index=(sound_options_ids.index(cur_sid) if cur_sid in sound_options_ids else 0),
                                format_func=_fmt_sound, key=f"sound_sel_{gid}")
else:
    sound_id_sel = ""
sound_add_box = st.empty()

# Conditional Sound Fee when PRS provides sound
# Safe default: coerce NaN/None to 0.0 for the UI

cur_sound_fee_raw = row.get("sound_fee")
cur_sound_fee = (
    float(cur_sound_fee_raw)
    if (cur_sound_fee_raw is not None and not pd.isna(cur_sound_fee_raw))
    else 0.0
)
sound_fee = None
if not sound_provided:
    sound_fee = st.number_input("Sound Fee ($)", min_value=0.0, step=25.0, format="%.2f",
                                value=cur_sound_fee, key=f"edit_sound_fee_{gid}")

# Venue-provided (free text) — always visible
sv1, sv2 = st.columns([1, 1])
with sv1:
    sound_by_venue_name = st.text_input("Venue Sound Company/Contact (optional)",
                                        value=_opt_label(row.get("sound_by_venue_name"), ""),
                                        key=f"edit_sound_vendor_name_{gid}")
with sv2:
    sound_by_venue_phone = st.text_input("Venue Sound Phone/Email (optional)",
                                         value=_opt_label(row.get("sound_by_venue_phone"), ""),
                                         key=f"edit_sound_vendor_phone_{gid}")

# -----------------------------
# Lineup (Role Assignments)
# -----------------------------
st.markdown("---")
st.subheader("Lineup (Role Assignments)")

assigned_df = _table_exists("gig_musicians") and _select_df(
    "gig_musicians", "*", where_eq={"gig_id": gid_str}
)
assigned_df = assigned_df if isinstance(assigned_df, pd.DataFrame) else pd.DataFrame()

# PATCH: Capture old players before editing
old_player_ids = set()
if isinstance(assigned_df, pd.DataFrame) and not assigned_df.empty:
    try:
        old_player_ids = set(
            assigned_df["musician_id"].dropna().astype(str).tolist()
        )
    except Exception:
        old_player_ids = set()

# --- DIAGNOSTIC: verify gig_id + rowcount + RLS errors on cold start --- Keep for future troubleshooting if needed
# st.caption(f"DBG gid_str={gid_str}")
# st.caption(f"DBG assigned_df_rows={0 if assigned_df is None else len(assigned_df)}")

# Ensure SB client is available in this scope
# sb = _sb()

# Also hit Supabase directly and show any error (RLS/permission) on cold start
# try:
    # dbg = sb.table("gig_musicians").select("gig_id,role,musician_id").eq("gig_id", gid_str).limit(3).execute()
    # st.caption(f"DBG supabase_resp_count={len(dbg.data) if getattr(dbg, 'data', None) else 0}")
    # if getattr(dbg, "error", None):
        # st.error(f"DBG supabase_error: {dbg.error}")
# except Exception as e:
    # st.error(f"DBG exception on select: {e}")

# ----- One-time lineup buffer per gig -----
buf_key = k("lineup_buf")
buf_gid_key = k("lineup_buf_gid")

if buf_key not in st.session_state or st.session_state.get(buf_gid_key) != gid_str:
    cur_map: Dict[str, Optional[str]] = {}
    if not assigned_df.empty:
        for _, r in assigned_df.iterrows():
            cur_map[str(r.get("role"))] = str(r.get("musician_id")) if pd.notna(r.get("musician_id")) else ""
    st.session_state[buf_key] = cur_map
    st.session_state[buf_gid_key] = gid_str
else:
    cur_map = st.session_state[buf_key]
    
#DBG - Keep for future troubleshooting if needed
# st.caption(f"DBG buf_gid_key={st.session_state.get(buf_gid_key)}")
# st.caption(f"DBG buf_has_roles={(st.session_state.get(buf_key) or {})}")
    

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

# === Buffered lineup editor (no form; widgets update live) ===
# Use the per-gig buffer seeded earlier
lineup_buf = st.session_state[buf_key]

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

        default_val = lineup_buf.get(role, "")

        if default_val and default_val not in role_labels:
            fallback = mus_labels.get(default_val)
            if fallback:
                role_labels[default_val] = fallback

        mus_options_ids = [""] + list(role_labels.keys()) + [sentinel]

        def mus_fmt(x: str, _role=role):
            if x == "":
                return "(unassigned)"
            if x.startswith("__ADD_MUS__"):
                return "(+ Add New Musician)"
            return role_labels.get(x, mus_labels.get(x, x))

        sel = st.selectbox(
            role,
            options=mus_options_ids,
            index=(mus_options_ids.index(default_val) if default_val in mus_options_ids else 0),
            format_func=mus_fmt,
            key=k(f"edit_role_{role}"),
        )

        # immediately reflect current selection in the buffer
        lineup_buf[role] = sel

        if sel and not sel.startswith("__ADD_MUS__"):
            lineup.append({"role": role, "musician_id": sel})

        role_add_boxes[role] = st.empty()

# === end of role-assignment loop ===

# PATCH: Determine newly added players
new_player_ids = {p["musician_id"] for p in lineup if p["musician_id"]}
added_player_ids = new_player_ids - old_player_ids
st.session_state[f"added_players_{gid_str}"] = added_player_ids

# -----------------------------
# Inline “Add New …” sub-forms
# -----------------------------

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
            new_st_name  = st.text_input("Display Name", key=f"new_st_name_{gid}")
            new_st_phone = st.text_input("Phone (optional)", key=f"new_st_phone_{gid}")
        with s2:
            new_st_company = st.text_input("Company (optional)", key=f"new_st_company_{gid}")
            new_st_email   = st.text_input("Email (optional)", key=f"new_st_email_{gid}")

        if st.button("Create Sound Tech", key=f"create_sound_btn_{gid}"):
            # Require a display name (avoid blank records)
            name = (new_st_name or "").strip()
            if not name:
                st.error("Please enter a Display Name before creating the sound tech.")
                st.stop()

            # Optional fields (normalize empty -> None)
            company = (new_st_company or "").strip()
            phone   = (new_st_phone or "").strip() or None
            email   = (new_st_email or "").strip() or None

            new_id = _create_soundtech(
                display_name=name,
                company=company,
                phone=phone,
                email=email,
            )

            if new_id:
                # Don’t set the selectbox state this run — stash and rerun
                st.cache_data.clear()
                st.session_state[f"preselect_sound_{gid}"] = new_id
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
notes = st.text_area(
    "Notes / Special Instructions (optional)",
    max_chars=1000,
    value=_opt_label(row.get("notes"), ""),
    key=f"notes_{gid}",
)

# Ensure overtime_rate is always defined (public + private)
overtime_rate = row.get("overtime_rate")

# Load any existing private details for this gig (from gigs_private)
gp_row: Dict[str, object] = {}
if bool(is_private) and _table_exists("gigs_private"):
    gp_df = _select_df("gigs_private", "*", where_eq={"gig_id": gid_str}, limit=1)
    if isinstance(gp_df, pd.DataFrame) and not gp_df.empty:
        gp_row = gp_df.iloc[0].to_dict()

if is_private:
    st.markdown("#### Private Event Details")
    p1, p2 = st.columns([1, 1])

    # -------------------------------
    # Column 1
    # -------------------------------
    with p1:
        private_event_type = st.text_input(
            "Type of Event",
            value=_opt_label(
                (gp_row.get("event_type") if gp_row and gp_row.get("event_type") else row.get("private_event_type")),
                "",
            ),
            key=f"pet_{gid}",
        )

        organizer = st.text_input(
            "Organizer / Company",
            value=_opt_label(
                (gp_row.get("organizer") if gp_row and gp_row.get("organizer") else row.get("organizer")),
                "",
            ),
            key=f"org_{gid}",
        )

        guest_of_honor = st.text_input(
            "Guest(s) of Honor / Bride/Groom",
            value=_opt_label(
                (gp_row.get("honoree") if gp_row and gp_row.get("honoree") else row.get("guest_of_honor")),
                "",
            ),
            key=f"goh_{gid}",
        )

    # -------------------------------
    # Column 2
    # -------------------------------
    with p2:
        private_contact = st.text_input(
            "Primary Contact (name)",
            value=_opt_label(
                (gp_row.get("client_name") if gp_row and gp_row.get("client_name") else row.get("private_contact")),
                "",
            ),
            key=f"pc_{gid}",
        )

        private_client_email = st.text_input(
            "Client Email",
            value=_opt_label(
                (gp_row.get("client_email") if gp_row and gp_row.get("client_email") else row.get("client_email")),
                "",
            ),
            key=f"client_email_{gid}",
        )

        private_client_phone = st.text_input(
            "Client Phone",
            value=_opt_label(
                (gp_row.get("client_phone") if gp_row and gp_row.get("client_phone") else row.get("client_phone")),
                "",
            ),
            key=f"client_phone_{gid}",
        )

        additional_services = st.text_input(
            "Additional Musicians/Services (optional)",
            value=_opt_label(row.get("additional_services"), ""),
            key=f"adds_{gid}",
        )

    # -------------------------------
    # Overtime Rate
    # -------------------------------
    overtime_rate = st.text_input(
        "Overtime Rate (e.g., $300/hr)",
        value=_opt_label(
            (gp_row.get("overtime_rate") if gp_row and gp_row.get("overtime_rate") else row.get("overtime_rate")),
            "",
        ),
        key=f"otrate_{gid}",
    )

    # ----------------------------------------
    # Organizer Address (FULL WIDTH)
    # ----------------------------------------
    st.markdown("##### Organizer Address")
    a1, a2 = st.columns([2, 1])

    with a1:
        st.text_input(
            "Street Address",
            key=f"priv_addr_street_{gid}",
            value=_opt_label(
                (gp_row.get("organizer_street") if gp_row and gp_row.get("organizer_street") else row.get("organizer_street")),
                "",
            ),
        )

        st.text_input(
            "City",
            key=f"priv_addr_city_{gid}",
            value=_opt_label(
                (gp_row.get("organizer_city") if gp_row and gp_row.get("organizer_city") else row.get("organizer_city")),
                "",
            ),
        )

    with a2:
        st.text_input(
            "State",
            key=f"priv_addr_state_{gid}",
            value=_opt_label(
                (gp_row.get("organizer_state") if gp_row and gp_row.get("organizer_state") else row.get("organizer_state")),
                "",
            ),
        )

        st.text_input(
            "Zip Code",
            key=f"priv_addr_zip_{gid}",
            value=_opt_label(
                (gp_row.get("organizer_zip") if gp_row and gp_row.get("organizer_zip") else row.get("organizer_zip")),
                "",
            ),
        )

# ----------------------------------------
# Contract-Specific Details (FULL WIDTH)
# ----------------------------------------
st.markdown("### Contract-Specific Details")

special_instructions = st.text_area(
    "Special Instructions (Contract Only)",
    value=_opt_label(gp_row.get("special_instructions") if gp_row else None, ""),
    height=120,
    key=f"special_instr_{gid}",
)

cocktail_coverage = st.text_input(
    "Cocktail Coverage (optional)",
    value=_opt_label(gp_row.get("cocktail_coverage") if gp_row else None, ""),
    key=f"cocktail_cov_{gid}",
)


# -----------------------------
# Deposits (Admin)
# -----------------------------
# Always render the UI; fall back to empty if table doesn't exist
table_exists = _table_exists("gig_deposits")
existing_deps = pd.DataFrame()
if table_exists:
    existing_deps = _select_df("gig_deposits", "*", where_eq={"gig_id": gid_str})
    existing_deps = (
        existing_deps.sort_values(by="seq")
        if isinstance(existing_deps, pd.DataFrame) and not existing_deps.empty
        else pd.DataFrame()
    )

st.markdown("---")
st.subheader("Finance (Deposits)")
if not table_exists:
    st.info("Deposits table not found; you can configure rows here, but they won’t persist until the table exists.")

cur_n = len(existing_deps) if not existing_deps.empty else 0
n = st.number_input("Number of deposits (0–4)", min_value=0, max_value=4, step=1, value=cur_n, key=f"deps_n_{gid}")

# ---- Buffer-based editor (kept) ----
dep_buf_key = k("deposit_buf")
if dep_buf_key not in st.session_state:
    if not existing_deps.empty:
        seed = (
            existing_deps.sort_values("seq")
            .assign(
                sequence=lambda d: d["seq"].fillna(0).astype(int),
                amount=lambda d: d["amount"].fillna(0.0).astype(float),
                is_percentage=lambda d: d["is_percentage"].fillna(False).astype(bool),
                due_date=lambda d: d["due_date"].fillna(""),
            )[["sequence", "due_date", "amount", "is_percentage"]]
            .to_dict("records")
        )
    else:
        seed = []
    st.session_state[dep_buf_key] = seed

dep_rows = st.session_state[dep_buf_key]

# Sync buffer length to n
cur_len = len(dep_rows)
if n > cur_len:
    for i in range(cur_len, n):
        dep_rows.append({"sequence": i + 1, "due_date": "", "amount": 0.0, "is_percentage": False})
elif n < cur_len:
    del dep_rows[n:]

st.markdown("#### Deposit Schedule")
for i, d in enumerate(dep_rows):
    c = st.columns([1, 3, 2, 2, 1])
    with c[0]:
        d["sequence"] = int(st.number_input("Seq", min_value=1, max_value=10, step=1,
                                            value=int(d.get("sequence", i + 1)), key=k(f"dep_seq_{i}")))
    with c[1]:
        d["due_date"] = st.text_input("Due (YYYY-MM-DD)", value=str(d.get("due_date") or ""), key=k(f"dep_due_{i}"))
    with c[2]:
        d["amount"] = st.number_input("Amount", min_value=0.0, step=50.0,
                                      value=float(d.get("amount") or 0.0), key=k(f"dep_amt_{i}"))
    with c[3]:
        d["is_percentage"] = st.checkbox("% of Fee", value=bool(d.get("is_percentage")), key=k(f"dep_pct_{i}"))
    with c[4]:
        if st.button("🗑", key=k(f"dep_del_{i}")):
            dep_rows.pop(i)
            st.rerun()     

# -----------------------------
# SAVE CHANGES
# -----------------------------

# Offer auto-send only when the sound tech assignment changed AND PRS provides sound
autoc_send_now = False
if not sound_provided:
    changed_assignment = (str(cur_sid or "") != str(orig_sound_tech_id or ""))
    if changed_assignment:
        autoc_send_now = st.checkbox(
            "Send confirmation to sound tech on save",
            value=True,
            key=f"send_on_save_{gid}"
        )

if st.button("💾 Save Changes", type="primary", key=f"save_{gid}"):
    def _compose_datetimes(event_dt: date, start_t: time, end_t: time):
        start_dt = datetime.combine(event_dt, start_t)
        end_dt = datetime.combine(event_dt, end_t)
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)
        return start_dt, end_dt

    start_dt, end_dt = _compose_datetimes(event_date, start_time_in, end_time_in)
    if end_dt.date() > start_dt.date():
        st.info(
            f"This gig ends next day ({end_dt.strftime('%Y-%m-%d %I:%M %p')}). "
            "We'll keep event_date as the start date."
        )

    # Determine IDs (respect session_state selection)
    agent_id_val = agent_id_sel if agent_id_sel not in ("", AGENT_ADD) else None
    venue_id_val = venue_id_sel if venue_id_sel not in ("", VENUE_ADD) else None

    # Prevent saving while (+ Add New Sound Tech) is selected
    if (not sound_provided) and (sound_id_sel == SOUND_ADD):
        st.error("Finish creating the new sound tech (click “Create Sound Tech”) or choose an existing one before saving.")
        st.stop()

    # --- Sound tech logic (selection-only) ---
    if sound_provided:
        # Venue provides sound -> clear app-provided tech and ignore fee
        sound_tech_id_val = None
        sound_fee_val = None
    else:
        # PRS provides sound -> use selected tech if any; do NOT create from text fields
        sel = sound_id_sel if sound_id_sel not in ("", SOUND_ADD) else None
        sound_tech_id_val = sel
        sound_fee_val = None if (sound_fee is None or pd.isna(sound_fee)) else float(sound_fee)

    # Build payload
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
        "sound_tech_id": sound_tech_id_val,  # selection-only
        "private_flag": bool(is_private),
        # keep legacy is_private for now in case the column still exists; schema filter will drop it if not
        "is_private": bool(is_private),
        "notes": (notes or None),
        "organizer_street": st.session_state.get(f"priv_addr_street_{gid}") or None,
        "organizer_city": st.session_state.get(f"priv_addr_city_{gid}") or None,
        "organizer_state": st.session_state.get(f"priv_addr_state_{gid}") or None,
        "organizer_zip": st.session_state.get(f"priv_addr_zip_{gid}") or None,
        "sound_by_venue_name": (sound_by_venue_name or None),   # pure text
        "sound_by_venue_phone": (sound_by_venue_phone or None), # pure text (may contain email or phone)
        "sound_provided": bool(sound_provided),
        "sound_fee": sound_fee_val,
        "eligible_1099": bool(eligible_1099) if "eligible_1099" in _table_columns("gigs") else None,
        "overtime_rate": overtime_rate or None,
    }

    payload = _filter_to_schema("gigs", payload)

    # Update gig
    ok = _robust_update("gigs", {"id": row.get("id")}, payload)
    if not ok:
        st.stop()
        
    # If this is a private event, upsert private details into gigs_private
    if bool(is_private) and _table_exists("gigs_private"):
        try:
            gp_payload = {
                "gig_id": gid_str,
                "organizer": organizer or None,
                "event_type": private_event_type or None,
                "honoree": guest_of_honor or None,
                "special_instructions": special_instructions or None,
                "cocktail_coverage": cocktail_coverage or None,
                "client_name": private_contact or None,
                "client_email": private_client_email or None,
                "client_phone": private_client_phone or None,
            }

            # Initialize contract_total_amount from the gig fee if present
            if fee:
                try:
                    gp_payload["contract_total_amount"] = float(fee)
                except Exception:
                    pass

            gp_payload = _filter_to_schema("gigs_private", gp_payload)
            if gp_payload:
                sb.table("gigs_private").upsert(gp_payload, on_conflict="gig_id").execute()
        except Exception as e:
            st.error(f"Could not save private event details: {e}")

    # --- Persist lineup (no table-exists gate) ---
    # Guard: avoid accidental full wipe unless explicitly confirmed
    if not lineup:
        st.warning("No roles are assigned. To clear the entire lineup, check the box below and save again.")
        if not st.checkbox("Yes, clear all lineup for this gig", key=k("confirm_clear_lineup")):
            st.stop()

    try:
        sb.table("gig_musicians").delete().eq("gig_id", gid_str).execute()
    except Exception as e:
        st.error(f"Could not clear existing lineup: {e}")
    else:
        if lineup:
            try:
                rows = []
                for r in lineup:
                    rows.append(_filter_to_schema("gig_musicians", {
                        "gig_id": gid_str,
                        "role": r["role"],
                        "musician_id": r["musician_id"],
                    }))
                if rows:
                    sb.table("gig_musicians").insert(rows).execute()
            except Exception as e:
                st.error(f"Could not insert lineup: {e}")
    # Post-save sanity check
    try:
        post = _select_df("gig_musicians", "count(*) as c", where_eq={"gig_id": gid_str})
        n_roles = int(post.iloc[0]["c"]) if (isinstance(post, pd.DataFrame) and not post.empty) else 0
        st.toast(f"Saved lineup: {n_roles} role(s).", icon="✅")
    except Exception:
        pass
    # --- Persist deposits (delete→insert, only if table exists) ---
    if dep_rows:
        if table_exists:
            try:
                sb.table("gig_deposits").delete().eq("gig_id", gid_str).execute()
            except Exception as e:
                st.error(f"Could not clear existing deposits: {e}")
            else:
                try:
                    rows = []
                    for d in dep_rows:
                        rows.append(_filter_to_schema("gig_deposits", {
                            "gig_id": gid_str,
                            "seq": d["sequence"],
                            "due_date": d["due_date"].isoformat() if isinstance(d["due_date"], (date, datetime)) else d["due_date"],
                            "amount": float(d["amount"] or 0.0),
                            "is_percentage": bool(d["is_percentage"]),
                        }))
                    if rows:
                        sb.table("gig_deposits").insert(rows).execute()
                except Exception as e:
                    st.error(f"Could not insert deposits: {e}")

            # Tiny toast confirming how many deposits were saved
            try:
                post_deps = _select_df("gig_deposits", "count(*) as c", where_eq={"gig_id": gid_str})
                n_deps = int(post_deps.iloc[0]["c"]) if (isinstance(post_deps, pd.DataFrame) and not post_deps.empty) else 0
                st.toast(f"Saved deposits: {n_deps} row(s).", icon="💵")
            except Exception:
                pass
        else:
            st.info("Deposits were not persisted because the 'gig_deposits' table is missing.")

    # ---- Queue calendar upsert for this gig and also run immediately ----
    try:
        gid = gid_str.strip()
        if not gid:
            raise ValueError("Missing gig_id for calendar upsert")
        st.session_state["pending_cal_upsert"] = {
            "gig_id": gid,
            "calendar_name": "Philly Rock and Soul",  # must match calendar_utils
        }
    except Exception as e:
        st.warning(f"Could not queue calendar upsert: {e}")

    try:
        res = upsert_band_calendar_event(
            gig_id=gid_str,
            sb=sb,
            calendar_name="Philly Rock and Soul",  # keep this exact name
        )
        if isinstance(res, dict) and res.get("error"):
            st.error(f"Calendar upsert failed: {res.get('error')} (stage: {res.get('stage')})")
        else:
            action = (res or {}).get("action", "updated")
            ev_id  = (res or {}).get("eventId")
            st.success(f"PRS Calendar {action}.")
            if ev_id:
                st.caption(f"Event ID: {ev_id}")
    except Exception as e:
        st.error(f"Calendar upsert exception: {e}")

    # ---- Queue autosend (sound-tech / agent / players) if toggled/selected ----
    # Persist the per-gig sound-tech-on-save flag so the autosend runner can see it
    st.session_state[f"edit_autoc_send_now_{gid_str}"] = bool(autoc_send_now)

    if any([
        st.session_state.get("autoc_send_st_on_create", False),
        st.session_state.get("autoc_send_agent_on_create", False),
        st.session_state.get("autoc_send_players_on_create", False),
        autoc_send_now,  # respect the per-gig checkbox as well
    ]):
        guard_key = f"autosend_guard__{gid_str}"
        if not st.session_state.get(guard_key, False):
            if gid_str not in st.session_state["autosend_queue"]:
                st.session_state["autosend_queue"].append(gid_str)
            try:
                st.rerun()
            except Exception:
                _safe_rerun("autosend after edit save")

        ...
    # any lineup / deposit post-save checks

    # Remember which gig was just saved so we can refresh its widget state on the next run
    st.session_state["_edit_just_saved_gid"] = gid_str

    # Bust caches so the next render reflects changes immediately
    st.cache_data.clear()
    st.success("Gig updated successfully ✅")
    
# -----------------------------
# Auto-send Sound Tech confirmation if assignment changed
# -----------------------------
# try:
    # if (
        # not sound_provided
        # and autoc_send_now
        # and (sound_tech_id_val is not None)
        # and (str(sound_tech_id_val) != str(orig_sound_tech_id or ""))
    # ):
        # import time
        # t0 = time.perf_counter()
        # with st.status("Sending sound-tech confirmation…", state="running") as s:
            # from tools.send_soundtech_confirm import send_soundtech_confirm
            # send_soundtech_confirm(gid)
            # s.update(label="Confirmation sent", state="complete")
        # dt = time.perf_counter() - t0
        # st.toast(f"📧 Sound-tech emailed (took {dt:.1f}s).", icon="📧")
# except Exception as e:
    # st.warning(f"Auto-send failed: {e}")
   
    
# DEBUG OUTPUT (commented) — keep for future troubleshooting if needed
    # st.write({
        # "id": row.get("id"),
        # "event_date": str(event_date),
        # "time": f"{start_time_in.strftime('%I:%M %p').lstrip('0')} – {end_time_in.strftime('%I:%M %p').lstrip('0')}",
        # "status": contract_status,
        # "fee": format_currency(fee),
        # "sound_provided": bool(sound_provided),
        # "sound_tech_id": sound_tech_id_val or "(none)",
        # "sound_fee": (None if sound_fee_val is None else format_currency(sound_fee_val)),
    # })

with st.expander("🔎 Gmail Credentials / Diagnostics", expanded=False):
    gmail = st.secrets.get("gmail", {})
    st.write("**Loaded Gmail config:**")
    st.json({
        "has_client_id": bool(gmail.get("client_id")),
        "has_client_secret": bool(gmail.get("client_secret")),
        "has_refresh_token": bool(gmail.get("refresh_token")),
        "scopes": gmail.get("scopes"),
    })

    # Show environment vars too (in case Streamlit injected them)
    st.write("**Environment overrides:**")
    st.json({
        "GMAIL_CLIENT_ID": bool(os.environ.get("GMAIL_CLIENT_ID")),
        "GMAIL_CLIENT_SECRET": bool(os.environ.get("GMAIL_CLIENT_SECRET")),
        "GMAIL_REFRESH_TOKEN": bool(os.environ.get("GMAIL_REFRESH_TOKEN")),
    })
with st.expander("🔎 Root-level Gmail Key Check", expanded=True):
    st.json({
        "ROOT_GMAIL_CLIENT_ID": bool(st.secrets.get("GAIL_CLIENT_ID")),
        "ROOT_GMAIL_CLIENT_SECRET": bool(st.secrets.get("GMAIL_CLIENT_SECRET")),
        "ROOT_GMAIL_REFRESH_TOKEN": bool(st.secrets.get("GMAIL_REFRESH_TOKEN")),
    })
st.write("TEST_KEY present:", "TEST_KEY" in st.secrets)

# -----------------------------
# Manual: Resend Player Confirmations
# -----------------------------
with st.expander("📧 Manual: Resend Player Confirmations", expanded=False):

    # --- Use the global Supabase client already defined above ---
    global sb

    # --- Fetch current lineup from gig_musicians ---
    current_rows = (
        sb.table("gig_musicians")
          .select("musician_id")
          .eq("gig_id", gig_id_str)
          .execute()
          .data
    ) or []

    current_player_ids = {
        str(r.get("musician_id"))
        for r in current_rows
        if r.get("musician_id")
    }

    # Players from autosend snapshot (baseline)
    prior_player_ids = {
        str(pid)
        for pid in (st.session_state.get("autosend__prior_players") or [])
    }

    newly_added_ids = current_player_ids - prior_player_ids
    unchanged_ids   = current_player_ids & prior_player_ids


    st.write("### Lineup snapshot")
    st.json({
        "current": sorted(list(current_player_ids)),
        "prior": sorted(list(prior_player_ids)),
        "newly_added": sorted(list(newly_added_ids)),
        "unchanged": sorted(list(unchanged_ids)),
    })

    mode = st.radio(
        "Which players should receive confirmations?",
        ["Only newly-added players", "All current players"],
        index=0,
    )

    do_dry_run = st.checkbox("Dry run (no email sent)", value=True)

    if st.button("Send Player Confirmations Now"):
        from tools.send_player_confirms import send_player_confirms

        target_ids = (
            newly_added_ids
            if mode == "Only newly-added players"
            else current_player_ids
        )

        if not target_ids:
            st.warning("No players match the selected criteria.")
        else:
            st.success(f"Sending to {len(target_ids)} player(s)…")

            send_player_confirms(
                gig_id_str,
                override_player_ids=list(target_ids),
                dry_run=do_dry_run,
            )

# -----------------------------
# MANUAL: Send Sound Tech Confirm (admin-only)
# -----------------------------
if IS_ADMIN:
    st.markdown("---")
    st.subheader("Email — Sound Tech")

    # Diagnostic toggle (does not email; writes 'dry-run' to email_audit)
    diag_dry_run = st.checkbox("Diagnostic mode (no email, write 'dry-run' to audit)", value=False, key=f"dryrun_send_sound_{gid}")

    # Derive current selection for sending (independent of Save block)
    if sound_provided:
        selected_soundtech_id_for_send = None
    else:
        sel = st.session_state.get(f"sound_sel_{gid}", "")
        if not sel:
            sel = sound_id_sel if (sound_id_sel not in ("", SOUND_ADD)) else ""
        selected_soundtech_id_for_send = sel if sel and sel != SOUND_ADD else None

    can_send = bool(selected_soundtech_id_for_send)

    if not can_send:
        st.caption("Assign a sound tech and uncheck “Venue provides sound?” to enable the send button.")
    else:
        tech_label = sound_labels.get(selected_soundtech_id_for_send, "(selected tech)")
        st.caption(f"Will email: {tech_label} (includes .ics attachment).")

    if st.button("📧 Send Sound Tech Confirm", key=f"send_soundtech_{gid}", disabled=not can_send):
        try:
            # temporarily set DRY-RUN via secrets shim (env is fine too)
            if diag_dry_run:
                os.environ["SOUNDT_EMAIL_DRY_RUN"] = "1"
            else:
                os.environ.pop("SOUNDT_EMAIL_DRY_RUN", None)

            from tools.send_soundtech_confirm import send_soundtech_confirm
            send_soundtech_confirm(gid)
            st.success("Sound tech confirmation executed. See audit below for status.")
            st.session_state.pop(f"send_soundtech_last_error_{gid}", None)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            st.session_state[f"send_soundtech_last_error_{gid}"] = (f"{type(e).__name__}: {e}", tb)
            st.error(f"Unable to send the email: {e}")

    # Persisted error details
    persisted = st.session_state.get(f"send_soundtech_last_error_{gid}")
    if persisted:
        msg, tb = persisted
        with st.expander("Show detailed error trace", expanded=True):
            st.code(tb, language="python")

    # Show last 10 audit rows inline (helps confirm write path even on dry-run)
    with st.expander("Recent email_audit entries (last 10)", expanded=True):
        try:
            audit_df = _select_df(
                "email_audit",
                "ts, kind, status, gig_id, recipient_email, token",
                None,
                limit=200
            )
            if not audit_df.empty:
                audit_df = audit_df[audit_df["kind"].isin(["soundtech_confirm"])].sort_values("ts", ascending=False).head(10)
                st.dataframe(audit_df, use_container_width=True)
            else:
                st.caption("No audit rows available to display.")
        except Exception as _:
            st.caption("Could not load email_audit (possibly RLS).")
