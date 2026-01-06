# -----------------------------
# 01_Login.py  — RESET FLOW STABLE
# -----------------------------
import streamlit as st
from supabase import create_client, Client

st.set_page_config(page_title="Login", page_icon="🔐", layout="centered")

SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_ANON_KEY = st.secrets["SUPABASE_ANON_KEY"]
sb: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

EMAIL_REDIRECT_URL = "https://booking-management.streamlit.app/Login?type=recovery"

# --- Normalize Supabase recovery redirects (supports BOTH flows) ---
st.components.v1.html(
    """
    <script>
      (function () {
        const h = window.location.hash;

        // If Supabase sent tokens in the hash, convert them to ?params
        if (h && h.includes("access_token")) {
          const q = new URLSearchParams(h.substring(1));
          const token   = q.get("access_token");
          const refresh = q.get("refresh_token") || "";

          const url =
            "/Login"
            + "?type=recovery"
            + "&access_token=" + encodeURIComponent(token)
            + "&refresh_token=" + encodeURIComponent(refresh);

          try { window.parent.location.replace(url); }
          catch (e) { window.location.replace(url); }
        }
      })();
    </script>
    """,
    height=0,
)

params = st.query_params
is_recovery = params.get("type") == "recovery"

# 1️⃣ HASH TOKEN FLOW (access_token + refresh_token)
if is_recovery and params.get("access_token"):
    try:
        sb.auth.set_session(
            params.get("access_token"),
            params.get("refresh_token") or ""
        )
        st.session_state["sb_access_token"]  = params["access_token"]
        st.session_state["sb_refresh_token"] = params.get("refresh_token") or ""

        st.success("Recovery session established (hash tokens).")
    except Exception as e:
        st.error(f"Token session restore failed: {e}")


# 2️⃣ PKCE CODE FLOW
elif is_recovery and params.get("code"):
    try:
        resp = sb.auth.exchange_code_for_session(params["code"])
        session = getattr(resp, "session", resp)

        st.session_state["sb_access_token"]  = session.access_token
        st.session_state["sb_refresh_token"] = session.refresh_token

        sb.auth.set_session(session.access_token, session.refresh_token)

        st.success("Recovery session established (PKCE).")
    except Exception as e:
        st.error(f"Code-exchange recovery failed: {e}")


# 2️⃣-bis  VERIFY-LINK TOKEN FLOW (THIS IS YOUR CASE)
elif is_recovery and params.get("token"):
    try:
        token = params.get("token")

        resp = sb.auth.exchange_code_for_session(token)
        session = getattr(resp, "session", resp)

        st.session_state["sb_access_token"]  = session.access_token
        st.session_state["sb_refresh_token"] = session.refresh_token

        sb.auth.set_session(session.access_token, session.refresh_token)

        st.success("Recovery session established (verify token).")
    except Exception as e:
        st.error(f"Token-hash recovery failed: {e}")


# 3️⃣ Show Reset UI only when a session exists

if is_recovery:
    st.subheader("Reset Your Password")

    new_pw  = st.text_input("New Password", type="password")
    new_pw2 = st.text_input("Confirm Password", type="password")

    if st.button("Update Password"):
        if new_pw != new_pw2:
            st.error("Passwords do not match.")
            st.stop()

        if len(new_pw) < 6:
            st.error("Password must be at least 6 characters.")
            st.stop()

        try:
            # 🔹 ALWAYS re-establish session before updating password
            access  = st.session_state.get("sb_access_token")
            refresh = st.session_state.get("sb_refresh_token")

            if not access:
                st.error("Recovery session missing — reload using the email link.")
                st.stop()

            sb.auth.set_session(access, refresh or "")

            # 🔹 Now Supabase has a valid session
            sb.auth.update_user({"password": new_pw})

            st.success("Password updated successfully. Please sign in.")
            st.stop()

        except Exception as e:
            st.error(f"Could not update password: {e}")

    st.stop()


# -----------------------------------------------------------
# NORMAL LOGIN UI
# -----------------------------------------------------------
st.title("🔐 Login")

mode = st.radio(
    "Choose an option",
    ["Sign In", "Create Account", "Forgot Password"],
    horizontal=True,
)

if mode == "Sign In":
    email = st.text_input("Email")
    pw = st.text_input("Password", type="password")

    if st.button("Sign In"):
        try:
            res = sb.auth.sign_in_with_password(
                {"email": email, "password": pw}
            )

            if res.user:
                # 🔹 persist session across pages
                st.session_state["sb_access_token"]  = res.session.access_token
                st.session_state["sb_refresh_token"] = res.session.refresh_token

                sb.auth.set_session(
                    res.session.access_token,
                    res.session.refresh_token
                )

                st.success("Signed in successfully.")
            else:
                st.error("Invalid login.")
        except Exception as e:
            st.error(f"Sign-in failed: {e}")

            # if res.user:
                # st.session_state["sb_session"] = {
                    # "access_token": res.session.access_token,
                    # "refresh_token": res.session.refresh_token,
                    # "email": res.user.email,
                    # "user_id": res.user.id,
                # }

                # st.success("Signed in successfully.")
            # else:
                # st.error("Invalid login.")

        # except Exception as e:
            # st.error(f"Sign-in failed: {e}")

# if mode == "Sign In":
    # email = st.text_input("Email")
    # pw = st.text_input("Password", type="password")

    # if st.button("Sign In"):
        # try:
            # res = sb.auth.sign_in_with_password({"email": email, "password": pw})
            # if res.user:
                # st.success("Signed in successfully.")
            # else:
                # st.error("Invalid login.")
        # except Exception as e:
            # st.error(f"Sign-in failed: {e}")

elif mode == "Create Account":
    email = st.text_input("Email")
    pw = st.text_input("Password", type="password")

    if st.button("Create Account"):
        try:
            sb.auth.sign_up(
                {
                    "email": email,
                    "password": pw,
                    "options": {"emailRedirectTo": EMAIL_REDIRECT_URL},
                }
            )
            st.success("Check your email to confirm your account.")
        except Exception as e:
            st.error(f"Account creation failed: {e}")

elif mode == "Forgot Password":
    email = st.text_input("Email")

    if st.button("Send Reset Link"):
        try:
            sb.auth.reset_password_email(
                email,
                options={"redirect_to": "https://booking-management.streamlit.app/00_Reset_Password"},
            )
            st.success("Password reset email sent.")
        except Exception as e:
            st.error(f"Could not send reset email: {e}")
