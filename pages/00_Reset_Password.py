## --- 00_Reset_Password.py (REST-based reset) ---

import streamlit as st
import requests
from supabase import create_client, Client

st.set_page_config(page_title="Reset Password", layout="centered")

SUPABASE_URL = st.secrets["SUPABASE_URL"].rstrip("/")
SUPABASE_ANON_KEY = st.secrets["SUPABASE_ANON_KEY"]

sb: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# --- Normalize Supabase hash → query params (recovery links) ---
st.components.v1.html(
    """
    <script>
      (function () {
        const h = window.location.hash;
        if (!h || !h.includes("access_token")) return;

        const q = new URLSearchParams(h.substring(1));

        const url =
          window.location.pathname
          + "?type=recovery"
          + "&access_token=" + encodeURIComponent(q.get("access_token"))
          + "&refresh_token=" + encodeURIComponent(q.get("refresh_token") || "");

        // 🔹 Force redirect at the TOP level (important in Streamlit)
        if (window.top) {
          window.top.location.replace(url);
        } else {
          window.location.replace(url);
        }
      })();
    </script>
    """,
    height=1,   # <— give the component a tiny render surface
)

params = st.query_params
is_recovery = params.get("type") == "recovery"
access_token = params.get("access_token")

# --- Guard: must come from a valid link ---
if not is_recovery or not access_token:
    st.warning("This page is only for password reset links. Please use the link from your email.")
    st.stop()

st.subheader("Reset Your Password")

pw1 = st.text_input("New Password", type="password")
pw2 = st.text_input("Confirm Password", type="password")

if st.button("Update Password"):
    if pw1 != pw2:
        st.error("Passwords do not match.")
        st.stop()
    if len(pw1) < 6:
        st.error("Password must be at least 6 characters.")
        st.stop()

    try:
        # 🔹 Call Supabase Auth REST API directly using the access token
        url = f"{SUPABASE_URL}/auth/v1/user"
        headers = {
            "apikey": SUPABASE_ANON_KEY,
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        payload = {"password": pw1}

        resp = requests.put(url, headers=headers, json=payload)

        if resp.status_code == 200:
            st.success("Password updated — you may now sign in.")
        else:
            # Show useful info, but not the whole response body
            st.error(
                f"Password reset failed (HTTP {resp.status_code}). "
                "The link may have expired or been used already."
            )

    except Exception as e:
        st.error(f"Password reset failed: {e}")

