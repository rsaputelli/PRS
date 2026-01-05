# -----------------------------
# 01_Login.py  — RESET FLOW STABLE
# -----------------------------
import streamlit as st
from supabase import create_client, Client

st.set_page_config(page_title="Login", page_icon="🔐", layout="centered")

SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_ANON_KEY = st.secrets["SUPABASE_ANON_KEY"]
sb: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

EMAIL_REDIRECT_URL = "https://booking-management.streamlit.app/Login"

# -----------------------------------------------------------
# 1) CAPTURE TOKENS DIRECTLY FROM HASH (NO REDIRECT)
# -----------------------------------------------------------
st.components.v1.html(
    """
    <script>
      const h = window.location.hash;

      // If Supabase sent recovery tokens in the hash…
      if (h && h.includes("access_token")) {
        const q = new URLSearchParams(h.substring(1));

        const token   = q.get("access_token");
        const refresh = q.get("refresh_token") || "";
        const ttype   = q.get("type") || "recovery";

        // Write them to browser storage so Streamlit can read them
        sessionStorage.setItem("sb_recovery_access", token || "");
        sessionStorage.setItem("sb_recovery_refresh", refresh || "");
        sessionStorage.setItem("sb_recovery_type",   ttype || "recovery");

        // Clean the URL (remove hash) without reload
        history.replaceState(null, "", window.location.pathname);
      }
    </script>
    """,
    height=0,
)

# Read values that JS just stored
stored_access  = st.experimental_get_query_params().get("_ignore", [None])[0]  # noop to trigger rerun
access_token   = st.session_state.get("sb_recovery_access")
refresh_token  = st.session_state.get("sb_recovery_refresh")
recovery_type  = st.session_state.get("sb_recovery_type")


# Bridge from sessionStorage → session_state
if "sb_recovery_access" not in st.session_state:
    st.session_state["sb_recovery_access"]  = None
    st.session_state["sb_recovery_refresh"] = None
    st.session_state["sb_recovery_type"]    = None

st.components.v1.html(
    """
    <script>
      // Push sessionStorage values into Streamlit session_state
      const a = sessionStorage.getItem("sb_recovery_access");
      const r = sessionStorage.getItem("sb_recovery_refresh");
      const t = sessionStorage.getItem("sb_recovery_type");

      if (a) {
        window.parent.postMessage(
          {type: "sb_recovery_tokens", access: a, refresh: r, rtype: t},
          "*"
        );
      }
    </script>
    """,
    height=0,
)

msg = st.experimental_get_query_params()  # keep rerun stable


# -----------------------------------------------------------
# 2) RECEIVE TOKENS & CREATE SESSION
# -----------------------------------------------------------
if "sb_recovery_ready" not in st.session_state:
    st.session_state["sb_recovery_ready"] = False


def _try_establish_session():
    try:
        a = st.session_state["sb_recovery_access"]
        r = st.session_state["sb_recovery_refresh"]

        if a:
            sb.auth.set_session(a, r or "")
            st.session_state["sb_recovery_ready"] = True
    except Exception as e:
        st.error(f"Could not establish recovery session: {e}")

# -----------------------------------------------------------
# 3) PASSWORD RESET UI (ONLY WHEN SESSION EXISTS)
# -----------------------------------------------------------
is_recovery = st.session_state.get("sb_recovery_ready", False)

if is_recovery:
    st.subheader("Reset Your Password")

    new_pw = st.text_input("New Password", type="password")
    new_pw2 = st.text_input("Confirm Password", type="password")

    if st.button("Update Password"):
        if new_pw != new_pw2:
            st.error("Passwords do not match.")
        elif len(new_pw) < 6:
            st.error("Password must be at least 6 characters.")
        else:
            try:
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
            res = sb.auth.sign_in_with_password({"email": email, "password": pw})
            if res.user:
                st.success("Signed in successfully.")
            else:
                st.error("Invalid login.")
        except Exception as e:
            st.error(f"Sign-in failed: {e}")

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
                options={"redirect_to": EMAIL_REDIRECT_URL},
            )
            st.success("Password reset email sent.")
        except Exception as e:
            st.error(f"Could not send reset email: {e}")
