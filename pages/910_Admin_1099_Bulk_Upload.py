from __future__ import annotations

import os
import streamlit as st
import pandas as pd
from datetime import datetime
from typing import Optional, Dict, Any

from supabase import create_client, Client
from auth_helper import require_admin
from lib.tax_crypto import get_fernet, encrypt_tin, normalize_tin, last4

# ------------------------------
# Secrets + Clients
# ------------------------------
def _get_secret(name: str, default=None, required: bool = False) -> Optional[str]:
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
SUPABASE_SERVICE_KEY = _get_secret("SUPABASE_SERVICE_KEY", required=True)

sb: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
sb_svc: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# Attach session (admin gate uses this)
if st.session_state.get("sb_access_token") and st.session_state.get("sb_refresh_token"):
    try:
        sb.auth.set_session(
            access_token=st.session_state["sb_access_token"],
            refresh_token=st.session_state["sb_refresh_token"],
        )
    except Exception as e:
        st.warning(f"Could not attach Supabase session. ({e})")

user, session, user_id = require_admin()
if not user:
    st.stop()

st.title("Admin: 1099 Bulk Upload (Addresses + Tax Info)")

# ------------------------------
# Config
# ------------------------------
tax_key = _get_secret("TAX_ENCRYPTION_KEY", required=True)
fernet = get_fernet(tax_key)

PAYEE_TYPE_MAP = {
    "musicians": "musician",
    "agents": "agent",
    "sound_techs": "sound_tech",
}

ULTRA_CANON = {
    "display_name": "Ultra Artists",
    "company": "Ultra Artists",
    "address": "40 Maple Ave",
    "city": "Morristown",
    "state": "NJ",
    "zip": "07960",
    "active": True,
}

REQUIRED_COLS = [
    "target_table",   # musicians | sound_techs | agents
    "matched_id",     # uuid (may be blank for new agents)
    "name_for_1099_source",
    "first_name",
    "middle_name",
    "last_name",
    "Email",
    "Phone",
    "street",
    "city",
    "state",
    "zip",
    "tax_id",
    "w9_received",
]

def _safe_str(x) -> str:
    if x is None:
        return ""
    s = str(x).strip()
    return "" if s.lower() == "nan" else s

def _mask_tax(t: str) -> str:
    d = normalize_tin(t)
    if not d:
        return ""
    return f"***-**-{d[-4:]}"

def _upsert_payee_tax(payee_type: str, payee_id: str, tax_id: str, w9_received: bool):
    tax_digits = normalize_tin(tax_id)
    if not tax_digits:
        return "no_tax_id"
    payload = {
        "payee_type": payee_type,
        "payee_id": payee_id,
        "tin_last4": last4(tax_digits),
        "tin_ciphertext": encrypt_tin(tax_digits, fernet),
        "tin_key_version": 1,
        "w9_received": bool(w9_received),
    }
    sb_svc.table("payee_tax_info").upsert(payload).execute()
    return "tax_saved"

def _update_address(table: str, row_id: str, street: str, city: str, state: str, zip_code: str):
    payload = {
        "address": street or None,
        "city": city or None,
        "state": state or None,
        "zip": zip_code or None,
        "updated_at": datetime.utcnow().isoformat(),
    }
    sb_svc.table(table).update(payload).eq("id", row_id).execute()

def _ensure_ultra_agent() -> str:
    # Try find by display_name
    existing = (
        sb_svc.table("agents")
        .select("id,display_name")
        .ilike("display_name", "Ultra Artists")
        .execute()
        .data
        or []
    )
    if existing:
        ultra_id = existing[0]["id"]
        # Make sure canonical address is applied
        sb_svc.table("agents").update({
            "company": ULTRA_CANON["company"],
            "address": ULTRA_CANON["address"],
            "city": ULTRA_CANON["city"],
            "state": ULTRA_CANON["state"],
            "zip": ULTRA_CANON["zip"],
            "active": True,
            "updated_at": datetime.utcnow().isoformat(),
        }).eq("id", ultra_id).execute()
        return ultra_id

    # Insert new
    inserted = (
        sb_svc.table("agents")
        .insert({
            "display_name": ULTRA_CANON["display_name"],
            "company": ULTRA_CANON["company"],
            "address": ULTRA_CANON["address"],
            "city": ULTRA_CANON["city"],
            "state": ULTRA_CANON["state"],
            "zip": ULTRA_CANON["zip"],
            "active": True,
        })
        .execute()
        .data
        or []
    )
    if not inserted:
        raise Exception("Failed to create Ultra Artists agent record.")
    return inserted[0]["id"]

def _ensure_agent_from_row(r: dict) -> str:
    email = _safe_str(r.get("Email"))
    display_name = _safe_str(r.get("name_for_1099_source")) or _safe_str(r.get("Member")) or "Agent"

    # If Ultra, force canonical
    if "ultra" in display_name.lower():
        return _ensure_ultra_agent()

    # Try match by email
    if email:
        found = (
            sb_svc.table("agents")
            .select("id,email")
            .ilike("email", email)
            .execute()
            .data
            or []
        )
        if found:
            return found[0]["id"]

    # Insert new agent
    inserted = (
        sb_svc.table("agents")
        .insert({
            "first_name": _safe_str(r.get("first_name")) or None,
            "last_name": _safe_str(r.get("last_name")) or None,
            "display_name": display_name,
            "name_for_1099": _safe_str(r.get("name_for_1099_source")) or None,
            "phone": _safe_str(r.get("Phone")) or None,
            "email": email or None,
            "address": _safe_str(r.get("street")) or None,
            "city": _safe_str(r.get("city")) or None,
            "state": _safe_str(r.get("state")) or None,
            "zip": _safe_str(r.get("zip")) or None,
            "active": True,
        })
        .execute()
        .data
        or []
    )
    if not inserted:
        raise Exception(f"Failed to create agent for: {display_name}")
    return inserted[0]["id"]

# ------------------------------
# Upload
# ------------------------------
uploaded = st.file_uploader("Upload the reissued mapping CSV", type=["csv"])

if not uploaded:
    st.info("Upload your reissued mapping CSV (the one with target_table + matched_id + tax_id + address parts).")
    st.stop()

df = pd.read_csv(uploaded)
missing = [c for c in REQUIRED_COLS if c not in df.columns]
if missing:
    st.error(f"CSV missing required columns: {missing}")
    st.stop()

# Preview (mask tax id)
preview = df.copy()
preview["tax_id_masked"] = preview["tax_id"].apply(_mask_tax)
preview.drop(columns=["tax_id"], inplace=True)
st.subheader("Preview (Tax IDs masked)")
st.dataframe(preview.head(50), use_container_width=True)

st.warning("This will write to Supabase using the SERVICE KEY. Double-check the preview before running.")

run = st.button("RUN BULK UPLOAD", type="primary", use_container_width=True)

if run:
    results: list[dict] = []
    ultra_id: str | None = None

    rows = df.fillna("").to_dict(orient="records")

    for idx, r in enumerate(rows, start=1):
        try:
            target_table = _safe_str(r.get("target_table"))
            payee_type = PAYEE_TYPE_MAP.get(target_table)
            if not payee_type:
                raise Exception(f"Unknown target_table='{target_table}'")

            matched_id = _safe_str(r.get("matched_id"))
            street = _safe_str(r.get("street"))
            city = _safe_str(r.get("city"))
            state = _safe_str(r.get("state"))
            zip_code = _safe_str(r.get("zip"))
            tax_id = _safe_str(r.get("tax_id"))
            w9 = bool(r.get("w9_received")) if r.get("w9_received") != "" else (tax_id != "")

            # Determine payee_id (agents may be new)
            payee_id = None
            created_new = False

            if target_table == "agents":
                if matched_id:
                    payee_id = matched_id
                else:
                    payee_id = _ensure_agent_from_row(r)
                    created_new = True
            else:
                # musicians / sound_techs must match existing
                if not matched_id:
                    raise Exception("Missing matched_id (cannot update existing payee)")
                payee_id = matched_id

            # Special: enforce Ultra canonical address + company/display for agents
            name_src = _safe_str(r.get("name_for_1099_source"))
            member_src = _safe_str(r.get("Member"))
            is_ultra = ("ultra" in name_src.lower()) or ("ultra" in member_src.lower())

            if target_table == "agents" and is_ultra:
                # Ensure Ultra exists and use canonical values
                ultra_id = ultra_id or _ensure_ultra_agent()
                payee_id = ultra_id
                created_new = created_new or (matched_id == "")

                street = ULTRA_CANON["address"]
                city = ULTRA_CANON["city"]
                state = ULTRA_CANON["state"]
                zip_code = ULTRA_CANON["zip"]

            # Update address fields on the underlying table
            _update_address(target_table, payee_id, street, city, state, zip_code)

            # Upsert tax info (only if tax_id present)
            tax_status = "no_tax_id"
            if tax_id:
                tax_status = _upsert_payee_tax(payee_type, payee_id, tax_id, w9)

            results.append({
                "row": idx,
                "target_table": target_table,
                "payee_type": payee_type,
                "payee_id": payee_id,
                "created_new": created_new,
                "address_updated": True,
                "tax_status": tax_status,
                "w9_received": bool(w9),
                "name_for_1099_source": name_src or member_src,
                "email": _safe_str(r.get("Email")),
                "notes": "OK",
            })

        except Exception as e:
            results.append({
                "row": idx,
                "target_table": _safe_str(r.get("target_table")),
                "payee_type": PAYEE_TYPE_MAP.get(_safe_str(r.get("target_table"))),
                "payee_id": _safe_str(r.get("matched_id")) or "",
                "created_new": False,
                "address_updated": False,
                "tax_status": "error",
                "w9_received": False,
                "name_for_1099_source": _safe_str(r.get("name_for_1099_source")) or _safe_str(r.get("Member")),
                "email": _safe_str(r.get("Email")),
                "notes": str(e),
            })

    res_df = pd.DataFrame(results)
    st.subheader("Bulk Upload Results")
    st.dataframe(res_df, use_container_width=True)

    # Summary counts
    st.markdown("#### Summary")
    st.write({
        "rows_total": int(len(res_df)),
        "ok": int((res_df["notes"] == "OK").sum()),
        "errors": int((res_df["notes"] != "OK").sum()),
        "new_agents_created": int(res_df["created_new"].sum()),
        "tax_saved": int((res_df["tax_status"] == "tax_saved").sum()),
        "no_tax_id": int((res_df["tax_status"] == "no_tax_id").sum()),
    })

    # Download results CSV
    out_csv = res_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download Results CSV",
        data=out_csv,
        file_name="1099_bulk_upload_results.csv",
        mime="text/csv",
        use_container_width=True,
    )

    st.success("Bulk upload completed.")
