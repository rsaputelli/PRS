# lib/auth.py

import ast
import streamlit as st

def is_logged_in() -> bool:
    """True if user is authenticated via Supabase."""
    return "user" in st.session_state and bool(st.session_state["user"])


def current_user():
    """Returns the Supabase user dict or None."""
    return st.session_state.get("user")


def _load_admin_list() -> list[str]:
    """
    Load PRS_ADMINS from st.secrets in a robust way.

    Handles:
    - Proper TOML list: PRS_ADMINS = ["a", "b"]
    - JSON/TOML string: PRS_ADMINS = "['a','b']"
    - Nested dicts: {"PRS_ADMINS": [...]}
    """

    raw = st.secrets.get("PRS_ADMINS", None)

    # Minimal debug to understand structure without dumping all secrets
    # st.write("DEBUG IS_ADMIN raw PRS_ADMINS type:", type(raw).__name__)
    # st.write("DEBUG IS_ADMIN raw PRS_ADMINS value:", raw)

    admins: list[str] = []

    if raw is None:
        return []

    # Case 1: already a list/tuple/set
    if isinstance(raw, (list, tuple, set)):
        admins = list(raw)

    # Case 2: a dict, possibly nested
    elif isinstance(raw, dict):
        if "PRS_ADMINS" in raw and isinstance(raw["PRS_ADMINS"], (list, tuple, set)):
            admins = list(raw["PRS_ADMINS"])
        else:
            # Fallback: use all values
            admins = [v for v in raw.values()]

    # Case 3: a string — might be a JSON/TOML list
    elif isinstance(raw, str):
        txt = raw.strip()
        if txt.startswith("[") and txt.endswith("]"):
            try:
                parsed = ast.literal_eval(txt)
                if isinstance(parsed, (list, tuple, set)):
                    admins = list(parsed)
                else:
                    admins = [txt]
            except Exception:
                admins = [txt]
        else:
            admins = [txt]

    # Normalize: strings, lowercase, strip
    norm = []
    for a in admins:
        if a is None:
            continue
        norm.append(str(a).lower().strip())

    # st.write("DEBUG IS_ADMIN parsed admin list:", norm)
    return norm


def IS_ADMIN() -> bool:
    """
    Unified PRS admin gate.
    All admin-only pages should rely on this.
    """
    user = current_user()
    if not user:
        return False

    email = (user.get("email") or "").lower().strip()
    admin_list = _load_admin_list()

    # st.write("DEBUG IS_ADMIN current user email:", email)

    return email in admin_list

