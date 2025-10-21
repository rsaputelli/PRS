import streamlit as st

st.title("📅 Schedule View")

if "user" not in st.session_state or not st.session_state["user"]:
    st.error("Please sign in first from the Login page.")
    st.stop()

st.write("This is where the gig schedule will appear.")
