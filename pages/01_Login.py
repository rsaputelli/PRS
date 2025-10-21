# pages/01_Login.py
import streamlit as st
from supabase import create_client, Client
import os

def _get_secret(name, default=None):
    if hasattr(st, "secrets") and name in st.secrets:
        return st.secrets[name]
    return os.getenv(name, default)

SUPABASE_URL = _get_secret("SUPABASE_URL")
SUPABASE_ANON_KEY = _get_secret("SUPABASE_ANON_KEY")
sb: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

st.title("Sign In")

if "user" not in st.session_state:
    st.session_state["user"] = None

if st.session_state["user"]:
    st.success(f"Signed in as {st.session_state['user']['email']}")
    if st.button("Sign out"):
        st.session_state["user"] = None
        st.experimental_rerun()
else:
    email = st.text_input("Email")
    password = st.text_input("Password", type="password")
    if st.button("Sign In"):
        try:
            res = sb.auth.sign_in_with_password({"email": email, "password": password})
            if res.user:
                st.session_state["user"] = {"email": res.user.email}
                st.success("Logged in successfully!")
                st.experimental_rerun()
            else:
                st.error("Invalid credentials.")
        except Exception as e:
            st.error(f"Login failed: {e}")
