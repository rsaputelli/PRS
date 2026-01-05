# --------------------------------------------------
# 00_Reset_Password.py  — resilient recovery handler
# --------------------------------------------------

import streamlit as st
from supabase import create_client, Client

st.set_page_config(page_title="Reset Password", layout="centered")

sb: Client = create_client(
    st.secrets["SUPABASE_URL"],
    st.secrets["SUPABASE_ANON_KEY"]
)

raw_params = st.query_params


# --------------------------------------------------
# Normalize Streamlit query params (lists → strings)
# --------------------------------------------------
def qp(key):
    v = raw_params.get(key)
    if isinstance(v, list):
        return v[0]
    return v

params = {k: qp(k) for k in raw_params}


# --------------------------------------------------
# Robust hash→query converter (runs BEFORE UI load)
# --------------------------------------------------
st.components.v1.html("""
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

  if (!window.location.search.includes("access_token"))
    window.location.replace(url);
})();
</script>
""", height=0)


# Track whether session attached successfully
st.session_state.setdefault("reset_session_ready", False)

is_recovery = params.get("type") == "recovery"


# --------------------------------------------------
# Token session flow  (?access_token=…)
# --------------------------------------------------
if is_recovery and params.get("access_token"):
    try:
        sb.auth.set_session(
            params["access_token"],
            params.get("refresh_token") or ""
        )
        st.session_state["reset_session_ready"] = True
    except Exception as e:
        st.error(f"Could not attach recovery session: {e}")


# --------------------------------------------------
# PKCE ?code=… flow
# --------------------------------------------------
elif is_recovery and params.get("code"):
    try:
        resp = sb.auth.exchange_code_for_session(params["code"])
        session = getattr(resp, "session", resp)
        sb.auth.set_session(session.access_token, session.refresh_token)
        st.session_state["reset_session_ready"] = True
    except Exception as e:
        st.error(f"PKCE recovery failed: {e}")


# --------------------------------------------------
# Require valid session BEFORE UI
# --------------------------------------------------
if not st.session_state["reset_session_ready"]:
    st.warning("This reset link is not active yet — if you just opened it, refresh once.")
    st.stop()


# --------------------------------------------------
# Password Reset Form
# --------------------------------------------------
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
            st.success("Password updated — you may now sign in.")
        except Exception as e:
            st.error(f"Password reset failed: {e}")
