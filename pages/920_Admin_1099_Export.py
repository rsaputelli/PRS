# pages/920_Admin_1099_Export.py
from __future__ import annotations

import os
import streamlit as st
import pandas as pd
from datetime import date
from typing import Optional, Any, Dict

from supabase import create_client, Client
from auth_helper import require_admin
from lib.tax_crypto import get_fernet


# ------------------------------
# Secrets + Clients (PRS pattern)
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

st.title("Admin: 1099 Export Builder")

EXCLUDE_1099_PAYEE_IDS = {
    "81f4e9ef-5f7d-44cb-95e9-b9dcf8d53135",
}

# ------------------------------
# Helpers
# ------------------------------
PAYEE_TABLES = {
    "musician": "musicians",
    "agent": "agents",
    "sound_tech": "sound_techs",
}

def _svc_select_all(table: str) -> pd.DataFrame:
    resp = sb_svc.table(table).select("*").execute()
    raw = resp.model_dump()
    if raw.get("error"):
        raise Exception(str(raw["error"]))
    return pd.DataFrame(raw.get("data") or [])

def _svc_select_gig_payments(tax_year: int) -> pd.DataFrame:
    """
    Pull only paid payments in a tax year and eligible for 1099.
    """
    # Supabase filters: use >= and <= bounds for date
    start = f"{int(tax_year)}-01-01"
    end = f"{int(tax_year)}-12-31"

    resp = (
        sb_svc.table("gig_payments")
        .select("id,gig_id,kind,due_on,amount,paid_on,method,reference,payee_id,payee_name,role,fee_withheld,eligible_1099,net_amount")
        .gte("paid_on", start)
        .lte("paid_on", end)
        .eq("eligible_1099", True)
        .execute()
    )
    raw = resp.model_dump()
    if raw.get("error"):
        raise Exception(str(raw["error"]))
    df = pd.DataFrame(raw.get("data") or [])
    return df

def _norm_state(x: Any) -> str:
    return "" if x is None else str(x).strip().upper()

def _norm_zip(x: Any) -> str:
    if x is None:
        return ""
    s = str(x).strip()
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 9:
        return f"{digits[:5]}-{digits[5:9]}"
    return digits[:5]

def _coalesce(*vals: Any) -> Any:
    for v in vals:
        if v not in (None, ""):
            return v
    return None

def _decrypt_tin(ciphertext: Any, fernet) -> str:
    if ciphertext in (None, ""):
        return ""
    try:
        token = str(ciphertext).encode("utf-8")
        plain = fernet.decrypt(token)
        return plain.decode("utf-8")
    except Exception:
        return ""

def _build_payee_master(selected_types: list[str], active_only: bool, include_full_tin: bool) -> pd.DataFrame:
    payee_tax = _svc_select_all("payee_tax_info")
    if payee_tax.empty:
        payee_tax = pd.DataFrame(columns=[
            "payee_type", "payee_id", "tin_last4", "tin_ciphertext", "w9_received"
        ])

    fernet = None
    if include_full_tin:
        tax_key = _get_secret("TAX_ENCRYPTION_KEY", required=True)
        fernet = get_fernet(tax_key)

    rows: list[Dict[str, Any]] = []

    for payee_type in selected_types:
        table = PAYEE_TABLES[payee_type]
        df = _svc_select_all(table)
        if df.empty:
            continue

        if active_only and "active" in df.columns:
            df = df[df["active"] == True]  # noqa: E712

        t = payee_tax[payee_tax["payee_type"] == payee_type].copy()
        merged = df.merge(t, how="left", left_on="id", right_on="payee_id", suffixes=("", "_tax"))

        for _, r in merged.iterrows():
            d = r.to_dict()

            first = d.get("first_name") or ""
            middle = d.get("middle_name") or ""
            last = d.get("last_name") or ""
            display_name = d.get("display_name") or d.get("stage_name") or ""
            name_for_1099 = _coalesce(d.get("name_for_1099"), display_name, f"{first} {last}".strip(), "—")

            company = d.get("company") or ""

            address1 = d.get("address") or ""
            address2 = d.get("address2") or ""
            city = d.get("city") or ""
            state = _norm_state(d.get("state"))
            zip_code = _norm_zip(d.get("zip"))

            w9_received = bool(d.get("w9_received", False))
            tin_last4 = d.get("tin_last4") or ""
            tin_on_file = bool(d.get("tin_ciphertext"))

            full_tin = ""
            if include_full_tin and fernet is not None:
                full_tin = _decrypt_tin(d.get("tin_ciphertext"), fernet)

            rows.append({
                "payee_type": payee_type,
                "payee_id": d.get("id"),

                "name_for_1099": name_for_1099,
                "company": company,

                "first_name": first,
                "middle_name": middle,
                "last_name": last,

                "email": d.get("email") or "",
                "phone": d.get("phone") or "",

                "address": address1,
                "address2": address2,
                "city": city,
                "state": state,
                "zip": zip_code,

                "active": bool(d.get("active", True)),

                "w9_received": w9_received,
                "tin_on_file": tin_on_file,
                "tin_last4": tin_last4,
                "tin_full": full_tin,  # only if include_full_tin
            })

    out = pd.DataFrame(rows)
    if out.empty:
        return out

    out = out.sort_values(["payee_type", "name_for_1099"]).reset_index(drop=True)
    return out


def _build_nec_totals(gp: pd.DataFrame) -> pd.DataFrame:
    """
    Build NEC totals from gig_payments:
      - group by kind + payee_id
      - sum net_amount
    """
    if gp.empty:
        return pd.DataFrame(columns=["payee_type", "payee_id", "nec_amount"])

    # Normalize numeric columns
    gp["net_amount"] = pd.to_numeric(gp["net_amount"], errors="coerce").fillna(0.0)

    totals = (
        gp.groupby(["kind", "payee_id"], as_index=False)["net_amount"]
        .sum()
        .rename(columns={"kind": "payee_type", "net_amount": "nec_amount"})
    )
    totals["nec_amount"] = totals["nec_amount"].round(2)
    return totals.sort_values(["payee_type", "nec_amount"], ascending=[True, False]).reset_index(drop=True)


# ------------------------------
# Controls
# ------------------------------
col1, col2, col3 = st.columns(3)
with col1:
    default_year = date.today().year - 1
    tax_year = st.number_input("1099 Tax Year", min_value=2000, max_value=2100, value=default_year, step=1)
with col2:
    active_only = st.checkbox("Active only", value=True)
with col3:
    include_full_tin = st.checkbox("Include FULL TIN in export (sensitive)", value=False)

selected_types = st.multiselect(
    "Include payee types",
    options=list(PAYEE_TABLES.keys()),
    default=["musician", "agent", "sound_tech"],
)

st.markdown("---")
st.markdown("## 1) Build Payee Master + NEC Totals")

build_all = st.button("Build 1099 Exports", type="primary", use_container_width=True)

if build_all:
    try:
        # Payees
        payee_df = _build_payee_master(selected_types, active_only, include_full_tin)
        if payee_df.empty:
            st.info("No payees found for selected filters.")
            st.stop()

        # Payments totals (gig_payments)
        gp = _svc_select_gig_payments(int(tax_year))
        totals_df = _build_nec_totals(gp)

        # Merge totals into payees (left join)
        merged = payee_df.merge(
            totals_df,
            how="left",
            left_on=["payee_type", "payee_id"],
            right_on=["payee_type", "payee_id"],
        )
        merged["nec_amount"] = pd.to_numeric(merged["nec_amount"], errors="coerce").fillna(0.0).round(2)

        st.session_state["merged_1099"] = merged.copy()

        # Filter: only those with NEC amount > 0 (toggle)
        st.markdown("### Filters")
        only_with_payments = st.checkbox("Show only payees with NEC payments > 0", value=True)
        if only_with_payments:
            merged = merged[merged["nec_amount"] > 0].copy()

        st.markdown("### Preview: Combined (Payee + NEC totals)")
        st.dataframe(merged, use_container_width=True, height=480)

        st.markdown("### Quality Checks")
        st.write({
            "payees_in_master": int(len(payee_df)),
            "payments_rows_in_year": int(len(gp)),
            "payees_with_payments": int((merged["nec_amount"] > 0).sum()),
            "total_nec_amount": float(merged["nec_amount"].sum()),
            "missing_tin_on_file": int((merged["tin_on_file"] == False).sum()),  # noqa: E712
            "w9_not_received": int((merged["w9_received"] == False).sum()),  # noqa: E712
            "missing_address": int((merged["address"].fillna("").str.strip() == "").sum()),
            "missing_city": int((merged["city"].fillna("").str.strip() == "").sum()),
            "missing_state": int((merged["state"].fillna("").str.strip() == "").sum()),
            "missing_zip": int((merged["zip"].fillna("").str.strip() == "").sum()),
        })

        # Downloads
        master_name = f"PRS_1099_Payee_Master_{tax_year}.csv"
        totals_name = f"PRS_1099_NEC_Totals_{tax_year}.csv"
        combined_name = f"PRS_1099_Combined_Upload_{tax_year}.csv"

        st.download_button(
            "Download Payee Master CSV",
            data=payee_df.to_csv(index=False).encode("utf-8"),
            file_name=master_name,
            mime="text/csv",
            use_container_width=True,
        )

        st.download_button(
            "Download NEC Totals CSV",
            data=totals_df.to_csv(index=False).encode("utf-8"),
            file_name=totals_name,
            mime="text/csv",
            use_container_width=True,
        )

        st.download_button(
            "Download Combined Upload CSV",
            data=merged.to_csv(index=False).encode("utf-8"),
            file_name=combined_name,
            mime="text/csv",
            use_container_width=True,
        )

        st.success("1099 exports built successfully.")

    except Exception as e:
        st.error(f"Export failed: {e}")


# Track1099 export (uses merged from session_state)
st.markdown("---")
st.markdown("## 2) Track1099 (Avalara) 1099-NEC Upload CSV")

def _infer_fed_id_type(tin: str) -> str:
    if not tin:
        return ""
    s = str(tin).strip()
    if "-" in s:
        first = s.split("-")[0]
        if len(first) == 2:
            return "1"  # EIN
        if len(first) == 3:
            return "2"  # SSN
    return ""

merged_ss = st.session_state.get("merged_1099")

if merged_ss is None:
    st.info("Build the 1099 exports above first, then you can generate the Track1099 upload CSV.")
else:
    if not include_full_tin:
        st.warning("Enable 'Include FULL TIN in export (sensitive)' above to generate the Track1099 upload CSV.")
    else:
        build_t1099 = st.button("Build Track1099 1099-NEC CSV", use_container_width=True)
        apply_600 = st.checkbox("Apply $600 1099-NEC threshold", value=True)
        threshold = 600.00

        if build_t1099:

            # Always pull from session state (safer than locals)
            df = st.session_state["merged_1099"].copy()

            # Only payees with compensation > 0
            df = df[df["nec_amount"] > 0].copy()

            # Apply $600 rule
            if apply_600:
                df = df[df["nec_amount"] >= threshold].copy()

            # Require TIN on file
            df = df[df["tin_on_file"] == True].copy()  # noqa: E712

            df = df[~df["payee_id"].astype(str).isin(EXCLUDE_1099_PAYEE_IDS)].copy()


            if df.empty:
                st.warning("No eligible payees after applying filters.")
                st.stop()

            tin_full = df["tin_full"] if "tin_full" in df.columns else pd.Series([""] * len(df), index=df.index)

            out = pd.DataFrame({
                "Reference ID (Optional)": df["payee_id"].astype(str),
                "Recipient's Name": df["name_for_1099"].fillna(""),
                "Recipient's Federal ID No.": tin_full.fillna(""),
                "Federal ID type (1=EIN, 2=SSN, 3=ITIN, 4=ATIN)": tin_full.fillna("").apply(_infer_fed_id_type),
                "Recipient's Second Name (optional)": df["company"].fillna(""),
                "Street Address": df["address"].fillna(""),
                "Street Address Line 2": df["address2"].fillna(""),
                "City": df["city"].fillna(""),
                "State (2 letters)": df["state"].fillna(""),
                "Zip": df["zip"].fillna(""),
                "Recipient's Email": df["email"].fillna(""),
                "Acc't No. (optional)": "",
                "Office Code (optional)": "",
                "Recipient Non-(US or Canadian) Province": "",
                "Country Code": "",
                "Box 1 Nonemployee compensation": df["nec_amount"].round(2),

                "Box 2 (blank=false, 1=true) Payer made direct sales of $5,000 or more of consumer products to a buyer (recipient) for resale": "",
                "Box 3 Excess golden parachute payments": "",
                "Box 4 Federal income tax withheld": "",
                "Box 5 State tax withheld": "",
                "Box 6 State": "",
                "Box 6 Payer's State no.": "",
                "Box 7 State Income": "",
                "Second TIN Notice (1=true, else blank)": "",
                "Box 5b Local Tax Withheld": "",
                "Box 6b Locality": "",
                "Box 6b Locality no.": "",
                "Box 7b Local Income": "",
            })

            st.subheader("Track1099 Preview")
            st.dataframe(out.head(50), use_container_width=True)

            st.download_button(
                "Download Track1099 1099-NEC CSV",
                data=out.to_csv(index=False).encode("utf-8"),
                file_name=f"PRS_Track1099_1099-NEC_{tax_year}.csv",
                mime="text/csv",
                use_container_width=True,
            )

            st.success("Track1099 upload file generated.")

st.caption(
    "Notes: NEC totals are based on gig_payments.paid_on within the tax year, "
    "eligible_1099 = true, and net_amount (amount - fee_withheld)."
)
