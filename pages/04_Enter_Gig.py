# pages/04_Enter_Gig.py
from __future__ import annotations
import os, re
from typing import Dict, List, Optional, Set, Tuple
from datetime import date, time, datetime, timedelta
import random
import traceback
import pandas as pd
import streamlit as st
from supabase import create_client, Client
from tools.send_player_confirms import _insert_email_audit
from lib.calendar_utils import upsert_band_calendar_event
from lib.ui_header import render_header
from lib.ui_format import format_currency  # kept for parity / future use
from lib.auth import IS_ADMIN

# ============================
# Page config + Auth gate
# ============================
st.set_page_config(page_title="Enter Gig", page_icon="📝", layout="wide")

# --- Login Gate ---
if "user" not in st.session_state or not st.session_state["user"]:
    st.error("Please sign in from the Login page.")
    st.stop()

# --- Admin Gate (MUST run before header/UI) ---
if not IS_ADMIN():
    st.error("You do not have permission to enter gigs.")
    st.stop()

render_header(title="Enter Gig", emoji="")
st.markdown("---")

# --- AUTH & CALENDAR DIAGNOSTICS (on demand) ---
try:
    from lib.calendar_utils import debug_auth_config, debug_calendar_access

    with st.expander("Calendar diagnostics (optional)", expanded=False):
        st.caption("Diagnostics: calendar_utils v2025-11-12a")

        run_diag = st.button("Run calendar diagnostics")
        if run_diag:
            auth_cfg = debug_auth_config()
            if not auth_cfg.get("has_google_oauth"):
                st.error(
                    "Google OAuth config missing in st.secrets['google_oauth']. "
                    f"Present: {auth_cfg.get('present_keys') or []}  "
                    f"Missing: {auth_cfg.get('missing_keys') or ['client_id','client_secret','refresh_token']}"
                )
            else:
                try:
                    dbg = debug_calendar_access("Philly Rock and Soul")
                    if dbg.get("events_list_ok"):
                        st.success(
                            "Google auth OK  \n"
                            f"CalendarId: {dbg.get('target_calendar_id')}  \n"
                            "events.list: allowed (scope: calendar.events)"
                        )
                    else:
                        st.error(
                            "Calendar access check failed (events.list): "
                            f"{dbg.get('error')}"
                        )
                except Exception as e:
                    st.error(f"Calendar access check failed: {e}")
except Exception as e:
    st.error(f"Calendar diagnostics unavailable (import/exec error): {e}")

# === Email autosend toggles — init keys (do not use `value=` anywhere) ===
for _k, _default in [
    ("autoc_send_st_on_create", True),
    ("autoc_send_agent_on_create", True),
    ("autoc_send_players_on_create", True),
]:
    if _k not in st.session_state:
        st.session_state[_k] = _default

# --- Safe admin accessor (must exist before any calls) ---
# def _IS_ADMIN() -> bool:
    # u = st.session_state.get("user") or {}
    # return bool(
        # st.session_state.get("IS_ADMIN",
            # st.session_state.get("is_admin",
                # u.get("is_admin", True)  # default True so toggles render if unknown
            # )
        # )
    # )

# === Email autosend toggles UI (admin-only) ===
if IS_ADMIN():
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
# ---- Safe rerun helper ----
def _safe_rerun():
    """Prefer st.rerun(); never call experimental_rerun."""
    import streamlit as st
    try:
        st.rerun()
    except Exception:
        # If rerun genuinely fails (rare), just continue this run.
        # The queue runner at top will still process on the next user action.
        pass

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

# ---------- Autosend session guard ----------
def _autosend_once(stage: str, gig_id: str) -> bool:
    """True the first time per (stage,gig) for this session; False thereafter."""
    import streamlit as st
    k = f"autosend_once::{stage}::{gig_id}"
    if st.session_state.get(k):
        return False
    st.session_state[k] = True
    return True

# ===== AUTOSEND RUNTIME (queue + resumable per-channel) =====
def _autosend_run_for(gig_id_str: str):
    # Snapshot (for log)
    snap = {
        "is_admin": bool(IS_ADMIN()),
        "toggles": {
            "agent": bool(st.session_state.get("autoc_send_agent_on_create", False)),
            "soundtech": bool(st.session_state.get("autoc_send_st_on_create", False)),
            "players": bool(st.session_state.get("autoc_send_players_on_create", False)),
        },
        "gig_id_str": gig_id_str,
    }
    _log("snapshot", f"{snap}")

    # Per-gig progress
    prog_key = f"autosend_progress__{gig_id_str}"
    prog = st.session_state.get(prog_key) or {"st": False, "agent": False, "players": False}
    st.session_state[prog_key] = prog

    def _mark_done(name: str):
        prog[name] = True
        st.session_state[prog_key] = prog

    def _need_more() -> bool:
        return not (prog["st"] and prog["agent"] and prog["players"])

    def _bump_and_rerun(why: str):
        _log("system", f"Autosend controlled rerun: {why}")
        _safe_rerun()

    # ---- Channel 1: Sound-tech ----
    _enabled_st = (IS_ADMIN() and st.session_state.get("autoc_send_st_on_create", False)
                   and not st.session_state.get("sound_by_venue_in", False))
    if not _enabled_st:
        _log("Sound-tech confirmation",
             f"SKIPPED: is_admin={IS_ADMIN()}, toggle={st.session_state.get('autoc_send_st_on_create', False)}, "
             f"sound_by_venue={st.session_state.get('sound_by_venue_in', False)}")
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
                _bump_and_rerun("advance to agent/players")
                return

    # ---- Channel 2: Agent ----
    _enabled_agent = (IS_ADMIN() and st.session_state.get("autoc_send_agent_on_create", False))
    if not _enabled_agent:
        _log("Agent confirmation",
             f"SKIPPED: is_admin={IS_ADMIN()}, toggle={st.session_state.get('autoc_send_agent_on_create', False)}")
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

    # ---- Channel 3: Players ----
    _enabled_players = (IS_ADMIN() and st.session_state.get("autoc_send_players_on_create", False))
    if not _enabled_players:
        _log("Player confirmations",
             f"SKIPPED: is_admin={IS_ADMIN()}, toggle={st.session_state.get('autoc_send_players_on_create', False)}")
        _mark_done("players")
    elif not prog["players"]:
        try:
            # Take the guard here so log+send happen in the same rerun
            if not _autosend_once("players", gig_id_str):
                _log("Player confirmations", "Skipped by guard (already handled this session).")
            else:
                _log("Player confirmations", "Calling sender...")
                from tools.send_player_confirms import send_player_confirms
                send_player_confirms(gig_id_str)
                st.toast("📧 Players emailed.", icon="📧")
                _log("Player confirmations", "Sent OK.")
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
        if st.session_state["autosend_queue"] and st.session_state["autosend_queue"][0] == gig_id_str:
            st.session_state["autosend_queue"] = st.session_state["autosend_queue"][1:]

# Run the queue head (if any) every run (independent of form state)
if st.session_state["autosend_queue"]:
    _autosend_run_for(st.session_state["autosend_queue"][0])



# ============================
# Secrets / Supabase
# ============================
def _get_secret(name: str, default=None, required: bool = False):
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

# Attach session for RLS
if st.session_state.get("sb_access_token") and st.session_state.get("sb_refresh_token"):
    try:
        sb.auth.set_session(
            access_token=st.session_state["sb_access_token"],
            refresh_token=st.session_state["sb_refresh_token"],
        )
    except Exception as e:
        st.warning(f"Could not attach session; proceeding with limited access. ({e})")
        
# ---- Process pending calendar upsert (rerun-proof) ----
try:
    from lib.calendar_utils import upsert_band_calendar_event
    _pending = st.session_state.pop("pending_cal_upsert", None)
    if _pending:
        res = upsert_band_calendar_event(
            _pending["gig_id"],
            sb,
            _pending.get("calendar_name", "Philly Rock and Soul"),
        )
        if "error" in res:
            st.error(f"Calendar upsert failed at [{res.get('stage')}]: {res.get('error')}")
            if res.get("calendarId"):
                st.info(f"calendarId: {res['calendarId']}")
        else:
            action = res.get("action", "ok").title()
            st.success(f"🗓️ {action} calendar event: {res.get('summary','(no title)')}")
            eid = res.get("eventId"); cid = res.get("calendarId")
            if eid and cid:
                st.info(f"Event link: https://calendar.google.com/calendar/u/0/r/eventedit/{eid}")
except Exception as e:
    st.error(f"Calendar upsert processor error: {e}")
        

# ============================
# Cached DB helpers
# ============================
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

def _filter_to_schema(table: str, payload: Dict):
    cols = _table_columns(table)
    if not cols:
        return payload
    return {k: v for k, v in payload.items() if k in cols}

def _robust_insert(table: str, payload: Dict, max_attempts: int = 8) -> Optional[Dict]:
    """
    Insert while auto-dropping unknown columns reported by PostgREST (PGRST204).
    """
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

def _insert_rows(table: str, rows: List[Dict]) -> bool:
    if not rows:
        return True
    try:
        sb.table(table).insert(rows).execute()
        return True
    except Exception as e:
        st.warning(f"Bulk insert into {table} failed: {e}")
        return False

def _opt_label(val, fallback: str = "") -> str:
    return str(val) if pd.notna(val) and str(val).strip() else fallback

def _name_for_mus_row(r: pd.Series) -> str:
    stage = _opt_label(r.get("stage_name"), "")
    if stage:
        return stage
    full = " ".join([_opt_label(r.get("first_name"), ""), _opt_label(r.get("last_name"), "")]).strip()
    return full or _opt_label(r.get("display_name"), "") or "Unnamed Musician"

# ============================
# Reference data (dropdowns)
# ============================
venues_df = _select_df("venues", "*")
sound_df  = _select_df("sound_techs", "*")
mus_df    = _select_df("musicians", "*")
agents_df = _select_df("agents", "*")

agent_labels: Dict[str, str] = {}
if not agents_df.empty and "id" in agents_df.columns:
    for _, r in agents_df.iterrows():
        # Prefer display_name, then first+last, then company
        disp = _opt_label(r.get("display_name"), "")
        if disp:
            lbl = disp
        else:
            fn = _opt_label(r.get("first_name"), "")
            ln = _opt_label(r.get("last_name"), "")
            if fn or ln:
                lbl = (fn + " " + ln).strip()
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

# Musician master labels (fallback)
mus_labels: Dict[str, str] = {}
if not mus_df.empty and "id" in mus_df.columns:
    if "active" in mus_df.columns:
        mus_df = mus_df.sort_values(by="active", ascending=False)
    for _, r in mus_df.iterrows():
        name = (
            _opt_label(r.get("stage_name"), "")
            or " ".join([_opt_label(r.get("first_name"), ""), _opt_label(r.get("last_name"), "")]).strip()
            or _opt_label(r.get("display_name"), "")
            or "Unnamed Musician"
        )
        mus_labels[str(r["id"])] = name

ROLE_CHOICES: List[str] = [
    "Male Vocals", "Female Vocals",
    "Keyboard", "Drums", "Guitar", "Bass",
    "Trumpet", "Saxophone", "Trombone",
]

# ============================
# Session defaults (pending-select + add flags)
# ============================
st.session_state.setdefault("agent_sel_pending", None)
st.session_state.setdefault("venue_sel_pending", None)
st.session_state.setdefault("sound_sel_pending", None)
st.session_state.setdefault("preselect_role_ids", {})  # role -> musician_id

st.session_state.setdefault("agent_is_add", False)
st.session_state.setdefault("venue_is_add", False)
st.session_state.setdefault("sound_is_add", False)
for _r in ROLE_CHOICES:
    st.session_state.setdefault(f"mus_is_add_{_r}", False)

# ============================
# on_change callbacks for selects
# ============================
def _on_agent_change():
    st.session_state["agent_is_add"] = (st.session_state.get("agent_sel") == "__ADD_AGENT__")

def _on_venue_change():
    st.session_state["venue_is_add"] = (st.session_state.get("venue_sel") == "__ADD_VENUE__")

def _on_sound_change():
    st.session_state["sound_is_add"] = (st.session_state.get("sound_sel") == "__ADD_SOUND__")

def _make_role_change(role: str):
    def _cb():
        st.session_state[f"mus_is_add_{role}"] = (st.session_state.get(f"mus_sel_{role}") == f"__ADD_MUS__:{role}")
    return _cb

# ============================
# Event basics (no form)
# ============================
st.subheader("Event Basics")
eb1, eb2, eb3 = st.columns([1, 1, 1])

with eb1:
    title = st.text_input("Title (optional)", placeholder="e.g., Spring Gala", key="title_in")
    event_date = st.date_input("Date of Performance", value=date.today(), key="event_date_in")

def _ampm_time_input(label: str, default_hour: int, default_min: int, key_prefix: str) -> time:
    c1, c2, c3 = st.columns([1, 1, 1])
    with c1:
        default_index = (default_hour % 12) - 1
        if default_index < 0:
            default_index = 11
        hr12 = st.selectbox(f"{label} Hour", list(range(1, 13)), index=default_index, key=f"{key_prefix}_hr")
    with c2:
        minute = st.selectbox(f"{label} Min", [0, 15, 30, 45], index=default_min // 15, key=f"{key_prefix}_min")
    with c3:
        ampm = st.selectbox("AM/PM", ["AM", "PM"], index=0 if default_hour < 12 else 1, key=f"{key_prefix}_ampm")
    hour24 = (hr12 % 12) + (12 if ampm == "PM" else 0)
    return time(hour24, minute)

with eb2:
    contract_status = st.selectbox("Status", ["Pending", "Hold", "Confirmed"], index=0, key="status_in")
    fee = st.number_input("Contracted Fee ($)", min_value=0.0, step=50.0, format="%.2f", key="fee_in")
    start_time_in = _ampm_time_input("Start", 9, 0, key_prefix="start_time_in")
    end_time_in   = _ampm_time_input("End",   1, 0, key_prefix="end_time_in")

with eb3:
    band_name = st.text_input("Band (optional)", placeholder="PRS", key="band_in")  # not stored today

# ============================
# Agent select + inline add (anchor under the select)
# ============================
AGENT_ADD = "__ADD_AGENT__"
agent_options = [""] + list(agent_labels.keys()) + [AGENT_ADD]
def agent_fmt(x: str) -> str:
    if x == "": return "(none)"
    if x == AGENT_ADD: return "(+ Add New Agent)"
    return agent_labels.get(x, x)

_pending_agent = st.session_state.pop("agent_sel_pending", None)
if _pending_agent:
    st.session_state["agent_sel"] = _pending_agent

ag_col, _ = st.columns([1, 1])
with ag_col:
    agent_id_sel = st.selectbox(
        "Agent",
        options=agent_options,
        format_func=agent_fmt,
        key="agent_sel",
        on_change=_on_agent_change,
    )
    agent_add_box = st.empty()  # anchor immediately below the select

# ============================
# Venue & Sound
# ============================
st.markdown("---")
st.subheader("Venue & Sound")

vs1, vs2 = st.columns([1, 1])
with vs1:
    VENUE_ADD = "__ADD_VENUE__"
    venue_options_ids = [""] + list(venue_labels.keys()) + [VENUE_ADD]
    def venue_fmt(x: str) -> str:
        if x == "": return "(select venue)"
        if x == VENUE_ADD: return "(+ Add New Venue)"
        return venue_labels.get(x, x)

    _pending_venue = st.session_state.pop("venue_sel_pending", None)
    if _pending_venue:
        st.session_state["venue_sel"] = _pending_venue

    venue_id_sel = st.selectbox(
        "Venue",
        options=venue_options_ids,
        format_func=venue_fmt,
        key="venue_sel",
        on_change=_on_venue_change,
    )
    venue_add_box = st.empty()  # anchor directly under the select

    is_private = st.checkbox("Private Event?", value=False, key="is_private_in")
    eligible_1099 = st.checkbox("1099 Eligible", value=False, key="eligible_1099_in")

with vs2:
    SOUND_ADD = "__ADD_SOUND__"
    sound_options_ids = [""] + list(sound_labels.keys()) + [SOUND_ADD]
    def sound_fmt(x: str) -> str:
        if x == "": return "(none)"
        if x == SOUND_ADD: return "(+ Add New Sound Tech)"
        return sound_labels.get(x, x)

    _pending_sound = st.session_state.pop("sound_sel_pending", None)
    if _pending_sound:
        st.session_state["sound_sel"] = _pending_sound

    sound_id_sel = st.selectbox(
        "Confirmed Sound Tech",
        options=sound_options_ids,
        format_func=sound_fmt,
        key="sound_sel",
        on_change=_on_sound_change,
    )
    sound_add_box = st.empty()  # anchor directly under the select
    sound_by_venue = st.checkbox("Sound provided by venue?", value=False, key="sound_by_venue_in")

# Optional venue-provided sound contact fields
if sound_by_venue:
    sv1, sv2 = st.columns([1, 1])
    with sv1:
        sound_by_venue_name = st.text_input("Venue Sound Company/Contact (optional)", key="sv_name_in")
    with sv2:
        sound_by_venue_phone = st.text_input("Venue Sound Phone/Email (optional)", key="sv_phone_in")
    sound_fee_val = None
else:
    sound_by_venue_name = ""
    sound_by_venue_phone = ""
    _sfee = st.number_input("Sound Fee ($, optional)", min_value=0.0, step=25.0, format="%.2f", key="sound_fee_in")
    sound_fee_val = float(_sfee) if pd.notna(_sfee) else None

# ============================
# Lineup (Role Assignments)
# ============================
st.markdown("---")
st.subheader("Lineup (Role Assignments)")
lineup_cols = st.columns(3)
preselect_roles: Dict[str, Optional[str]] = st.session_state.get("preselect_role_ids", {})

def _norm(s: str) -> str:
    return (str(s or "").strip().lower())

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

def _role_labels_for(role: str) -> Dict[str, str]:
    role_df = mus_df.copy()
    if "instrument" in role_df.columns:
        role_df = role_df[role_df["instrument"].fillna("").apply(lambda x: _matches_role(x, role))]
    if role_df.empty:
        role_df = mus_df
    if not role_df.empty and "active" in role_df.columns:
        role_df = role_df.sort_values(by="active", ascending=False)
    labels: Dict[str, str] = {}
    if not role_df.empty and "id" in role_df.columns:
        for _, r in role_df.iterrows():
            labels[str(r["id"])] = _name_for_mus_row(r)
    return labels

role_add_boxes: Dict[str, st.delta_generator.DeltaGenerator] = {}
for idx, role in enumerate(ROLE_CHOICES):
    with lineup_cols[idx % 3]:
        sentinel = f"__ADD_MUS__:{role}"
        role_labels = _role_labels_for(role)
        mus_options_ids = [""] + list(role_labels.keys()) + [sentinel]

        def mus_fmt(x: str, _role=role):
            if x == "": return "(unassigned)"
            if x.startswith("__ADD_MUS__"): return "(+ Add New Musician)"
            return role_labels.get(x, mus_labels.get(x, x))

        pre_id = preselect_roles.get(role) or ""
        sel_index = mus_options_ids.index(pre_id) if pre_id in mus_options_ids else 0
        st.selectbox(
            role,
            options=mus_options_ids,
            index=sel_index,
            format_func=mus_fmt,
            key=f"mus_sel_{role}",
            on_change=_make_role_change(role),
        )
        role_add_boxes[role] = st.empty()  # anchor directly under the role select

# ============================
# Notes & Private details
# ============================
st.markdown("---")
st.subheader("Notes")
st.text_area(
    "Notes / Special Instructions (optional)",
    height=100,
    placeholder="Load-in details, parking, dress code, etc.",
    key="notes_in",
)

if st.session_state.get("is_private_in", False):

    st.markdown("#### Private Event Details")
    p1, p2 = st.columns([1, 1])

    with p1:
        st.text_input("Type of Event (e.g., Wedding, Corporate, Birthday)", key="priv_type_in")
        st.text_input("Organizer / Company", key="priv_org_in")
        st.text_input("Guest(s) of Honor / Bride/Groom", key="priv_gh_in")

    with p2:
        st.text_input("Primary Contact (name)", key="priv_contact_in")
        st.text_input("Client Email", key="priv_client_email_in", placeholder="email")
        st.text_input("Client Phone", key="priv_client_phone_in", placeholder="phone")
        st.text_input("Additional Musicians/Services (optional)", key="priv_addsvc_in")
        
    # -------------------------------
    # Organizer Address (NEW FIELDS)
    # -------------------------------
    st.markdown("##### Organizer Address")
    a1, a2 = st.columns([2, 1])

    with a1:
        st.text_input("Street Address", key="priv_addr_street_in")
        st.text_input("City", key="priv_addr_city_in")

    with a2:
        st.text_input("State", key="priv_addr_state_in")
        st.text_input("Zip Code", key="priv_addr_zip_in")

    st.markdown("### Contract-Specific Details")

    priv_special_instructions_in = st.text_area(
        "Special Instructions (Contract Only)",
        placeholder="Run of show, ceremony info, dinner/cocktail coverage, timing, etc.",
        height=120,
        key="priv_special_instructions_in",
    )

    priv_cocktail_coverage_in = st.text_input(
        "Cocktail Coverage (optional)",
        placeholder="e.g. Trio for cocktail hour",
        key="priv_cocktail_coverage_in",
    )      
        
    # -------------------------------
    # Overtime Rate (NEW FIELD)
    # -------------------------------
    st.markdown("##### Overtime Rate")
    st.text_input(
        "Overtime Rate (e.g., $300/hr)",
        key="priv_ot_rate_in",
        placeholder="Optional"
    )
      

# ============================
# Finance (Admin Only)
# ============================
deposit_rows: List[Dict] = []
if IS_ADMIN():
    st.markdown("---")
    st.subheader("Finance (Admin Only)")
    add_deps = st.number_input(
        "Number of deposits (0–4)", min_value=0, max_value=4, step=1,
        value=int(st.session_state.get("num_deposits", 0)), key="num_deposits"
    )
    for i in range(int(add_deps)):
        cdl, cda, cdm = st.columns([1, 1, 1])
        with cdl:
            st.date_input(f"Deposit {i+1} due", value=date.today(), key=f"dep_due_{i}")
        with cda:
            st.number_input(f"Deposit {i+1} amount", min_value=0.0, step=50.0, format="%.2f", key=f"dep_amt_{i}")
        with cdm:
            st.checkbox(f"Deposit {i+1} is % of fee", value=False, key=f"dep_pct_{i}")

# ============================
# Add-New sub-forms (rendered in the anchors right below each select)
# ============================

# --- Agent add (now with Email + Phone; dedupe by email, case-insensitive) ---
if st.session_state.get("agent_is_add"):
    with agent_add_box.container():
        with st.expander("➕ Add New Agent", expanded=True):
            a1, a2 = st.columns([1, 1])
            with a1:
                new_agent_first = st.text_input("First Name (optional)", key="new_agent_first")
                new_agent_last  = st.text_input("Last Name (optional)", key="new_agent_last")
                new_agent_company = st.text_input("Company (optional)", key="new_agent_company")
            with a2:
                new_agent_email = st.text_input("Email (optional; used for dedupe)", key="new_agent_email")
                new_agent_phone = st.text_input("Phone (optional)", key="new_agent_phone")
                new_agent_1099  = st.text_input("Name for 1099 (optional)", key="new_agent_1099")

            c1, c2 = st.columns([1, 1])
            if c1.button("Create Agent", key="create_agent_btn"):
                email_val = (new_agent_email or "").strip()
                # If email provided, try to find existing (case-insensitive)
                existing_id: Optional[str] = None
                if email_val:
                    try:
                        # ilike is case-insensitive comparison
                        res = sb.table("agents").select("id,email").ilike("email", email_val).limit(1).execute()
                        rows = res.data or []
                        if rows:
                            existing_id = str(rows[0]["id"])
                    except Exception as e:
                        st.warning(f"Lookup by email failed, proceeding to insert new agent. ({e})")

                if existing_id:
                    st.session_state["agent_is_add"] = False
                    st.session_state["agent_sel_pending"] = existing_id
                    st.success("Agent with that email already exists; selecting existing record.")
                    st.cache_data.clear()
                    st.rerun()
                else:
                    payload = _filter_to_schema("agents", {
                        "first_name": (new_agent_first or "").strip() or None,
                        "last_name":  (new_agent_last or "").strip() or None,
                        "company":    (new_agent_company or "").strip() or None,
                        "email":      email_val or None,
                        "phone":      (new_agent_phone or "").strip() or None,
                        "name_for_1099": (new_agent_1099 or "").strip() or None,
                        "active": True,
                    })
                    row = _robust_insert("agents", payload)
                    if row and "id" in row:
                        st.cache_data.clear()
                        st.session_state["agent_is_add"] = False
                        st.session_state["agent_sel_pending"] = str(row["id"])
                        st.success("Agent created ✅")
                        st.rerun()

            if c2.button("Cancel", key="cancel_agent_btn"):
                st.session_state["agent_is_add"] = False
                st.session_state["agent_sel"] = ""
                st.rerun()

# --- Venue add ---
if st.session_state.get("venue_is_add"):
    with venue_add_box.container():
        with st.expander("➕ Add New Venue", expanded=True):
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

            c1, c2 = st.columns([1, 1])
            if c1.button("Create Venue", key="create_venue_btn"):
                payload = _filter_to_schema("venues", {
                    "name": (new_v_name or "").strip() or None,
                    "address_line1": (new_v_addr1 or "").strip() or None,
                    "address_line2": (new_v_addr2 or "").strip() or None,
                    "city": (new_v_city or "").strip() or None,
                    "state": (new_v_state or "").strip() or None,
                    "postal_code": (new_v_zip or "").strip() or None,
                    "phone": (new_v_phone or "").strip() or None,
                })
                row = _robust_insert("venues", payload)
                if row and "id" in row:
                    st.cache_data.clear()
                    st.session_state["venue_is_add"] = False
                    st.session_state["venue_sel_pending"] = str(row["id"])
                    st.success("Venue created ✅")
                    st.rerun()
            if c2.button("Cancel", key="cancel_venue_btn"):
                st.session_state["venue_is_add"] = False
                st.session_state["venue_sel"] = ""
                st.rerun()

# --- Sound tech add ---
if st.session_state.get("sound_is_add"):
    with sound_add_box.container():
        with st.expander("➕ Add New Sound Tech", expanded=True):
            s1, s2 = st.columns([1, 1])
            with s1:
                new_st_name   = st.text_input("Display Name", key="new_st_name")
                new_st_phone  = st.text_input("Phone (optional)", key="new_st_phone")
            with s2:
                new_st_company = st.text_input("Company (optional)", key="new_st_company")
                new_st_email   = st.text_input("Email (optional)", key="new_st_email")

            c1, c2 = st.columns([1, 1])
            if c1.button("Create Sound Tech", key="create_sound_btn"):
                payload = _filter_to_schema("sound_techs", {
                    "display_name": (new_st_name or "").strip() or None,
                    "company": (new_st_company or "").strip() or None,
                    "phone": (new_st_phone or "").strip() or None,
                    "email": (new_st_email or "").strip() or None,
                })
                row = _robust_insert("sound_techs", payload)
                if row and "id" in row:
                    st.cache_data.clear()
                    st.session_state["sound_is_add"] = False
                    st.session_state["sound_sel_pending"] = str(row["id"])
                    st.success("Sound Tech created ✅")
                    st.rerun()
            if c2.button("Cancel", key="cancel_sound_btn"):
                st.session_state["sound_is_add"] = False
                st.session_state["sound_sel"] = ""
                st.rerun()

# --- Musician add per role ---
for role in ROLE_CHOICES:
    sel_id = st.session_state.get(f"mus_sel_{role}", "")
    sentinel = f"__ADD_MUS__:{role}"
    if st.session_state.get(f"mus_is_add_{role}") and sel_id == sentinel:
        with role_add_boxes[role].container():
            with st.expander(f"➕ Add New Musician for {role}", expanded=True):
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
                if c1.button("Create Musician", key=f"create_mus_btn_{role}"):
                    payload = _filter_to_schema("musicians", {
                        "first_name": (new_mus_fn or "").strip() or None,
                        "last_name": (new_mus_ln or "").strip() or None,
                        "stage_name": (new_mus_stage or "").strip() or None,
                        "instrument": (new_mus_instr or role or "").strip() or None,
                        "active": True if "active" in _table_columns("musicians") else None,
                    })
                    row = _robust_insert("musicians", payload)
                    if row and "id" in row:
                        st.cache_data.clear()
                        pre = st.session_state.get("preselect_role_ids", {})
                        pre[role] = str(row["id"])
                        st.session_state["preselect_role_ids"] = pre
                        st.session_state[f"mus_is_add_{role}"] = False
                        st.success(f"Musician created and preselected for {role} ✅")
                        st.rerun()
                if c2.button("Cancel", key=f"cancel_mus_btn_{role}"):
                    st.session_state[f"mus_is_add_{role}"] = False
                    st.session_state[f"mus_sel_{role}"] = ""
                    st.rerun()


# ============================
# SAVE button (single path)
# ============================
def _compose_datetimes(event_dt: date, start_t: time, end_t: time) -> Tuple[datetime, datetime]:
    start_dt = datetime.combine(event_dt, start_t)
    end_dt = datetime.combine(event_dt, end_t)
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)
    return start_dt, end_dt

# -------------------------------------------------------------------
# Helper: resilient gig loader for immediate post-save email lookups
# -------------------------------------------------------------------
def _load_gig_for_email(gid: str, tries: int = 3):
    """Fetches the gig directly from the base table (not joined views),
    retrying briefly to let related inserts become visible."""
    import time
    for _ in range(tries):
        try:
            row = sb.table("gigs").select("*").eq("id", gid).limit(1).execute().data
            if row:
                return row[0]
        except Exception:
            pass
        time.sleep(0.1)  # short micro-wait
    return None

if st.button("💾 Save Gig", type="primary", key="enter_save_btn"):
    # Guard: if PRS provides sound but sentinel selected, block save
    if (not st.session_state.get("sound_by_venue_in", False)) and st.session_state.get("sound_sel") == "__ADD_SOUND__":
        st.error("Finish creating the new sound tech (click “Create Sound Tech”) or choose an existing one before saving.")
        st.stop()

    start_dt, end_dt = _compose_datetimes(
        event_date,            # local from date_input
        start_time_in,         # local from _ampm_time_input
        end_time_in,           # local from _ampm_time_input
    )
    if end_dt.date() > start_dt.date():
        st.info(
            f"This gig ends next day ({end_dt.strftime('%Y-%m-%d %I:%M %p')}). "
            "We’ll save event_date as the start date and keep your end time as entered."
        )

    # Resolve IDs
    agent_sel = st.session_state.get("agent_sel")
    agent_id_val = agent_sel if agent_sel not in ("", "__ADD_AGENT__") else None

    venue_sel = st.session_state.get("venue_sel")
    venue_id_val = venue_sel if venue_sel not in ("", "__ADD_VENUE__") else None

    sound_sel = st.session_state.get("sound_sel")
    sound_tech_id_val = (
        None if st.session_state.get("sound_by_venue_in", False)
        else (sound_sel if sound_sel not in ("", "__ADD_SOUND__") else None)
    )

    # Build payload (sound_fee guard NaN→None already handled; may be absent in schema)
    _sfee_val = st.session_state.get("sound_fee_in", None)
    if _sfee_val is not None and pd.isna(_sfee_val):
        _sfee_val = None

    gig_payload = {
        "title": st.session_state.get("title_in") or None,
        "event_date": event_date.isoformat(),
        "start_time": start_time_in.strftime("%H:%M:%S"),
        "end_time":   end_time_in.strftime("%H:%M:%S"),
        "contract_status": st.session_state.get("status_in"),
        "fee": float(st.session_state.get("fee_in") or 0.0) or None,
        "agent_id": agent_id_val,
        "venue_id": venue_id_val,
        "sound_tech_id": sound_tech_id_val,
        "is_private": bool(st.session_state.get("is_private_in", False)),
        "notes": st.session_state.get("notes_in") or None,
        "sound_by_venue_name": st.session_state.get("sv_name_in") or None,
        "sound_by_venue_phone": st.session_state.get("sv_phone_in") or None,
        "sound_fee": float(_sfee_val) if (_sfee_val is not None and _sfee_val != 0.0) else None,
        "organizer_street": st.session_state.get("priv_addr_street_in") or None,
        "organizer_city": st.session_state.get("priv_addr_city_in") or None,
        "organizer_state": st.session_state.get("priv_addr_state_in") or None,
        "organizer_zip": st.session_state.get("priv_addr_zip_in") or None,
        "overtime_rate": st.session_state.get("priv_ot_rate_in") or None,
        # private block (only if is_private)
        # "private_event_type": st.session_state.get("priv_type_in") or None if st.session_state.get("is_private_in") else None,
        # "organizer": st.session_state.get("priv_org_in") or None if st.session_state.get("is_private_in") else None,
        # "guest_of_honor": st.session_state.get("priv_gh_in") or None if st.session_state.get("is_private_in") else None,
        # "private_contact": st.session_state.get("priv_contact_in") or None if st.session_state.get("is_private_in") else None,
        # "private_contact_info": st.session_state.get("priv_contact_info_in") or None if st.session_state.get("is_private_in") else None,
        # "additional_services": st.session_state.get("priv_addsvc_in") or None if st.session_state.get("is_private_in") else None,
        # "overtime_rate": st.session_state.get("priv_ot_rate_in") or None,        
    }
    gig_payload = _filter_to_schema("gigs", gig_payload)

    # Insert gig
    new_gig = _robust_insert("gigs", gig_payload)
    if not new_gig:
        st.stop()

    gig_id = str(new_gig.get("id", ""))

    # ------------------------------------------------------------
    # Save private gig details into gigs_private
    # ------------------------------------------------------------
    if st.session_state.get("is_private_in", False) and _table_exists("gigs_private"):
        try:
            gp_payload = {
                "gig_id": gig_id,
                "organizer": st.session_state.get("priv_org_in") or None,
                "event_type": st.session_state.get("priv_type_in") or None,
                "honoree": st.session_state.get("priv_gh_in") or None,
                "special_instructions": st.session_state.get("priv_special_instructions_in") or None,
                "cocktail_coverage": st.session_state.get("priv_cocktail_coverage_in") or None,
                "client_name": st.session_state.get("priv_contact_in") or None,
            }

            # Separate email + phone
            email = st.session_state.get("priv_client_email_in")
            phone = st.session_state.get("priv_client_phone_in")
            if email:
                gp_payload["client_email"] = email
            if phone:
                gp_payload["client_phone"] = phone

            # Mailing address assembly (optional)
            street = st.session_state.get("priv_addr_street_in")
            city = st.session_state.get("priv_addr_city_in")
            state_val = st.session_state.get("priv_addr_state_in")
            zip_val = st.session_state.get("priv_addr_zip_in")
            lines = []
            if street:
                lines.append(street)
            if city or state_val or zip_val:
                line2 = ", ".join([c for c in [city, state_val] if c])
                if zip_val:
                    line2 = f"{line2} {zip_val}".strip()
                lines.append(line2)
            if lines:
                gp_payload["client_mailing_address"] = "\n".join(lines)

            gp_payload = _filter_to_schema("gigs_private", gp_payload)
            if gp_payload:
                sb.table("gigs_private").upsert(gp_payload, on_conflict="gig_id").execute()

        except Exception as e:
            st.error(f"Could not save private gig details: {e}")

    # gig_musicians
    if _table_exists("gig_musicians"):
        gm_rows: List[Dict] = []
        for role in ROLE_CHOICES:
            sel = st.session_state.get(f"mus_sel_{role}", "")
            if sel and not sel.startswith("__ADD_MUS__"):
                gm_rows.append(_filter_to_schema("gig_musicians", {
                    "gig_id": gig_id,
                    "role": role,
                    "musician_id": sel,
                }))
        if gm_rows:
            _insert_rows("gig_musicians", gm_rows)

    # gig_deposits
    if IS_ADMIN() and _table_exists("gig_deposits"):
        rows: List[Dict] = []
        n = int(st.session_state.get("num_deposits", 0))
        for i in range(n):
            due = st.session_state.get(f"dep_due_{i}", date.today())
            amt = float(st.session_state.get(f"dep_amt_{i}", 0.0) or 0.0)
            is_pct = bool(st.session_state.get(f"dep_pct_{i}", False))
            rows.append(_filter_to_schema("gig_deposits", {
                "gig_id": gig_id,
                "seq": i + 1,
                "due_date": due.isoformat() if isinstance(due, (date, datetime)) else due,
                "amount": amt,
                "is_percentage": is_pct,
            }))
        if rows:
            _insert_rows("gig_deposits", rows)
            
    # ---- After successful INSERT/UPDATE ----
    # Queue calendar upsert; the top-of-page processor will execute it reliably
    try:
        gid = str(gig_id).strip()
        if not gid:
            raise ValueError("Missing gig_id for calendar upsert")
        st.session_state["pending_cal_upsert"] = {
            "gig_id": gid,
            "calendar_name": "Philly Rock and Soul",
        }
    except Exception as e:
        st.warning(f"Could not queue calendar upsert: {e}")


    
    # gig_id_str must be a string UUID for the saved gig
    if any([
        st.session_state.get("autoc_send_st_on_create", False),
        st.session_state.get("autoc_send_agent_on_create", False),
        st.session_state.get("autoc_send_players_on_create", False),
    ]):
        guard_key = f"autosend_guard__{gig_id}"
        if not st.session_state.get(guard_key, False):
            # queue and rerun
            if gig_id not in st.session_state["autosend_queue"]:
                st.session_state["autosend_queue"].append(gig_id)
            try:
                st.rerun()
            except Exception:
                _safe_rerun()

    
        
    # Success summary
    def _fmt12(t: time) -> str:
        dt0 = datetime(2000, 1, 1, t.hour, t.minute)
        return dt0.strftime("%I:%M %p").lstrip("0")

    st.cache_data.clear()
    st.success("Gig saved successfully ✅")

    # Schedule a rerun-proof upsert (kept)
    st.session_state["pending_cal_upsert"] = {
        "gig_id": gig_id,
        "calendar_name": "Philly Rock and Soul",  # must match elsewhere
    }

    # Also perform the upsert immediately so posting doesn't rely on a rerun
    try:
        res = upsert_band_calendar_event(
            gig_id=gig_id,
            sb=sb,
            calendar_name="Philly Rock and Soul",  # keep this exact name
        )
        # === NEW: inspect result dict so we don't show success on silent errors ===
        if isinstance(res, dict) and res.get("error"):
            st.error(f"Calendar upsert failed: {res.get('error')} (stage: {res.get('stage')})")
            # Optional: show more detail during debug
            st.write({"calendarId": res.get("calendarId")})
        else:
            action = (res or {}).get("action", "updated")
            ev_id  = (res or {}).get("eventId")
            st.success(f"PRS Calendar {action}.")
            if ev_id:
                st.caption(f"Event ID: {ev_id}")
    except Exception as e:
        st.error(f"Calendar upsert exception: {e}")

    st.write({
        "id": gig_id,
        "title": new_gig.get("title"),
        "event_date": new_gig.get("event_date"),
        "start_time (12-hr)": _fmt12(start_time_in),
        "end_time (12-hr)": _fmt12(end_time_in),
        "status": new_gig.get("contract_status"),
        "fee": new_gig.get("fee"),
    })

    # Optional: store the actual time objects in session for reuse elsewhere
    st.session_state["start_time_in_obj"] = start_time_in
    st.session_state["end_time_in_obj"]   = end_time_in

    st.info("Open the Schedule View to verify the new gig appears with Venue / Location / Sound.")

  
