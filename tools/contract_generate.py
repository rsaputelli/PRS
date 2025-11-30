"""
Contract DOCX generation engine for PRS.

Correct merge engine using docxtpl (Jinja2 syntax).
"""

import os
import tempfile
from typing import Dict, Any
from datetime import datetime
from docxtpl import DocxTemplate, InlineImage
from docx.shared import Inches

# ------------------------------
# Custom Jinja2 Filters
# ------------------------------

def jinja_filter_to_12h(value):
    """
    Convert 'HH:MM' or 'HH:MM:SS' string to 12-hour AM/PM format.
    Safe for None or empty input.
    """
    if not value:
        return ""
    if isinstance(value, str):
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                dt_obj = datetime.strptime(value, fmt)
                # Use %I and strip leading zero so 07:00 -> 7:00 PM
                return dt_obj.strftime("%I:%M %p").lstrip("0")
            except ValueError:
                continue
    # Fallback: if parsing fails, just return the original
    return value


# MAIN ENTRY POINT -----------------------------------------------------

def render_contract_docx(ctx: Dict[str, Any], template_path: str) -> str:
    """
    Build a fully rendered contract DOCX using docxtpl + Jinja.

    Returns a filesystem path to a temp .docx file ready for download.
    """

    if not os.path.exists(template_path):
        raise FileNotFoundError(f"Template DOCX not found: {template_path}")

    # Base assets path
    base_dir = os.path.dirname(os.path.dirname(__file__))
    assets_dir = os.path.join(base_dir, "assets")

    logo_path = os.path.join(assets_dir, "prs_logo.png")
    signature_path = os.path.join(assets_dir, "ray_signature.png")

    # Load template FIRST
    doc = DocxTemplate(template_path)

    # Register custom filters
    doc.jinja_env.filters["to_12h"] = jinja_filter_to_12h

    # Add images AFTER doc exists
    if os.path.exists(logo_path):
        ctx["prs_logo"] = InlineImage(doc, logo_path, width=Inches(2.5))
    else:
        ctx["prs_logo"] = ""

    if os.path.exists(signature_path):
        ctx["rays_signature"] = InlineImage(doc, signature_path, width=Inches(2.0))
    else:
        ctx["rays_signature"] = ""

    # Render with Jinja2 context
    doc.render(ctx)

    # Create temp output
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".docx")
    doc.save(tmp.name)
    tmp.close()

    return tmp.name

