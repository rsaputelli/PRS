import streamlit as st

# 🚨 This MUST run before anything else
st.components.v1.html(
    """
    <script>
    console.log("🎯 RESET PAGE SHIM RUNNING");

    const h = window.location.hash;
    if (!h || !h.includes("access_token")) {
      console.log("❌ No hash present");
      return;
    }

    const q = new URLSearchParams(h.substring(1));

    const url =
      window.location.pathname
      + "?type=recovery"
      + "&access_token=" + encodeURIComponent(q.get("access_token") || "")
      + "&refresh_token=" + encodeURIComponent(q.get("refresh_token") || "");

    console.log("🔁 Redirecting to", url);
    window.location.replace(url);
    </script>
    """,
    height=0,
)

st.write("🔎 Debug: page loaded but no redirect yet")

params = st.query_params
is_recovery = params.get("type") == "recovery"
access_token = params.get("access_token")

if not is_recovery or not access_token:
    st.warning("This page is only for password reset links. Please use the link from your email.")
    st.stop()

st.success("🎉 Token detected — form will go here next")
