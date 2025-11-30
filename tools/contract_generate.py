"""
Contract DOCX generation engine for PRS.

Correct merge engine using docxtpl (Jinja2 syntax).
"""

import os
import tempfile
from typing import Dict, Any
from pathlib import Path

from docxtpl import DocxTemplate, InlineImage
from docx.shared import Inches

# ------------------------------
# Custom Jinja2 Filters
# ------------------------------

# def jinja_filter_to_12h(value):
    # """
    # Convert 'HH:MM' or 'HH:MM:SS' string to 12-hour AM/PM format.
    # Safe for None or empty input.
    # """
    # if not value:
        # return ""
    # if isinstance(value, str):
        # for fmt in ("%H:%M:%S", "%H:%M"):
            # try:
                # dt_obj = datetime.strptime(value, fmt)
                # Use %I and strip leading zero so 07:00 -> 7:00 PM
                # return dt_obj.strftime("%I:%M %p").lstrip("0")
            # except ValueError:
                # continue
    # Fallback: if parsing fails, just return the original
    # return value


# MAIN ENTRY POINT -----------------------------------------------------

def render_contract_docx(ctx: Dict[str, Any], template_path: Path | str) -> str:
    """
    Render a contract DOCX from the given context and template.
    Returns the path to a temporary .docx that the caller can offer for download.
    """
    # Ensure we have a real path string
    template_path = str(template_path)

    # Copy context so we can add aliases without mutating caller's dict
    ctx = dict(ctx or {})

    # --- Aliases / fallbacks for convenience inside the template ---
    # Total contract amount
    ctx.setdefault(
        "contract_total_amount",
        ctx.get("private_contract_total_amount")
        or ctx.get("gig_total_fee")
        or ctx.get("gig_fee")
        or 0,
    )

    # Nice date string
    if ctx.get("computed_event_date_formatted"):
        ctx.setdefault("event_date_long", ctx["computed_event_date_formatted"])

    # 12h time strings
    ctx.setdefault("start_time_12h", ctx.get("computed_start_time_12h") or "")
    ctx.setdefault("end_time_12h", ctx.get("computed_end_time_12h") or "")
    ctx.setdefault(
        "reception_time_12h",
        ctx.get("computed_reception_time_range_12h")
        or ctx.get("reception_time")
        or "",
    )

    # Build a simple client block for the header if you want it
    ctx.setdefault("client_display_name", ctx.get("private_client_name") or "")
    ctx.setdefault("organizer_display_name", ctx.get("private_organizer") or "")

    # --- Load template ---
    doc = DocxTemplate(template_path)

    # --- Inline images (small logo + signature) ---
    sig_path = ctx.get("signature_image_path")
    logo_path = ctx.get("logo_image_path")

    if sig_path and os.path.exists(sig_path):
        ctx["signature_image"] = InlineImage(doc, sig_path, width=Inches(1.8))
    else:
        ctx["signature_image"] = ""

    if logo_path and os.path.exists(logo_path):
        ctx["logo_image"] = InlineImage(doc, logo_path, width=Inches(1.5))
    else:
        ctx["logo_image"] = ""

    # --- Render ---
    doc.render(ctx)

    # --- Save to a temp file and return path ---
    fd, out_path = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    doc.save(out_path)
    return out_path