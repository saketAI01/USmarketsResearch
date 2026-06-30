#!/usr/bin/env python3
"""
ta_reports.py — Technical Analyst Pro
Export analysis to Markdown, HTML, and PDF.
"""

from __future__ import annotations
import json, os
from datetime import datetime
from typing import Optional

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        HRFlowable, Image as RLImage,
    )
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
    REPORTLAB_OK = True
except ImportError:
    REPORTLAB_OK = False


# ═══════════════════════════════════════════════════════════════════════════
#  REPORT GENERATOR
# ═══════════════════════════════════════════════════════════════════════════
class ReportGenerator:
    """Generates Markdown, HTML and PDF reports from analysis dicts."""

    # ── Markdown ──────────────────────────────────────────────────────────
    def to_markdown(self, analysis: dict, ai_insights: dict = None,
                    chart_path: str = None) -> str:
        a   = analysis
        sym = a.get("symbol","?")
        dt  = a.get("analysis_date", datetime.now().strftime("%Y-%m-%d"))
        cp  = a.get("current_price", 0)
        td  = a.get("trend",{})
        ma  = a.get("moving_averages",{})
        vd  = a.get("volume",{})
        sup = a.get("support_levels",[])
        res = a.get("resistance_levels",[])
        pats= a.get("patterns",[])
        scen= a.get("scenarios",[])
        obs = a.get("key_observations",[])
        ass = a.get("assessment","")

        lines = [
            f"# Technical Analysis Report — {sym}",
            "",
            f"**Ticker / Symbol:** {sym}  ",
            f"**Timeframe:** Weekly  ",
            f"**Analysis Date:** {dt}  ",
            f"**Current Price:** ${cp:,.2f}  ",
            f"**Analyst:** Technical Analyst Pro",
            "",
            "---",
            "",
            "## 1. Chart Overview",
            "",
            f"Price is currently at **${cp:,.2f}** with a "
            f"**{a.get('chg_1bar',0):+.1f}%** change over the past week, "
            f"**{a.get('chg_4bar',0):+.1f}%** over four weeks, and "
            f"**{a.get('chg_52bar',0):+.1f}%** over 52 periods.",
            "",
        ]

        if chart_path and os.path.exists(chart_path):
            lines += [f"![Chart]({chart_path})", ""]

        # Trend
        lines += [
            "---",
            "",
            "## 2. Trend Analysis",
            "",
            f"- **Direction:** {td.get('direction','?')}",
            f"- **Strength:** {td.get('strength','?')}",
            f"- **Duration:** ~{td.get('duration_bars','?')} bars",
            f"- **RSI:** {td.get('rsi',50):.1f}",
            "",
            td.get("description",""),
        ]
        if td.get("exhaustion_signals"):
            lines.append("")
            lines.append("**⚠ Exhaustion Signals:**")
            for ex in td["exhaustion_signals"]:
                lines.append(f"- {ex}")
        lines.append("")

        # S/R
        lines += ["---", "", "## 3. Support and Resistance Levels", "", "### Key Support Levels"]
        if sup:
            for i,s in enumerate(sup,1):
                lines.append(f"{i}. **${s['price']:,.2f}** — {s['strength']} ({s['touches']} touches, {s['distance_pct']:.1f}% away)")
        else:
            lines.append("No significant support levels identified.")

        lines += ["", "### Key Resistance Levels"]
        if res:
            for i,r in enumerate(res,1):
                lines.append(f"{i}. **${r['price']:,.2f}** — {r['strength']} ({r['touches']} touches, {r['distance_pct']:.1f}% away)")
        else:
            lines.append("No significant resistance levels identified.")
        lines.append("")

        # MA
        lines += ["---", "", "## 4. Moving Average Analysis", "",
                  f"**Overall Alignment:** {ma.get('alignment','?')}"]
        if ma.get("golden_cross"): lines.append("🟢 **Golden Cross detected** — MA20 recently crossed above MA50")
        if ma.get("death_cross"):  lines.append("🔴 **Death Cross detected** — MA20 recently crossed below MA50")
        lines.append("")
        for p in [20,50,200]:
            m = ma.get(f"ma{p}")
            if m:
                lines.append(f"- **MA{p}:** ${m['value']:,.2f} | {m['price_relation']} | Slope: {m['slope']} | {m['distance_pct']:+.1f}% from price")
        lines.append("")

        # Volume
        lines += ["---", "", "## 5. Volume Analysis", ""]
        lines.append(vd.get("description","No volume data."))
        lines += [
            "",
            f"- **Volume Trend:** {vd.get('trend','?')} ({vd.get('trend_pct',0):+.1f}%)",
            f"- **Volume Confirmation:** {vd.get('confirmation','?').replace('_',' ')}",
        ]
        if vd.get("spike"):
            lines.append(f"- **Volume Spike:** {vd.get('spike_ratio',1):.1f}× 20-bar average")
        lines.append("")

        # Patterns
        lines += ["---", "", "## 6. Chart Patterns and Price Action", ""]
        if pats:
            for p in pats:
                lines.append(f"- **{p['name']}** ({p['direction']}, {p['confidence']} confidence) — {p['description']}")
        else:
            lines.append("No significant chart patterns identified in recent price action.")
        lines.append("")

        # Assessment
        lines += ["---", "", "## 7. Current Market Assessment", "", ass, "", "### Key Observations"]
        for o in obs: lines.append(f"- {o}")
        lines.append("")

        # Scenarios
        lines += ["---", "", "## 8. Scenario Analysis", ""]
        for sc in scen:
            icon = {"BULLISH":"🟢","BEARISH":"🔴","NEUTRAL":"🟡"}.get(sc["type"],"⚪")
            lines += [
                f"### {icon} Scenario: {sc['name']} — **{sc['probability']}% Probability**",
                "",
                f"**Description:** {sc['description']}",
                "",
                "**Supporting Factors:**",
            ]
            for f in sc["supporting_factors"]: lines.append(f"- {f}")
            lines += [
                "",
                f"**Target Levels:** {', '.join(f'${t:,.2f}' for t in sc['target_levels'])}  ",
                f"**Invalidation Level:** ${sc['invalidation_level']:,.2f}",
                "",
                "---",
                "",
            ]

        # AI Insights
        if ai_insights and ai_insights.get("combined"):
            lines += ["## 9. AI Insights", "", ai_insights["combined"], ""]
        else:
            lines += ["## 9. AI Insights", "", "_AI insights not generated for this analysis._", ""]

        # Summary
        best_sc = max(scen, key=lambda s: s["probability"]) if scen else None
        lines += ["---", "", "## 10. Summary", ""]
        if best_sc:
            lines += [
                f"**Most Likely Scenario ({best_sc['probability']}%):** {best_sc['name']}",
                "",
                f"{best_sc['description']}",
                "",
            ]
        if sup: lines.append(f"**Critical Support:** ${sup[0]['price']:,.2f}")
        if res: lines.append(f"**Critical Resistance:** ${res[0]['price']:,.2f}")
        lines += [
            "",
            "---",
            "",
            "## 11. Disclaimer",
            "",
            "_This analysis is based purely on technical chart data and does not consider fundamental "
            "factors, news, or market sentiment. It represents a probabilistic assessment of potential "
            "scenarios, not a prediction or investment recommendation. All probabilities are estimates "
            "based on technical factors and are subject to change as new data emerges._",
            "",
            "---",
            "",
            f"**Generated by Technical Analyst Pro** — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        ]

        return "\n".join(lines)

    # ── HTML ──────────────────────────────────────────────────────────────
    def to_html(self, analysis: dict, ai_insights: dict = None,
                chart_path: str = None) -> str:
        md = self.to_markdown(analysis, ai_insights, chart_path)
        # Simple conversion (avoid heavy deps like markdown2)
        html_body = self._md_to_html(md)

        css = """
        body{font-family:'Segoe UI',Arial,sans-serif;background:#1a1a2e;color:#e0e0e0;max-width:900px;margin:0 auto;padding:32px}
        h1{color:#00b4d8;border-bottom:2px solid #2d2d44;padding-bottom:8px}
        h2{color:#00b4d8;margin-top:32px}h3{color:#a0d8ef}
        strong{color:#ffd700}
        hr{border:none;border-top:1px solid #2d2d44;margin:20px 0}
        code{background:#0f1a2e;padding:2px 6px;border-radius:3px;color:#06d6a0}
        .bull{color:#06d6a0}.bear{color:#e94560}.neutral{color:#ffd700}
        table{border-collapse:collapse;width:100%}
        td,th{border:1px solid #2d2d44;padding:8px 12px}
        th{background:#0f3460;color:#00b4d8}
        blockquote{border-left:3px solid #00b4d8;margin:0;padding:0 16px;color:#a0a0c0}
        img{max-width:100%;border-radius:8px;margin:16px 0}
        """

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Technical Analysis — {analysis.get('symbol','')}</title>
<style>{css}</style>
</head>
<body>
{html_body}
</body>
</html>"""

    def _md_to_html(self, md: str) -> str:
        """Minimal markdown → HTML converter (no external deps)."""
        import re
        lines = md.split("\n")
        out   = []
        for line in lines:
            if   line.startswith("# "):   line = f"<h1>{line[2:]}</h1>"
            elif line.startswith("## "):  line = f"<h2>{line[3:]}</h2>"
            elif line.startswith("### "): line = f"<h3>{line[4:]}</h3>"
            elif line.startswith("- "):   line = f"<li>{line[2:]}</li>"
            elif line.startswith("---"):  line = "<hr>"
            elif line.startswith("!["):
                m = re.match(r"!\[([^\]]*)\]\(([^)]+)\)", line)
                if m: line = f'<img src="{m.group(2)}" alt="{m.group(1)}">'
            else:
                # Inline formatting
                line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
                line = re.sub(r"_(.+?)_", r"<em>\1</em>", line)
                line = re.sub(r"`(.+?)`", r"<code>\1</code>", line)
                if line.strip():
                    line = f"<p>{line}</p>"
            out.append(line)
        return "\n".join(out)

    # ── PDF ───────────────────────────────────────────────────────────────
    def to_pdf(self, analysis: dict, output_path: str,
               ai_insights: dict = None, chart_path: str = None) -> bool:
        if not REPORTLAB_OK:
            # Fallback: save HTML and notify
            html = self.to_html(analysis, ai_insights, chart_path)
            html_path = output_path.replace(".pdf", ".html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)
            return False

        try:
            doc = SimpleDocTemplate(output_path, pagesize=A4,
                                    leftMargin=20*mm, rightMargin=20*mm,
                                    topMargin=20*mm, bottomMargin=20*mm)

            styles = getSampleStyleSheet()
            dark_bg = colors.HexColor("#1a1a2e")
            accent  = colors.HexColor("#00b4d8")
            text_c  = colors.HexColor("#e0e0e0")
            gold    = colors.HexColor("#ffd700")
            green_c = colors.HexColor("#06d6a0")
            red_c   = colors.HexColor("#e94560")

            h1s = ParagraphStyle("H1", parent=styles["Heading1"], textColor=accent, fontSize=20, spaceAfter=12)
            h2s = ParagraphStyle("H2", parent=styles["Heading2"], textColor=accent, fontSize=14, spaceBefore=16, spaceAfter=6)
            h3s = ParagraphStyle("H3", parent=styles["Heading3"], textColor=gold,   fontSize=11, spaceBefore=10, spaceAfter=4)
            body= ParagraphStyle("Body", parent=styles["Normal"], textColor=text_c, fontSize=9, spaceAfter=6, leading=14)
            sm  = ParagraphStyle("Sm",   parent=styles["Normal"], textColor=colors.HexColor("#a0a0c0"), fontSize=8, spaceAfter=4)

            sym = analysis.get("symbol","?")
            cp  = analysis.get("current_price", 0)
            dt  = analysis.get("analysis_date", datetime.now().strftime("%Y-%m-%d"))
            td  = analysis.get("trend",{})
            ma  = analysis.get("moving_averages",{})
            vd  = analysis.get("volume",{})
            sup = analysis.get("support_levels",[])
            res = analysis.get("resistance_levels",[])
            scen= analysis.get("scenarios",[])
            obs = analysis.get("key_observations",[])

            story = [
                Paragraph(f"Technical Analysis Report — {sym}", h1s),
                Paragraph(f"Date: {dt} | Price: ${cp:,.2f} | Timeframe: Weekly", sm),
                HRFlowable(width="100%", thickness=1, color=accent),
                Spacer(1, 6*mm),
            ]

            # Chart image
            if chart_path and os.path.exists(chart_path):
                try:
                    story.append(RLImage(chart_path, width=170*mm, height=85*mm))
                    story.append(Spacer(1, 4*mm))
                except Exception:
                    pass

            # Trend section
            story += [
                Paragraph("Trend Analysis", h2s),
                Paragraph(
                    f"<b>Direction:</b> {td.get('direction','?')} &nbsp;|&nbsp; "
                    f"<b>Strength:</b> {td.get('strength','?')} &nbsp;|&nbsp; "
                    f"<b>RSI:</b> {td.get('rsi',50):.0f}",
                    body
                ),
                Paragraph(td.get("description",""), body),
            ]
            for ex in td.get("exhaustion_signals",[]):
                story.append(Paragraph(f"⚠ {ex}", body))

            # S/R table
            story.append(Paragraph("Support & Resistance", h2s))
            tdata = [["Level", "Price", "Strength", "Dist %"]]
            for s in sup[:4]: tdata.append(["Support",  f"${s['price']:,.2f}", s['strength'], f"{s['distance_pct']:.1f}%"])
            for r in res[:4]: tdata.append(["Resistance",f"${r['price']:,.2f}", r['strength'], f"{r['distance_pct']:.1f}%"])
            tbl = Table(tdata, colWidths=[35*mm,40*mm,35*mm,30*mm])
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0,0),(-1,0), colors.HexColor("#0f3460")),
                ("TEXTCOLOR",  (0,0),(-1,0), accent),
                ("TEXTCOLOR",  (0,1),(-1,-1),text_c),
                ("FONTSIZE",   (0,0),(-1,-1), 8),
                ("GRID",       (0,0),(-1,-1), 0.5, colors.HexColor("#2d2d44")),
                ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.HexColor("#16213e"), colors.HexColor("#1a1a2e")]),
            ]))
            story += [tbl, Spacer(1,4*mm)]

            # MA section
            story.append(Paragraph(f"Moving Averages — {ma.get('alignment','?')} Alignment", h2s))
            for p in [20,50,200]:
                m = ma.get(f"ma{p}")
                if m:
                    story.append(Paragraph(
                        f"<b>MA{p}:</b> ${m['value']:,.2f} | {m['price_relation']} | "
                        f"{m['slope']} | {m['distance_pct']:+.1f}%", body
                    ))

            # Volume
            story.append(Paragraph("Volume Analysis", h2s))
            story.append(Paragraph(vd.get("description",""), body))

            # Scenarios
            story.append(Paragraph("Scenario Analysis", h2s))
            for sc in scen:
                col = green_c if sc["type"]=="BULLISH" else (red_c if sc["type"]=="BEARISH" else gold)
                story.append(Paragraph(f"<b>{sc['name']}</b> — {sc['probability']}% probability", h3s))
                story.append(Paragraph(sc["description"], body))
                story.append(Paragraph(
                    f"Targets: {', '.join(f'${t:,.2f}' for t in sc['target_levels'])} | "
                    f"Invalidation: ${sc['invalidation_level']:,.2f}", sm
                ))
                story.append(Spacer(1, 3*mm))

            # Key observations
            story.append(Paragraph("Key Observations", h2s))
            for o in obs:
                story.append(Paragraph(f"• {o}", body))

            # AI insights
            if ai_insights and ai_insights.get("combined"):
                story.append(Paragraph("AI Insights", h2s))
                for para in ai_insights["combined"].split("\n\n"):
                    if para.strip():
                        story.append(Paragraph(para.strip()[:500], body))

            # Disclaimer
            story += [
                Spacer(1,6*mm),
                HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#2d2d44")),
                Paragraph(
                    "This analysis is based purely on technical chart data. Not investment advice.",
                    ParagraphStyle("Disc", parent=styles["Normal"], textColor=colors.HexColor("#888888"), fontSize=7)
                ),
            ]

            doc.build(story)
            return True
        except Exception as e:
            print(f"PDF generation error: {e}")
            return False

    # ── Save helpers ──────────────────────────────────────────────────────
    def save_markdown(self, analysis: dict, output_dir: str,
                      ai_insights: dict = None, chart_path: str = None) -> str:
        sym  = analysis.get("symbol","UNKNOWN").replace("/","_")
        date = analysis.get("analysis_date", datetime.now().strftime("%Y-%m-%d"))
        name = f"{sym}_technical_analysis_{date}.md"
        path = os.path.join(output_dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_markdown(analysis, ai_insights, chart_path))
        return path

    def save_html(self, analysis: dict, output_dir: str,
                  ai_insights: dict = None, chart_path: str = None) -> str:
        sym  = analysis.get("symbol","UNKNOWN").replace("/","_")
        date = analysis.get("analysis_date", datetime.now().strftime("%Y-%m-%d"))
        name = f"{sym}_technical_analysis_{date}.html"
        path = os.path.join(output_dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_html(analysis, ai_insights, chart_path))
        return path

    def save_pdf(self, analysis: dict, output_dir: str,
                 ai_insights: dict = None, chart_path: str = None) -> str:
        sym  = analysis.get("symbol","UNKNOWN").replace("/","_")
        date = analysis.get("analysis_date", datetime.now().strftime("%Y-%m-%d"))
        name = f"{sym}_technical_analysis_{date}.pdf"
        path = os.path.join(output_dir, name)
        ok   = self.to_pdf(analysis, path, ai_insights, chart_path)
        if not ok:
            # Fell back to HTML
            path = path.replace(".pdf", ".html")
        return path

    def save_json(self, analysis: dict, output_dir: str,
                  ai_insights: dict = None) -> str:
        sym  = analysis.get("symbol","UNKNOWN").replace("/","_")
        date = analysis.get("analysis_date", datetime.now().strftime("%Y-%m-%d"))
        name = f"{sym}_technical_analysis_{date}.json"
        path = os.path.join(output_dir, name)
        payload = {"analysis": analysis, "ai_insights": ai_insights or {}}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
        return path
