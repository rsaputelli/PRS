"""
Contract DOCX generation engine for PRS.

Correct merge engine using docxtpl (Jinja2 syntax).
"""

import os
import tempfile
from typing import Dict, Any
from docxtpl import DocxTemplate, InlineImage
from docx.shared import Inches

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

    # Add images to context (InlineImage objects)
    # Only if file exists
    if os.path.exists(logo_path):
        ctx["prs_logo"] = InlineImage(DocxTemplate(template_path), logo_path, width=Inches(2.5))
    else:
        ctx["prs_logo"] = ""

    if os.path.exists(signature_path):
        ctx["rays_signature"] = InlineImage(DocxTemplate(template_path), signature_path, width=Inches(2.0))
    else:
        ctx["rays_signature"] = ""

    # Load template with docxtpl
    doc = DocxTemplate(template_path)

    # Render with Jinja2 context
    doc.render(ctx)

    # Create temp output
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".docx")
    doc.save(tmp.name)
    tmp.close()

    return tmp.name
