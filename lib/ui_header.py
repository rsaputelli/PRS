# lib/ui_header.py
from pathlib import Path
import streamlit as st

def render_header(title: str, emoji: str = "", logo_filename: str = "prs_logo.png") -> None:
    """
    Renders a two-column header with the logo on the left and a big title on the right.
    - `title`: page title text (e.g., "Enter Gig", "📅 Schedule View")
    - `emoji`: optional emoji that will appear before the title (if you want)
    - `logo_filename`: file name inside the repo's assets folder (default: prs_logo.png)
    """
    # Try a secrets-based URL first so you can swap logos without code changes
    # e.g., in secrets.toml: LOGO_URL = "https://example.com/logo.png"
    logo_url = st.secrets.get("LOGO_URL")

    # Resolve local assets path relative to the repo root:
    # lib/ is at repo_root/lib -> assets is repo_root/assets
    assets_path = Path(__file__).resolve().parent.parent / "assets" / logo_filename

    left, right = st.columns([0.12, 0.88])
    with left:
        if logo_url:
            st.image(logo_url, use_container_width=True)
        elif assets_path.exists():
            st.image(str(assets_path), use_container_width=True)
        # else: no error—just render nothing

    with right:
        # Compose the H1 (emoji optional)
        title_text = f"{emoji} {title}" if emoji else title
        st.markdown(f"<h1 style='margin-bottom:0'>{title_text}</h1>", unsafe_allow_html=True)
