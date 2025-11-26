"""
Contract DOCX generation engine for PRS.

Features:
 - Loads DOCX template
 - Replaces ${placeholders} using ctx
 - Inserts PRS logo at top
 - Inserts Ray’s signature at bottom
 - Saves a filled DOCX to a temp path
"""

import os
import io
import tempfile
from typing import Dict, Any
from docx import Document
from docx.shared import Inches
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT


# -------------------------------------------------------------------
# Utility: find first paragraph in the document (for inserting logo)
# -------------------------------------------------------------------
def _insert_logo(doc: Document, logo_path: str):
    if not os.path.exists(logo_path):
        print(f"[WARN] Logo file not found: {logo_path}")
        return

    # Insert at very top
    p = doc.paragraphs[0].insert_paragraph_before()
    run = p.add_run()
    run.add_picture(logo_path, width=Inches(2.5))
    p.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER


# -------------------------------------------------------------------
# Utility: insert signature on its own line at bottom
# -------------------------------------------------------------------
def _insert_signature(doc: Document, signature_path: str):
    if not os.path.exists(signature_path):
        print(f"[WARN] Signature file not found: {signature_path}")
        return

    # blank line
    p1 = doc.add_paragraph("")
    p1.alignment = WD_PARAGRAPH_ALIGNMENT.LEFT

    # signature image
    p2 = doc.add_paragraph()
    run = p2.add_run()
    run.add_picture(signature_path, width=Inches(2.0))
    p2.alignment = WD_PARAGRAPH_ALIGNMENT.LEFT

    # name line (optional)
    doc.add_paragraph("")


# -------------------------------------------------------------------
# Utility: replace placeholders everywhere in doc
# -------------------------------------------------------------------
def _replace_placeholders(doc: Document, ctx: Dict[str, Any]):
    replacement_map = {f"${{{k}}}": str(v) for k, v in ctx.items()}

    # paragraphs
    for p in doc.paragraphs:
        for key, val in replacement_map.items():
            if key in p.text:
                for run in p.runs:
                    run.text = run.text.replace(key, val)

    # tables
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    for run in p.runs:
                        for key, val in replacement_map.items():
                            if key in run.text:
                                run.text = run.text.replace(key, val)


# -------------------------------------------------------------------
# MAIN ENTRY POINT
# -------------------------------------------------------------------
def render_contract_docx(ctx: Dict[str, Any], template_path: str) -> str:
    """
    Build a fully rendered contract DOCX with images + merged fields.

    Returns:
        A filesystem path to a temp .docx file ready for download.
    """

    if not os.path.exists(template_path):
        raise FileNotFoundError(f"Template DOCX not found: {template_path}")

    # Determine asset paths relative to repo
    base_dir = os.path.dirname(os.path.dirname(__file__))
    assets_dir = os.path.join(base_dir, "assets")

    logo_path = os.path.join(assets_dir, "prs_logo.png")
    signature_path = os.path.join(assets_dir, "ray_signature.png")

    # Load template
    doc = Document(template_path)

    # Insert logo at top
    _insert_logo(doc, logo_path)

    # Replace placeholders
    _replace_placeholders(doc, ctx)

    # Insert signature at bottom
    _insert_signature(doc, signature_path)

    # Save to temp file
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".docx")
    doc.save(tmp.name)
    tmp.close()

    return tmp.name
