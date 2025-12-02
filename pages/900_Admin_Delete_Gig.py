# pages/900_Admin_Delete_Gig.py
from __future__ import annotations
import streamlit as st
from supabase import create_client

st.set_page_config(page_title="Admin: Delete Gig", layout="wide")

# ==========================================
# Supabase Client
# ==========================================
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_SERVICE_KEY"]  # service role is required
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==========================================
# Admin Authentication (PRS standard)
# ==========================================
def role_is_admin(user_id: str) -> bool:
    data = sb.table("profiles").select("role").eq("user_id", user_id).single().execute().data
    return bool(data and data.get("role") == "admin")

user_id = st.session_state.get("user_id")

if not user_id:
    st.error("Please sign in.")
    st.stop()

if not role_is_admin(user_id):
    st.error("Admins only.")
    st.stop()

# ==========================================
# Page Header
# ==========================================
st.title("🗑️ Admin – Delete Gig Safely (No Orphans)")
st.warning(
    "This will permanently delete the gig and ALL related records.\n\n"
    "Use with extreme caution."
)

gig_id = st.text_input("Gig ID", placeholder="Paste gig UUID here")

CHILD_TABLES = [
    ("gig_deposits", "gig_id"),
    ("gig_payments", "gig_id"),
    ("gig_musicians", "gig_id"),
    ("gigs_private", "gig_id"),
    ("gigs_public", "gig_id"),
]

def fetch_children(gid: str):
    out = {}
    for table, col in CHILD_TABLES:
        res = sb.table(table).select("*").eq(col, gid).execute().data or []
        out[table] = res
    return out

# ==========================================
# Preview Children
# ==========================================
if st.button("🔎 Preview Children (Safe)"):
    if not gig_id:
        st.error("Enter a valid gig ID.")
    else:
        children = fetch_children(gig_id)
        st.subheader("Related Records")

        for table, rows in children.items():
            st.write(f"**{table}**: {len(rows)}")
            if rows:
                st.json(rows)

        gig = sb.table("gigs").select("*").eq("id", gig_id).execute().data
        st.subheader("Gig Record")
        if gig:
            st.json(gig)
        else:
            st.error("No gig found.")

# ==========================================
# Delete
# ==========================================
if st.button("🗑️ Delete Gig (Irreversible!)"):
    if not gig_id:
        st.error("Enter a valid gig ID.")
    else:
        with st.spinner("Deleting..."):
            log = []
            # children
            for table, col in CHILD_TABLES:
                resp = sb.table(table).delete().eq(col, gig_id).execute()
                log.append(f"{table}: {len(resp.data or [])} deleted")

            # gig
            resp = sb.table("gigs").delete().eq("id", gig_id).execute()
            log.append(f"gigs: {len(resp.data or [])} deleted")

        st.success("Deletion complete.")
        for line in log:
            st.write("• " + line)
