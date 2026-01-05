import streamlit as st
from supabase import create_client, Client

# -----------------------------
# CONFIG
# -----------------------------
st.set_page_config(page_title="Login", page_icon="🔐", layout="centered")

SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_ANON_KEY = st.secrets["SUPABASE_ANON_KEY"]

sb: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)


# 👇 IMPORTANT — this must match the Login page URL
EMAIL_REDIRECT_URL = "https://booking-management.streamlit.app/Login"


# -----------------------------
# RECOVERY TOKEN SESSION SETUP
# -----------------------------
params = st.query_params
is_recovery = params.get("type") == "recovery"
force_reset = st.session_state.get("force_password_reset", False)

if is_recovery:
    access_token = params.get("access_token")
    refresh_token = params.get("refresh_token")

    if access_token and refresh_token:
        try:
            sb.auth.set_session(access_token, refresh_token)
            st.session_state["sb_access_token"] = access_token
            st.session_state["sb_refresh_token"] = refresh_token
        except Exception as e:
            st.error(f"Could not establish recovery session: {e}")


st.title("🔐 Login")


# -----------------------------
# PASSWORD RESET MODE
# -----------------------------
if is_recovery or force_reset:
    st.subheader("Reset Your Password")

    new_pw = st.text_input("New Password", type="password")
    new_pw_confirm = st.text_input("Confirm Password", type="password")

    if st.button("Update Password"):
        if new_pw != new_pw_confirm:
            st.error("Passwords do not match.")
        elif len(new_pw) < 6:
            st.error("Password must be at least 6 characters.")
        else:
            try:
                sb.auth.update_user({"password": new_pw})

                st.session_state["force_password_reset"] = False
                st.success("Password updated successfully. Please sign in.")
                st.stop()

            except Exception as e:
                st.error(f"Could not update password: {e}")

    st.stop()


# -----------------------------
# MODE SELECTOR
# -----------------------------
mode = st.radio(
    "Choose an option",
    ["Sign In", "Create Account", "Forgot Password"],
    horizontal=True,
)


# -----------------------------
# SIGN IN
# -----------------------------
if mode == "Sign In":
    email = st.text_input("Email")
    password = st.text_input("Password", type="password")

    if st.button("Sign In"):
        try:
            res = sb.auth.sign_in_with_password(
                {"email": email, "password": password}
            )

            if res.user is None:
                st.error("Invalid email or password.")
            else:
                st.success("Signed in successfully.")
        except Exception as e:
            st.error(f"Sign-in failed: {e}")


# -----------------------------
# CREATE ACCOUNT
# -----------------------------
elif mode == "Create Account":
    email = st.text_input("Email")
    password = st.text_input("Password", type="password")

    if st.button("Create Account"):
        try:
            sb.auth.sign_up(
                {
                    "email": email,
                    "password": password,
                    "options": {
                        "emailRedirectTo": EMAIL_REDIRECT_URL,
                    },
                }
            )

            st.success(
                "Account created. Please check your email to confirm and continue."
            )

        except Exception as e:
            st.error(f"Account creation failed: {e}")


# -----------------------------
# FORGOT PASSWORD
# -----------------------------
elif mode == "Forgot Password":
    email = st.text_input("Email")

    if st.button("Send Reset Link"):
        try:
            sb.auth.reset_password_email(
                email,
                options={
                    redirect_to: EMAIL_REDIRECT_URL,
                },
            )

            st.success(
                "Password reset email sent. Check your inbox (and spam folder)."
            )

        except Exception as e:
            st.error(f"Could not send reset email: {e}")
