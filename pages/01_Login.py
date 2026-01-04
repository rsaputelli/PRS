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

# Session bootstrap
if "user" not in st.session_state:
    st.session_state["user"] = None

# =========================================
# PASSWORD RECOVERY MODE (reset link flow)
# =========================================
params = st.query_params
is_recovery = params.get("type") == "recovery"

if is_recovery:
    st.subheader("Reset Your Password")

    new_pw = st.text_input("New password", type="password")
    new_pw2 = st.text_input("Confirm new password", type="password")

    if st.button("Update Password"):
        if not new_pw or new_pw != new_pw2:
            st.error("Passwords do not match.")
        else:
            try:
                sb.auth.update_user({"password": new_pw})
                st.success("Password updated successfully. Please sign in.")
                st.info("You may now log in with your new password.")
                st.stop()
            except Exception as e:
                st.error(f"Password reset failed: {e}")

    st.stop()

# If already logged in
if st.session_state["user"]:
    st.success(f"Signed in as {st.session_state['user']['email']}")
    if st.button("Sign out"):
        st.session_state["user"] = None
        st.session_state.pop("sb_access_token", None)
        st.session_state.pop("sb_refresh_token", None)
        st.rerun()

else:
    mode = st.radio(
        "Authentication",
        ["Sign In", "Create Account", "Forgot Password"],
        horizontal=True,
    )

    # -------------------------
    # SIGN IN
    # -------------------------
    if mode == "Sign In":
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")

        if st.button("Sign In"):
            try:
                res = sb.auth.sign_in_with_password(
                    {"email": email, "password": password}
                )
                if res.user:
                    st.session_state["sb_access_token"] = res.session.access_token
                    st.session_state["sb_refresh_token"] = res.session.refresh_token
                    st.session_state["user"] = {"email": res.user.email}
                    st.success("Logged in successfully!")
                    st.rerun()
                else:
                    st.error("Invalid credentials.")
            except Exception as e:
                st.error(f"Login failed: {e}")

    # -------------------------
    # CREATE ACCOUNT
    # -------------------------
    elif mode == "Create Account":
        email = st.text_input("Email")
        password = st.text_input(
            "Password",
            type="password",
            help="Minimum 6 characters (Supabase default)",
        )

        if st.button("Create Account"):
            try:
                res = sb.auth.sign_up({"email": email, "password": password})

                if res.user:
                    st.success("Account created! Please check your email to confirm.")
                    # Optional auto-login after signup
                    if res.session:
                        st.session_state["sb_access_token"] = res.session.access_token
                        st.session_state["sb_refresh_token"] = res.session.refresh_token
                        st.session_state["user"] = {"email": res.user.email}
                        st.rerun()
                else:
                    st.error("Account could not be created.")
            except Exception as e:
                st.error(f"Signup failed: {e}")

    # -------------------------
    # FORGOT PASSWORD
    # -------------------------
    elif mode == "Forgot Password":
        email = st.text_input("Email")

        if st.button("Send Reset Link"):
            try:
                sb.auth.reset_password_email(email)
                st.success(
                    "Password reset email sent. Check your inbox (and spam folder)."
                )
            except Exception as e:
                st.error(f"Could not send reset email: {e}")
