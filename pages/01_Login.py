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

# --- Capture recovery tokens even if they arrive directly on /Login ---
st.components.v1.html(
    """
    <script>
      const h = window.location.hash;

      // Only run on Supabase recovery links
      if (h && h.includes("access_token")) {

        // Turn #a=b&c=d into ?a=b&c=d
        const qs = h.replace('#', '?');

        // Send full token set to /Login as real query params
        const url = "/Login" + qs;

        window.top.location.replace(url);
      }
    </script>
    """,
    height=0,
)

# -----------------------------
# RECOVERY TOKEN SESSION SETUP
# -----------------------------
params = st.query_params
is_recovery = params.get("type") == "recovery"
force_reset = st.session_state.get("force_password_reset", False)

st.info("DEBUG PARAMS")
st.json(dict(params))

# Try to establish a real session from the recovery link
if is_recovery:
    try:
        session = sb.auth.exchange_code_for_session(params)

        st.session_state["sb_access_token"]  = session.session.access_token
        st.session_state["sb_refresh_token"] = session.session.refresh_token

        st.success("SESSION RESTORED (exchange_code_for_session)")
        st.json({
            "user_id": session.user.id if session and session.user else None,
            "expires_at": session.session.expires_at if session and session.session else None,
        })

    except Exception as e:
        st.error(f"Could not exchange recovery code for session: {e}")


# -----------------------------
# DEBUG: SESSION CHECK (PRE-RESET)
# -----------------------------
st.markdown("### DEBUG: SESSION CHECK (PRE-RESET)")

try:
    dbg_session = sb.auth.get_session()
    st.json({
        "supabase_user": getattr(dbg_session.user, "id", None) if dbg_session else None,
        "expires_at": getattr(dbg_session, "expires_at", None) if dbg_session else None,
        "has_access_token_state": bool(st.session_state.get("sb_access_token")),
        "has_refresh_token_state": bool(st.session_state.get("sb_refresh_token")),
        "force_reset_flag": st.session_state.get("force_password_reset", False),
    })
except Exception as e:
    st.error(f"DEBUG session fetch error: {e}")

st.markdown("---")

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
                # 🔹 Ensure the recovery session is active
                access = st.session_state.get("sb_access_token")
                refresh = st.session_state.get("sb_refresh_token")

                if access:
                    sb.auth.set_session(access, refresh)

                # 🔹 Now Supabase has a valid session — update password
                sb.auth.update_user({"password": new_pw})

                st.session_state["force_password_reset"] = False
                st.success("Password updated successfully. Please sign in.")
                st.stop()

            except Exception as e:
                st.error(f"Could not update password: {e}")

    st.stop()

st.title("🔐 Login")
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
                    "redirect_to": "https://booking-management.streamlit.app/Login?type=recovery"
                },
            )

            st.success("Password reset email sent. Check your inbox.")

        except Exception as e:
            st.error(f"Could not send reset email: {e}")


