"""Markdown -> PDF (xhtml2pdf + markdown) with system TTFs and glyph-safe output.

Usage: python md2pdf.py <input.md> <output.pdf> "<Title>" "<Footer label>"

Solves five xhtml2pdf/reportlab pitfalls (see the project playbook):
  1. en/em dashes + arrows must NOT be stripped (reportlab renders them fine);
  2. tables with >=5 columns are silently dropped -> collapse to bullet lists;
  3. colour emoji + box-drawing render as blank boxes -> transliterate;
  4. register real TTF families for typography + footer page numbers;
  5. (author-side) keep tables <=3 columns so columns don't overlap.
"""
import re
import sys

import markdown
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

SRC, OUT = sys.argv[1], sys.argv[2]
TITLE = sys.argv[3] if len(sys.argv) > 3 else "Document"
FOOTER = sys.argv[4] if len(sys.argv) > 4 else TITLE

# --- Fonts. Windows Segoe UI + Consolas. On Linux/macOS swap to DejaVu, e.g.:
#     Body  = /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf (+ -Bold/-Oblique)
#     Mono  = /usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf
_F = "C:/Windows/Fonts"
pdfmetrics.registerFont(TTFont("Body", f"{_F}/segoeui.ttf"))
pdfmetrics.registerFont(TTFont("Body-Bold", f"{_F}/segoeuib.ttf"))
pdfmetrics.registerFont(TTFont("Body-Italic", f"{_F}/segoeuii.ttf"))
pdfmetrics.registerFont(TTFont("Body-BoldItalic", f"{_F}/segoeuiz.ttf"))
pdfmetrics.registerFont(TTFont("Mono", f"{_F}/consola.ttf"))
pdfmetrics.registerFontFamily("Body", normal="Body", bold="Body-Bold",
                              italic="Body-Italic", boldItalic="Body-BoldItalic")
pdfmetrics.registerFontFamily("Mono", normal="Mono", bold="Mono", italic="Mono", boldItalic="Mono")

# GOTCHA 1: do NOT strip characters by TTFontFile.charToGlyph -- it under-reports the
# cmap and wrongly drops en/em dashes, arrows and middots that reportlab renders fine.
# Only true emoji/pictographs (which WOULD be notdef boxes) are dropped/mapped.
_MAP = {
    # status / emoji -> text or a covered symbol
    "\u2705": "\u25CF", "\u274C": "\u2014", "\U0001F512": "(scoped)",
    "\U0001F7E2": "\u25CF", "\U0001F534": "\u25CB", "\U0001F7E1": "\u25D0",
    "\u2714": "\u25CF", "\u2713": "\u25CF", "\u2717": "\u2014", "\u2716": "\u2014",
    "\u2B50": "*", "\U0001F4C4": "-", "\U0001F527": "~", "\u270F\uFE0F": "*", "\u270F": "*",
    "\U0001F550": "~", "\u26A0\uFE0F": "!", "\u26A0": "!", "\u2728": "*",
    "\U0001F916": "[AI]", "\U0001F4CA": "", "\U0001F44D": "+", "\U0001F44E": "-",
    "\uFE0F": "", "\u2699\uFE0F": "*", "\u2699": "*",
    # block triangles (not in Consolas) -> ASCII
    "\u25B6": ">", "\u25C0": "<", "\u25B2": "^", "\u25BC": "v", "\u25B8": ">", "\u25C2": "<",
    # box drawing -> ASCII (would be notdef boxes otherwise)
    "\u2500": "-", "\u2501": "-", "\u2502": "|", "\u2503": "|",
    "\u250C": "+", "\u250D": "+", "\u250E": "+", "\u250F": "+",
    "\u2510": "+", "\u2511": "+", "\u2512": "+", "\u2513": "+",
    "\u2514": "+", "\u2515": "+", "\u2516": "+", "\u2517": "+",
    "\u2518": "+", "\u2519": "+", "\u251A": "+", "\u251B": "+",
    "\u251C": "+", "\u2520": "+", "\u2524": "+", "\u2528": "+",
    "\u252C": "+", "\u2534": "+", "\u253C": "+", "\u2550": "=", "\u2551": "|",
    "\u2554": "+", "\u2557": "+", "\u255A": "+", "\u255D": "+", "\u2560": "+",
    "\u2563": "+", "\u2566": "+", "\u2569": "+", "\u256C": "+",
}


def _cells(line: str):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def collapse_wide_tables(text: str) -> str:
    """GOTCHA 2: xhtml2pdf silently DROPS tables with many columns. Rewrite any table
    with >=5 columns as a compact bulleted list so nothing is lost. Matrix-style tables
    (yes/no cells) become 'row-label -- [columns marked yes]'."""
    lines = text.split("\n")
    out, i = [], 0
    sep = re.compile(r"^\s*\|?[\s:\-|]+\|?\s*$")

    def is_yes(c):
        return ("\u2705" in c) or ("\U0001F512" in c) or (c.strip() == "\u25CF")

    while i < len(lines):
        ln = lines[i]
        if (ln.lstrip().startswith("|") and i + 1 < len(lines)
                and sep.match(lines[i + 1]) and "-" in lines[i + 1]):
            header = _cells(ln)
            if len(header) >= 5:
                j = i + 2
                rows = []
                while j < len(lines) and lines[j].lstrip().startswith("|"):
                    rows.append(_cells(lines[j])); j += 1
                matrix = any(is_yes(c) for r in rows for c in r[1:])
                out.append("")
                out.append(f"**{header[0]}** \u2014 by " + ", ".join(header[1:]) + ":")
                out.append("")
                for r in rows:
                    label = (r[0] if r else "").strip().strip("*").strip()
                    pairs = list(zip(header[1:], r[1:]))
                    if matrix:
                        parts = []
                        for h, c in pairs:
                            if not is_yes(c):
                                continue
                            ann = re.search(r"\(([^)]+)\)", c)
                            if "\U0001F512" in c:
                                parts.append(f"{h} (scoped)")
                            elif ann:
                                parts.append(f"{h} ({ann.group(1)})")
                            else:
                                parts.append(h)
                        joined = ", ".join(parts) if parts else "\u2014"
                        out.append(f"- **{label}** \u2014 {joined}")
                    else:
                        kv = "; ".join(f"{h}: {c}" for h, c in pairs if c and c not in ("\u2014", "-"))
                        out.append(f"- **{label}** \u2014 {kv}")
                out.append("")
                i = j
                continue
        out.append(ln); i += 1
    return "\n".join(out)


def _is_emoji(o: int) -> bool:
    return (0x1F000 <= o <= 0x1FAFF or 0x2600 <= o <= 0x27BF or 0x2B00 <= o <= 0x2BFF
            or 0xE000 <= o <= 0xF8FF or o in (0x200D, 0xFE0E, 0xFE0F))


def sanitize(text: str) -> str:
    """GOTCHA 3: map emoji/box-drawing to covered glyphs; drop only unmapped emoji.
    Everything else (dashes, arrows, middots, accents) passes through untouched."""
    out = []
    for ch in text:
        if ch in _MAP:
            out.append(_MAP[ch]); continue
        o = ord(ch)
        if 0x2500 <= o <= 0x259F:   # any box-drawing char not in _MAP -> line
            out.append("+" if o >= 0x250C else "-"); continue
        if _is_emoji(o):            # unmapped emoji/pictograph would be a notdef box
            continue
        out.append(ch)             # keep everything else (reportlab renders it)
    return "".join(out)


with open(SRC, "r", encoding="utf-8") as f:
    md_text = sanitize(collapse_wide_tables(f.read()))

body_html = markdown.markdown(
    md_text, extensions=["tables", "fenced_code", "sane_lists", "toc"],
    output_format="html5",
)

CSS = """
@page { size: a4 portrait; margin: 1.6cm 1.2cm 2.1cm 1.2cm;
    @frame footer_frame { -pdf-frame-content: footerContent; bottom: 1.0cm;
        margin-left: 1.6cm; margin-right: 1.6cm; height: 1cm; } }
body { font-family: 'Body'; font-size: 10pt; color: #1f2733; line-height: 1.42; }
h1 { font-size: 21pt; color: #0f2c4d; margin: 0 0 2pt 0; }
h2 { font-size: 14pt; color: #0f2c4d; margin: 18pt 0 6pt 0;
    border-bottom: 1.3pt solid #d3a12a; padding-bottom: 3pt; }
h3 { font-size: 11.5pt; color: #14395f; margin: 12pt 0 4pt 0; }
h4 { font-size: 10.3pt; color: #14395f; margin: 9pt 0 3pt 0; }
p { margin: 0 0 6pt 0; }
ul, ol { margin: 0 0 7pt 0; } li { margin: 0 0 2pt 0; }
strong { font-weight: bold; }
hr { border: none; border-top: 0.6pt solid #c9d2dc; margin: 11pt 0; }
a { color: #14395f; text-decoration: none; }
blockquote { background-color: #f4f7fb; border-left: 3pt solid #d3a12a;
    margin: 8pt 0; padding: 5pt 9pt; color: #33414f; }
code { font-family: 'Mono'; font-size: 8.6pt; background-color: #eef2f7; color: #b8003a; padding: 1pt 2pt; }
pre { font-family: 'Mono'; font-size: 7.2pt; background-color: #f4f7fb; border: 0.6pt solid #d7dee7;
    padding: 6pt 8pt; margin: 8pt 0; color: #24303d; line-height: 1.2; }
pre code { background-color: #f4f7fb; color: #24303d; padding: 0; font-size: 7.2pt; }
table { width: 100%; border-collapse: collapse; margin: 8pt 0; font-size: 7.2pt; }
th { background-color: #0f2c4d; color: #ffffff; text-align: left; padding: 2.2pt 3.5pt; border: 0.6pt solid #0f2c4d; }
td { padding: 2.2pt 3.5pt; border: 0.6pt solid #cdd6e0; vertical-align: top; word-wrap: break-word; }
tr:nth-child(even) td { background-color: #f6f8fb; }
#footerContent { color: #8a97a6; font-size: 7.2pt; text-align: center; }
"""

html = (f'<!DOCTYPE html><html><head><meta charset="utf-8"><title>{TITLE}</title>'
        f'<style>{CSS}</style></head><body>'
        f'<div id="footerContent">{FOOTER} &nbsp;|&nbsp; Page <pdf:pagenumber> of <pdf:pagecount></div>'
        f'{body_html}</body></html>')

from xhtml2pdf import pisa

with open(OUT, "wb") as out:
    result = pisa.CreatePDF(src=html, dest=out, encoding="utf-8")
print("FAILED" if result.err else f"OK -> {OUT}")
sys.exit(1 if result.err else 0)
