# -----------------------------
# 01_Login.py
# -----------------------------
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

# --- Convert Supabase password-reset hash → real query params ---
html = """
<script>
const h = window.location.hash;

// Only run if Supabase sent tokens in the hash
if (h && h.includes("access_token")) {
  const q = new URLSearchParams(h.substring(1));

  const token   = q.get("access_token");
  const refresh = q.get("refresh_token") || "";

  if (token) {
    const url =
      "/Login"
      + "?type=recovery"
      + "&access_token=" + encodeURIComponent(token)
      + "&refresh_token=" + encodeURIComponent(refresh);

    // Hard redirect BEFORE Streamlit renders anything
    try {
      window.parent.location.replace(url);
    } catch (e) {
      window.location.replace(url);
    }
  }
}
</script>
"""

st.components.v1.html(html, height=0)
st.stop()  # ⬅️ IMPORTANT — do not render page until redirect completes

# -----------------------------
# RECOVERY TOKEN SESSION SETUP
# -----------------------------
params = st.query_params
is_recovery = params.get("type") == "recovery"
force_reset = st.session_state.get("force_password_reset", False)

st.info("DEBUG PARAMS")
st.json(dict(params))

if is_recovery:
    access_token  = params.get("access_token")
    refresh_token = params.get("refresh_token")

    if access_token:
        try:
            sb.auth.set_session(access_token, refresh_token or "")

            st.session_state["sb_access_token"]  = access_token
            st.session_state["sb_refresh_token"] = refresh_token or ""

            session = sb.auth.get_session()

            st.success("SESSION RESTORED")
            st.json({
                "user_id": getattr(session.user, "id", None) if session else None,
                "expires_at": getattr(session, "expires_at", None) if session else None,
            })

        except Exception as e:
            st.error(f"Could not establish recovery session: {e}")
            
# --- PKCE fallback: some Supabase projects send ?code= instead of tokens ---
if is_recovery and not st.session_state.get("sb_access_token"):

    code = params.get("code")
    if code:
        try:
            resp = sb.auth.exchange_code_for_session(code)

            session_obj = getattr(resp, "session", resp)

            st.session_state["sb_access_token"]  = session_obj.access_token
            st.session_state["sb_refresh_token"] = session_obj.refresh_token

            st.success("SESSION RESTORED (via code exchange)")
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

# --- Guard: Do NOT show reset UI until session is ready ---
session_ready = (
    st.session_state.get("sb_access_token")
    or params.get("access_token")
    or (sb.auth.get_session() and sb.auth.get_session().user)
)

if is_recovery and not session_ready:
    st.info("Restoring secure reset session… please wait.")
    st.stop()

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


