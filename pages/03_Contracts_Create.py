import streamlit as st

st.title("📝 Create Contract")

if "user" not in st.session_state or not st.session_state["user"]:
    st.error("Please sign in first.")
    st.stop()

st.write("Contract creation form goes here.")
