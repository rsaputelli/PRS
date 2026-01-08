##----00_Player_Set_Password.py

import streamlit as st
from supabase import create_client
import re

st.set_page_config(page_title="Create / Update Password", layout="centered")

SUPABASE_URL = st.secrets["SUPABASE_URL"]
SERVICE_ROLE_KEY = st.secrets["SUPABASE_SERVICE_KEY"]  # required for admin ops
sb = create_client(SUPABASE_URL, SERVICE_ROLE_KEY)

st.title("Create / Update Your Password")

st.write(
    "Enter your email to set or update your password. "
    "Once saved, you can sign in from the Login page."
)

email = st.text_input("Email")

# Optional field — commented out for now
# phone_last4 = st.text_input("Last 4 digits of your phone (for identity verification)")

pw1 = st.text_input("New Password", type="password")
pw2 = st.text_input("Confirm Password", type="password")

def valid_email(e):
    return re.match(r"[^@]+@[^@]+\.[^@]+", e)

if st.button("Save Password"):
    if not valid_email(email):
        st.error("Please enter a valid email address.")
        st.stop()

    if pw1 != pw2:
        st.error("Passwords do not match.")
        st.stop()

    if len(pw1) < 6:
        st.error("Password must be at least 6 characters.")
        st.stop()

    try:
        # 🔎 Look up existing user
        users = sb.auth.admin.list_users(email=email)

        if not users or len(users.get("users", [])) == 0:
            st.error("No account found for that email.")
            st.stop()

        user = users["users"][0]

        # OPTIONAL — enable later if you want phone verification
        # stored_phone = user.get("phone") or ""
        # if phone_last4 and not stored_phone.endswith(phone_last4):
        #     st.error("Phone verification failed.")
        #     st.stop()

        # 🔐 Update the password
        sb.auth.admin.update_user_by_id(
            user["id"],
            {"password": pw1}
        )

        st.success("Password updated — you may now log in.")
        st.info("Go to the Login page and sign in with your new password.")

    except Exception as e:
        st.error(f"Password update failed: {e}")
