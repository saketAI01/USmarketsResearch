"""
PDF report generator using reportlab.
Produces:
  - Cover page
  - Market snapshot + scoring legend
  - Summary table (all candidates ranked)
  - Per-stock appendix (one page per stock)
"""
from __future__ import annotations
import io
from datetime import datetime
from pathlib import Path
from typing import List

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate,
    Paragraph, Spacer, Table, TableStyle, PageBreak, HRFlowable, KeepTogether,
)
from reportlab.graphics.shapes import Drawing, Rect, String, Line
from reportlab.graphics import renderPDF
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT

from config import COMPONENT_LABELS, APP_NAME, APP_VERSION, get_rating

# ── Colour palette ────────────────────────────────────────────────────────────
C_BG        = colors.HexColor("#1c1c1e")
C_AMBER     = colors.HexColor("#f5a623")
C_AMBER_LT  = colors.HexColor("#faeeda")
C_TEAL      = colors.HexColor("#1d9e75")
C_TEAL_LT   = colors.HexColor("#e1f5ee")
C_BLUE      = colors.HexColor("#185fa5")
C_BLUE_LT   = colors.HexColor("#e6f1fb")
C_RED       = colors.HexColor("#993c1d")
C_RED_LT    = colors.HexColor("#faece7")
C_GRAY      = colors.HexColor("#888780")
C_GRAY_LT   = colors.HexColor("#f1efe8")
C_WHITE     = colors.white
C_BLACK     = colors.HexColor("#1c1c1e")
C_LINE      = colors.HexColor("#d3d1c7")

W, H = A4  # 595.27 x 841.89 pt


def _score_color(score: int):
    if score >= 80: return C_AMBER
    if score >= 60: return C_TEAL
    if score >= 40: return C_BLUE
    return C_RED


def _score_bg(score: int):
    if score >= 80: return C_AMBER_LT
    if score >= 60: return C_TEAL_LT
    if score >= 40: return C_BLUE_LT
    return C_RED_LT


# ── Styles ────────────────────────────────────────────────────────────────────

def _styles():
    return {
        "title": ParagraphStyle("title", fontSize=28, textColor=C_WHITE,
                                fontName="Helvetica-Bold", leading=34, alignment=TA_LEFT),
        "subtitle": ParagraphStyle("subtitle", fontSize=13, textColor=C_AMBER,
                                   fontName="Helvetica", leading=18, alignment=TA_LEFT),
        "h1": ParagraphStyle("h1", fontSize=16, textColor=C_BLACK,
                             fontName="Helvetica-Bold", leading=22, spaceAfter=6),
        "h2": ParagraphStyle("h2", fontSize=12, textColor=C_GRAY,
                             fontName="Helvetica-Bold", leading=16, spaceAfter=4,
                             textTransform="uppercase", letterSpacing=0.8),
        "body": ParagraphStyle("body", fontSize=9, textColor=C_BLACK,
                               fontName="Helvetica", leading=13),
        "small": ParagraphStyle("small", fontSize=8, textColor=C_GRAY,
                                fontName="Helvetica", leading=11),
        "mono": ParagraphStyle("mono", fontSize=9, textColor=C_BLACK,
                               fontName="Courier", leading=13),
        "ticker_hdr": ParagraphStyle("ticker_hdr", fontSize=20, textColor=C_BLACK,
                                     fontName="Helvetica-Bold", leading=24),
        "score_big": ParagraphStyle("score_big", fontSize=36, textColor=C_AMBER,
                                    fontName="Helvetica-Bold", leading=40, alignment=TA_RIGHT),
        "disclaimer": ParagraphStyle("disclaimer", fontSize=7, textColor=C_GRAY,
                                     fontName="Helvetica", leading=9, alignment=TA_CENTER),
    }


# ── Score bar drawing ─────────────────────────────────────────────────────────

def _score_bar(score: int, bar_width: float = 200, bar_height: float = 8) -> Drawing:
    d = Drawing(bar_width, bar_height + 2)
    # Background
    d.add(Rect(0, 0, bar_width, bar_height, fillColor=C_GRAY_LT,
               strokeColor=None, rx=3, ry=3))
    # Fill
    fill_w = max(4, (score / 100) * bar_width)
    d.add(Rect(0, 0, fill_w, bar_height, fillColor=_score_color(score),
               strokeColor=None, rx=3, ry=3))
    return d


# ── Page templates ────────────────────────────────────────────────────────────

def _header_footer(canvas, doc):
    canvas.saveState()
    # Footer line
    canvas.setStrokeColor(C_LINE)
    canvas.setLineWidth(0.5)
    canvas.line(20*mm, 14*mm, W - 20*mm, 14*mm)
    # Footer text
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(C_GRAY)
    canvas.drawString(20*mm, 10*mm, APP_NAME)
    canvas.drawRightString(W - 20*mm, 10*mm, f"Page {doc.page}")
    canvas.restoreState()


def _cover_hf(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(C_BG)
    canvas.rect(0, 0, W, H, fill=1, stroke=0)
    canvas.restoreState()


# ── Cover page ────────────────────────────────────────────────────────────────

def _cover_elements(results, settings: dict, st: dict) -> list:
    date_str = datetime.now().strftime("%d %B %Y")
    n_analyzed = settings.get("n_analyzed", len(results))
    n_candidates = len(results)
    min_score = settings.get("min_score", 60)
    markets = settings.get("markets", "US + IN")

    elems = [Spacer(1, 60*mm)]
    elems.append(Paragraph("CANSLIM", st["title"]))
    elems.append(Paragraph("Screening Report", st["title"]))
    elems.append(Spacer(1, 6*mm))
    elems.append(Paragraph(f"Generated {date_str}  ·  {APP_NAME} v{APP_VERSION}", st["subtitle"]))
    elems.append(Spacer(1, 20*mm))

    # Stats row
    stat_data = [
        [_fmt_stat("Stocks Analysed", str(n_analyzed)),
         _fmt_stat("Candidates Found", str(n_candidates)),
         _fmt_stat("Min Score Threshold", str(min_score)),
         _fmt_stat("Markets", markets)],
    ]
    stat_table = Table(stat_data, colWidths=[(W - 40*mm) / 4] * 4)
    stat_table.setStyle(TableStyle([
        ("BOX",           (0, 0), (-1, -1), 0.5, colors.HexColor("#3a3a3c")),
        ("INNERGRID",     (0, 0), (-1, -1), 0.5, colors.HexColor("#3a3a3c")),
        ("BACKGROUND",    (0, 0), (-1, -1), colors.HexColor("#2c2c2e")),
        ("TOPPADDING",    (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    elems.append(stat_table)
    elems.append(Spacer(1, 8*mm))
    elems.append(Paragraph(
        "AI-scored using O'Neil CANSLIM methodology. Not investment advice. "
        "Verify all data with live sources before making any trading decisions.",
        ParagraphStyle("disc", fontSize=8, textColor=C_GRAY, fontName="Helvetica",
                       leading=11, alignment=TA_CENTER)
    ))
    return elems


def _fmt_stat(label: str, value: str) -> Paragraph:
    text = (f'<font size="8" color="#888780">{label}</font><br/>'
            f'<font size="18" color="#f5a623"><b>{value}</b></font>')
    return Paragraph(text, ParagraphStyle("stat", alignment=TA_CENTER, leading=24))


# ── Summary table ─────────────────────────────────────────────────────────────

def _summary_elements(results, st: dict) -> list:
    elems = []
    elems.append(Paragraph("Screening Results — Ranked by Composite Score", st["h1"]))
    elems.append(Spacer(1, 4*mm))

    header = ["#", "Ticker", "Company", "Mkt", "Score", "Rating",
              "C", "A", "N", "S", "L", "I", "M", "Buy?"]
    rows = [header]
    for i, r in enumerate(results, 1):
        comp = r.components
        row = [
            str(i), r.ticker,
            r.company_name[:28] + ("…" if len(r.company_name) > 28 else ""),
            r.market,
            f"{r.composite_score:.1f}",
            r.rating,
            *[str(comp[k].score) if k in comp else "—" for k in "CANSLIM"],
            "✓" if r.buy_candidate else "—",
        ]
        rows.append(row)

    col_w = [8*mm, 18*mm, 55*mm, 10*mm, 16*mm, 26*mm,
             10*mm, 10*mm, 10*mm, 10*mm, 10*mm, 10*mm, 10*mm, 10*mm]
    t = Table(rows, colWidths=col_w, repeatRows=1)

    style = [
        ("BACKGROUND",    (0, 0), (-1, 0),  C_BG),
        ("TEXTCOLOR",     (0, 0), (-1, 0),  C_WHITE),
        ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0),  7),
        ("FONTNAME",      (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE",      (0, 1), (-1, -1), 7.5),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [C_WHITE, C_GRAY_LT]),
        ("GRID",          (0, 0), (-1, -1), 0.3, C_LINE),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 3),
    ]

    for i, r in enumerate(results, 1):
        sc = r.composite_score
        bg = _score_bg(int(sc))
        style.append(("BACKGROUND", (4, i), (4, i), bg))
        style.append(("TEXTCOLOR",  (4, i), (4, i), _score_color(int(sc))))
        style.append(("FONTNAME",   (4, i), (4, i), "Helvetica-Bold"))
        if r.buy_candidate:
            style.append(("TEXTCOLOR", (13, i), (13, i), C_TEAL))
            style.append(("FONTNAME",  (13, i), (13, i), "Helvetica-Bold"))

    t.setStyle(TableStyle(style))
    elems.append(t)
    return elems


# ── Per-stock appendix page ───────────────────────────────────────────────────

def _stock_page_elements(r, st: dict) -> list:
    elems = [PageBreak()]
    comp = r.components
    sc = int(r.composite_score)

    # Header band
    hdr_data = [[
        Paragraph(f'<b>{r.ticker}</b>', st["ticker_hdr"]),
        Paragraph(r.company_name, ParagraphStyle(
            "cname", fontSize=10, textColor=C_GRAY, fontName="Helvetica", leading=14)),
        Paragraph(f'<font color="#f5a623"><b>{r.composite_score:.1f}</b></font>'
                  f'<br/><font size="8" color="#888780">{r.rating}</font>',
                  ParagraphStyle("cs", fontSize=28, fontName="Helvetica-Bold",
                                 leading=32, alignment=TA_RIGHT)),
    ]]
    hdr_t = Table(hdr_data, colWidths=[40*mm, 90*mm, 35*mm])
    hdr_t.setStyle(TableStyle([
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW",     (0, 0), (-1, 0),  1.0, C_AMBER),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    elems.append(hdr_t)
    elems.append(Spacer(1, 4*mm))

    # Buy badge + market + quality
    badge_txt = (
        f'<font color="#085041">Buy Candidate</font>' if r.buy_candidate
        else f'<font color="#444441">Watch Only</font>'
    )
    meta = (f'Market: <b>{r.market}</b>  |  Sector: <b>{r.sector or "—"}</b>  |  '
            f'Data Quality: <b>{r.data_quality}</b>  |  {badge_txt}')
    elems.append(Paragraph(meta, st["small"]))
    elems.append(Spacer(1, 5*mm))

    # Component rows
    elems.append(Paragraph("COMPONENT BREAKDOWN", st["h2"]))
    elems.append(Spacer(1, 2*mm))

    for key in ["C", "A", "N", "S", "L", "I", "M"]:
        cr = comp.get(key)
        if not cr:
            continue
        weight_pct = int(cr.weight * 100)

        comp_data = [[
            Paragraph(f'<b>{key}</b>', ParagraphStyle(
                "ck", fontSize=13, fontName="Helvetica-Bold",
                textColor=_score_color(cr.score), leading=16)),
            Paragraph(f'{cr.label} <font size="8" color="#888780">({weight_pct}%)</font>',
                      ParagraphStyle("cl", fontSize=9, fontName="Helvetica",
                                     textColor=C_BLACK, leading=13)),
            _score_bar(cr.score, bar_width=120, bar_height=7),
            Paragraph(f'<b>{cr.score}</b>', ParagraphStyle(
                "cscore", fontSize=12, fontName="Helvetica-Bold",
                textColor=_score_color(cr.score), leading=16, alignment=TA_RIGHT)),
        ]]
        comp_t = Table(comp_data, colWidths=[10*mm, 65*mm, 28*mm, 15*mm])
        comp_t.setStyle(TableStyle([
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING",    (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LINEBELOW",     (0, 0), (-1, 0),  0.3, C_LINE),
        ]))
        elems.append(comp_t)

        # Key metric + rationale
        metric_data = [[
            Paragraph(cr.key_metric, ParagraphStyle(
                "km", fontSize=8, fontName="Courier",
                textColor=_score_color(cr.score), leading=11)),
            Paragraph(cr.rationale, st["small"]),
        ]]
        metric_t = Table(metric_data, colWidths=[45*mm, 75*mm])
        metric_t.setStyle(TableStyle([
            ("LEFTPADDING",   (0, 0), (0, 0), 10*mm),
            ("TOPPADDING",    (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        elems.append(metric_t)

    # Errors / warnings
    if r.errors or r.warnings:
        elems.append(Spacer(1, 4*mm))
        elems.append(Paragraph("DATA NOTES", st["h2"]))
        for msg in r.errors:
            elems.append(Paragraph(f"⚠ {msg}", ParagraphStyle(
                "err", fontSize=8, textColor=C_RED, fontName="Helvetica", leading=11)))
        for msg in r.warnings:
            elems.append(Paragraph(f"ℹ {msg}", ParagraphStyle(
                "warn", fontSize=8, textColor=C_BLUE, fontName="Helvetica", leading=11)))

    elems.append(Spacer(1, 4*mm))
    elems.append(Paragraph(
        "This analysis is AI-assisted and based on publicly available data. "
        "Not investment advice. Always verify with current data before trading.",
        st["disclaimer"]
    ))

    return elems


# ── Main PDF builder ──────────────────────────────────────────────────────────

def generate_report(results: list, output_path: str, settings: dict = None) -> str:
    if settings is None:
        settings = {}

    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    st = _styles()

    doc = BaseDocTemplate(
        str(p),
        pagesize=A4,
        leftMargin=20*mm, rightMargin=20*mm,
        topMargin=20*mm, bottomMargin=20*mm,
        title=f"CANSLIM Screening Report — {datetime.now().strftime('%Y-%m-%d')}",
        author=APP_NAME,
    )

    cover_frame  = Frame(0, 0, W, H, leftPadding=20*mm, rightPadding=20*mm,
                         topPadding=0, bottomPadding=10*mm, id="cover")
    body_frame   = Frame(20*mm, 18*mm, W - 40*mm, H - 38*mm, id="body")

    cover_tmpl = PageTemplate(id="cover_tmpl", frames=[cover_frame],
                              onPage=_cover_hf)
    body_tmpl  = PageTemplate(id="body_tmpl",  frames=[body_frame],
                              onPage=_header_footer)
    doc.addPageTemplates([cover_tmpl, body_tmpl])

    story = []

    # Cover
    story.extend(_cover_elements(results, settings, st))
    story.append(PageBreak())

    # Switch to body template
    from reportlab.platypus import NextPageTemplate
    story.append(NextPageTemplate("body_tmpl"))
    story.append(PageBreak())

    # Summary table
    story.extend(_summary_elements(results, st))
    story.append(PageBreak())

    # Per-stock appendix
    story.append(Paragraph("PER-STOCK APPENDIX", st["h1"]))
    story.append(Spacer(1, 4*mm))
    story.append(Paragraph(
        f"Detailed component breakdown for all {len(results)} candidates, ranked by composite score.",
        st["body"]
    ))

    for r in results:
        story.extend(_stock_page_elements(r, st))

    doc.build(story)
    return str(p)
