import streamlit as st

st.title("📤 Contract Review & Send (Admin)")

if "user" not in st.session_state or not st.session_state["user"]:
    st.error("Please sign in.")
    st.stop()

st.write("Admin review and send functionality will appear here.")
