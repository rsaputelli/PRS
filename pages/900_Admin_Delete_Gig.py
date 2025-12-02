# pages/900_Admin_Delete_Gig.py
from __future__ import annotations
import streamlit as st
from supabase import create_client

st.set_page_config(page_title="Admin: Delete Gig", layout="wide")

# ===============================================================
# 🔐 ACCESS CONTROL — LOCKED TO RAY ONLY
# ===============================================================
ALLOWED_EMAILS = {
    "ray@lutinemanagement.com",
    "ray.saputelli@lutinemanagement.com",
	"rjs2119@gmail.com",
	"prsbandinfo@gmail.com",
}

user = st.session_state.get("user")

if not user or "email" not in user:
    st.error("You must be logged in to access this page.")
    st.stop()

if user["email"].lower() not in ALLOWED_EMAILS:
    st.error("You do not have permission to view this page.")
    st.stop()

# ===============================================================
# Supabase Client (Service Role — server-side only)
# ===============================================================
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_SERVICE_ROLE_KEY"]
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

# ===============================================================
# Page Header
# ===============================================================
st.title("🗑️ Admin – Delete Gig Safely (No Orphans)")
st.warning(
    "This tool **permanently deletes** gigs and all related records.\n\n"
    "**Use with extreme caution.**"
)

# ===============================================================
# Input
# ===============================================================
gig_id = st.text_input("Gig ID", placeholder="e.g., 415645ad-a89d-4bde-8b55-5ab5292041d1")

# All dependent tables
CHILD_TABLES = [
    ("gig_deposits", "gig_id"),
    ("gig_payments", "gig_id"),
    ("gig_musicians", "gig_id"),
    ("gigs_private", "gig_id"),
    ("gigs_public", "gig_id"),
]

# ===============================================================
# Helper: Fetch children
# ===============================================================
def fetch_children(gig_id: str):
    results = {}
    for table, column in CHILD_TABLES:
        res = sb.table(table).select("*").eq(column, gig_id).execute()
        results[table] = res.data or []
    return results

# ===============================================================
# PREVIEW
# ===============================================================
if st.button("🔎 Preview Children (Safe)"):
    if not gig_id:
        st.error("Enter a Gig ID first.")
    else:
        st.subheader("Child Records Found:")
        children = fetch_children(gig_id)
        empty = True

        for table, rows in children.items():
            if rows:
                empty = False
                st.markdown(f"**{table}: {len(rows)} rows**")
                st.json(rows)

        gig_res = sb.table("gigs").select("*").eq("id", gig_id).execute()

        st.subheader("Gig Record:")
        if gig_res.data:
            st.json(gig_res.data)
        else:
            st.error("No gig found with that ID.")

        if empty and not gig_res.data:
            st.info("No records found in any table.")

# ===============================================================
# DELETE
# ===============================================================
if st.button("🗑️ Delete Gig (Irreversible!)"):
    if not gig_id:
        st.error("Enter a Gig ID first.")
    else:
        with st.spinner("Deleting…"):
            log = []

            # Delete children first
            for table, column in CHILD_TABLES:
                resp = sb.table(table).delete().eq(column, gig_id).execute()
                deleted = len(resp.data or [])
                log.append(f"{table}: deleted {deleted}")

            # Delete main gig
            resp = sb.table("gigs").delete().eq("id", gig_id).execute()
            deleted_gig = len(resp.data or [])
            log.append(f"gigs: deleted {deleted_gig}")

        st.success("Gig deletion complete.")
        st.subheader("Deletion Log")
        for entry in log:
            st.write("• " + entry)

        st.info("✔ All related rows removed — no orphans remain.")
