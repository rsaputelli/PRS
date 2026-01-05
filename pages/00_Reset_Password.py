# pages/00_Reset_Password.py
import streamlit as st
from supabase import create_client, Client

st.set_page_config(page_title="Reset Password", layout="centered")

SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_ANON_KEY"]
sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

params = st.query_params


# ------------------------------------------------------
# 1) Convert Supabase #hash tokens → query params
# ------------------------------------------------------
st.components.v1.html(
    """
    <script>
      (function () {
        const h = window.location.hash;

        // Supabase sometimes sends tokens in the hash
        if (h && h.includes("access_token")) {
          const q = new URLSearchParams(h.substring(1));

          const url =
            window.location.pathname
            + "?type=recovery"
            + "&access_token=" + encodeURIComponent(q.get("access_token"))
            + "&refresh_token=" + encodeURIComponent(q.get("refresh_token") || "");

          window.location.replace(url);
        }
      })();
    </script>
    """,
    height=0,
)


# ------------------------------------------------------
# 2) Restore session from tokens OR PKCE code
# ------------------------------------------------------
if "reset_session_ready" not in st.session_state:
    st.session_state["reset_session_ready"] = False


def establish_session_from_tokens():
    """Attach Supabase session if tokens exist."""
    try:
        access = params.get("access_token")
        refresh = params.get("refresh_token") or ""

        if access:
            sb.auth.set_session(access, refresh)
            st.session_state["reset_session_ready"] = True
    except Exception as e:
        st.error(f"Token session restore failed: {e}")


def establish_session_from_code():
    """Handle PKCE ?code= recovery links."""
    try:
            resp = sb.auth.exchange_code_for_session(params["code"])
            session = getattr(resp, "session", resp)

            sb.auth.set_session(session.access_token, session.refresh_token)
            st.session_state["reset_session_ready"] = True
    except Exception as e:
        st.error(f"Code-exchange session failed: {e}")


# HASH TOKEN FLOW
if params.get("type") == "recovery" and params.get("access_token"):
    establish_session_from_tokens()

# PKCE CODE FLOW
elif params.get("type") == "recovery" and params.get("code"):
    establish_session_from_code()


# ------------------------------------------------------
# 3) Require valid recovery session BEFORE form
# ------------------------------------------------------
if not st.session_state.get("reset_session_ready"):
    st.warning("This reset link is not active. Please reopen the link from your email.")
    st.stop()


# ------------------------------------------------------
# 4) Password Reset Form
# ------------------------------------------------------
st.subheader("Reset Your Password")

pw1 = st.text_input("New Password", type="password")
pw2 = st.text_input("Confirm Password", type="password")

if st.button("Update Password"):
    if pw1 != pw2:
        st.error("Passwords do not match.")
    elif len(pw1) < 6:
        st.error("Password must be at least 6 characters.")
    else:
        try:
            sb.auth.update_user({"password": pw1})
            st.success("Password updated successfully — you may now sign in.")
        except Exception as e:
            st.error(f"Password reset failed: {e}")
