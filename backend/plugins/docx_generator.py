"""
DOCX Generator Utility
=======================
Converts a plain-text legislative analysis memo into a formatted Word document.

Design decisions:
  - Detects numbered section headers (e.g. "1. SUMMARY OF TESTIMONY POSITIONS")
    and renders them as Word Heading 2 in Senate dark-red.
  - All other lines become body paragraphs at 11pt.
  - A title block and footer are added automatically.
  - Returns a BytesIO buffer — caller streams it directly to the HTTP response.
"""

import re
from io import BytesIO
from datetime import datetime


def generate_docx_buffer(
    memo_text:       str,
    bill_id:         str,
    session:         str  = "",
    testifier_count: int  = 0,
    memo_style:      str  = "full_analysis",
) -> BytesIO:
    """
    Build a Word .docx from memo text and return it as a seeked BytesIO buffer.
    Raises ImportError if python-docx is not installed.
    """
    try:
        from docx import Document
        from docx.shared import Pt, RGBColor, Inches
        from docx.enum.text import WD_ALIGN_PARAGRAPH
    except ImportError:
        raise ImportError("python-docx not installed. Run: pip install python-docx")

    SENATE_RED  = RGBColor(0x7A, 0x1A, 0x1A)
    DARK_RED    = RGBColor(0x5A, 0x00, 0x00)
    GREY        = RGBColor(0x6B, 0x72, 0x80)
    LIGHT_GREY  = RGBColor(0x9C, 0xA3, 0xAF)

    style_labels = {
        "full_analysis":    "Full Analysis",
        "quick_summary":    "Quick Summary",
        "amendments_focus": "Amendments Focus",
        "opposition_report": "Opposition Report",
    }

    doc = Document()

    # ── Page margins ─────────────────────────────────────────────────────────
    for section in doc.sections:
        section.page_width   = Inches(8.5)
        section.page_height  = Inches(11)
        section.left_margin  = Inches(1.1)
        section.right_margin = Inches(1.1)
        section.top_margin   = Inches(1.0)
        section.bottom_margin = Inches(1.0)

    # ── Title ────────────────────────────────────────────────────────────────
    title_p = doc.add_paragraph()
    title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title_run = title_p.add_run("LEGISLATIVE ANALYSIS MEMO")
    title_run.bold       = True
    title_run.font.size  = Pt(16)
    title_run.font.color.rgb = SENATE_RED

    # ── Subtitle / metadata ──────────────────────────────────────────────────
    sub_p = doc.add_paragraph()
    sub_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub_text = f"Bill: {bill_id}"
    if session:
        sub_text += f"  ·  Session: {session}"
    if testifier_count:
        sub_text += f"  ·  {testifier_count} Testifier(s)"
    sub_text += f"  ·  {style_labels.get(memo_style, memo_style)}"
    sub_run = sub_p.add_run(sub_text)
    sub_run.font.size  = Pt(10)
    sub_run.font.color.rgb = GREY

    date_p = doc.add_paragraph()
    date_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    date_run = date_p.add_run(f"Generated: {datetime.now().strftime('%B %d, %Y')}")
    date_run.font.size  = Pt(9)
    date_run.font.color.rgb = LIGHT_GREY
    date_run.italic = True

    # Horizontal rule via a bottom border on an empty paragraph
    hr_p = doc.add_paragraph()
    from docx.oxml.ns import qn
    from docx.oxml   import OxmlElement
    pPr  = hr_p._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"),   "single")
    bottom.set(qn("w:sz"),    "6")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "7A1A1A")
    pBdr.append(bottom)
    pPr.append(pBdr)

    doc.add_paragraph()  # spacer

    # ── Body — parse memo text into headings vs. paragraphs ─────────────────
    #
    # Heading patterns we recognise:
    #   "1. SUMMARY OF TESTIMONY POSITIONS"
    #   "## AMENDMENTS REQUESTED"
    #   A line that is ALL CAPS and 5–70 chars long
    #
    HEADING_RE = re.compile(
        r'^(\d+\.\s+[A-Z][A-Z\s/()]{4,}|#{1,3}\s+.+|[A-Z][A-Z\s/()]{4,}[A-Z)])$'
    )

    lines = memo_text.split("\n")
    i     = 0
    while i < len(lines):
        raw     = lines[i]
        stripped = raw.strip()
        i += 1

        if not stripped:
            # Blank line — small spacer paragraph
            sp = doc.add_paragraph()
            sp.paragraph_format.space_before = Pt(2)
            sp.paragraph_format.space_after  = Pt(2)
            continue

        if stripped.startswith("---") or stripped.startswith("==="):
            continue  # Skip horizontal rules from the memo text itself

        if HEADING_RE.match(stripped):
            # Render as Word Heading 2
            clean = re.sub(r'^[#\d.\s]+', '', stripped).strip()
            h = doc.add_heading(clean or stripped, level=2)
            h.paragraph_format.space_before = Pt(14)
            h.paragraph_format.space_after  = Pt(4)
            for run in h.runs:
                run.font.color.rgb = DARK_RED
                run.font.size      = Pt(12)
        else:
            # Regular body paragraph
            p = doc.add_paragraph(stripped)
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after  = Pt(5)
            for run in p.runs:
                run.font.size = Pt(11)

    # ── Footer ───────────────────────────────────────────────────────────────
    doc.add_paragraph()
    footer_p = doc.add_paragraph()
    footer_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer_run = footer_p.add_run(
        "Maryland General Assembly  ·  Senate Staff  ·  Internal Use Only"
    )
    footer_run.font.size  = Pt(8)
    footer_run.font.color.rgb = LIGHT_GREY
    footer_run.italic = True

    # ── Serialise to buffer ──────────────────────────────────────────────────
    buf = BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf
