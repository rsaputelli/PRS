# pages/900_Admin_Delete_Gig.py

from __future__ import annotations
import streamlit as st
from supabase import create_client

st.set_page_config(page_title="Admin – Delete Gig", layout="wide")

# ---------------------------------------------------------
# Supabase Client (service key for privileged deletes)
# ---------------------------------------------------------
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_SERVICE_KEY"]
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

# ---------------------------------------------------------
# AUTH — MATCHES PRS LOGIN MODEL
# ---------------------------------------------------------
ALLOWED_EMAILS = {
    "ray@lutinemanagement.com",
    "ray.saputelli@lutinemanagement.com",
    "prsbandinfo@gmail.com",
    "rjs2119@gmail.com",
}

user = st.session_state.get("user")
if not user:
    st.error("Please sign in.")
    st.stop()

email = user.get("email", "").lower()
if email not in ALLOWED_EMAILS:
    st.error("You do not have permission to access this page.")
    st.stop()

# ---------------------------------------------------------
# UI
# ---------------------------------------------------------
st.title("🗑️ Admin – Delete Gig Safely (No Orphans)")
st.warning("This action is permanent. Use with caution.")

gig_id = st.text_input("Gig ID", placeholder="Paste the gig UUID")

# ---------------------------------------------------------
# CHILD TABLES (EXACT PRS SCHEMA) — DO NOT ADD 'gigs' HERE
# ---------------------------------------------------------
CHILD_TABLES = [
    ("gig_deposits", "gig_id"),
    ("gig_payments", "gig_id"),
    ("gig_musicians", "gig_id"),
    ("gigs_private", "gig_id"),
]

# ---------------------------------------------------------
# PREVIEW FUNCTION
# ---------------------------------------------------------
def fetch_children(gid):
    out = {}
    for table, col in CHILD_TABLES:
        res = sb.table(table).select("*").eq(col, gid).execute().data or []
        out[table] = res
    return out

# ---------------------------------------------------------
# PREVIEW CHILDREN + MAIN GIG
# ---------------------------------------------------------
if st.button("🔎 Preview Children"):
    if not gig_id:
        st.error("Enter a valid gig ID.")
    else:
        children = fetch_children(gig_id)
        st.subheader("Related Records")

        for table, rows in children.items():
            st.write(f"**{table}**: {len(rows)}")
            if rows:
                st.json(rows)

        # Preview parent gig
        gig = sb.table("gigs").select("*").eq("id", gig_id).execute().data
        st.subheader("Gig Record")
        if gig:
            st.json(gig)
        else:
            st.error("No gig found.")

# ---------------------------------------------------------
# DELETE ACTION
# ---------------------------------------------------------
if st.button("🗑️ Delete Gig (Irreversible!)"):
    if not gig_id:
        st.error("Enter a valid gig ID.")
    else:
        with st.spinner("Deleting…"):
            log = []

            # 1. Delete children
            for table, col in CHILD_TABLES:
                resp = sb.table(table).delete().eq(col, gig_id).execute()
                log.append(f"{table}: {len(resp.data or [])} deleted")

            # 2. Delete main gig (parent)
            resp = sb.table("gigs").delete().eq("id", gig_id).execute()
            log.append(f"gigs: {len(resp.data or [])} deleted")

        st.success("Deletion complete.")
        for line in log:
            st.write("• " + line)
