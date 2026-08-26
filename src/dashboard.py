"""Dashboard assembler.

Composes figures from viz_library into a single self-contained HTML file with
tabbed pages, mirroring the page structure of the Power BI report so the two
deliverables tell the same story:

    Executive | Product | Regional | Customer | Inventory | Data Quality

Plotly.js is inlined once, so the output opens offline from any browser.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import plotly.graph_objects as go
import plotly.io as pio

from .config import Theme

_CSS = """
:root {{
  --primary: {primary}; --secondary: {secondary}; --accent: {accent};
  --grid: {grid}; --neutral: {neutral}; --bg: #F4F6FA;
}}
* {{ box-sizing: border-box; }}
body {{ margin:0; background:var(--bg); color:#243746;
       font-family:'{font}', Inter, Helvetica, Arial, sans-serif; }}
header {{ background:var(--primary); color:#fff; padding:20px 32px; }}
header h1 {{ margin:0; font-size:21px; font-weight:600; letter-spacing:.2px; }}
header p  {{ margin:5px 0 0; font-size:12.5px; opacity:.78; }}
nav {{ display:flex; gap:2px; background:#fff; padding:0 24px;
      border-bottom:1px solid var(--grid); position:sticky; top:0; z-index:20;
      overflow-x:auto; }}
nav button {{ border:0; background:none; padding:13px 18px; font-size:13.5px;
             color:var(--neutral); cursor:pointer; border-bottom:3px solid transparent;
             white-space:nowrap; font-family:inherit; }}
nav button:hover {{ color:var(--primary); background:#FAFCFE; }}
nav button.active {{ color:var(--primary); font-weight:600;
                    border-bottom-color:var(--accent); }}
main {{ padding:20px 24px 48px; }}
.page {{ display:none; }}
.page.active {{ display:block; }}
.grid {{ display:grid; grid-template-columns:repeat(12,1fr); gap:16px; }}
.card {{ background:#fff; border:1px solid var(--grid); border-radius:9px;
        padding:8px 10px; box-shadow:0 1px 3px rgba(20,40,70,.05); overflow:hidden; }}
.w12 {{ grid-column:span 12; }} .w8 {{ grid-column:span 8; }}
.w6  {{ grid-column:span 6;  }} .w4 {{ grid-column:span 4; }}
.note {{ grid-column:span 12; background:#fff; border-left:4px solid var(--accent);
        border-radius:6px; padding:13px 17px; font-size:13px; line-height:1.55;
        color:#41556B; }}
.note b {{ color:var(--primary); }}
footer {{ padding:16px 32px 30px; font-size:11.5px; color:var(--neutral); }}
@media (max-width:1100px) {{ .w8,.w6,.w4 {{ grid-column:span 12; }} }}
"""

_JS = """
function showPage(id, btn) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('nav button').forEach(b => b.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  btn.classList.add('active');
  window.dispatchEvent(new Event('resize'));  // force Plotly to re-measure
}
"""


def _plotlyjs() -> str:
    """Return the Plotly bundle source. Its home moved in Plotly 6."""
    getter = getattr(pio, "get_plotlyjs", None)
    if getter is None:
        from plotly.offline import get_plotlyjs as getter  # Plotly >= 6
    return getter()


class Dashboard:
    """Collects figures into pages and writes one HTML file."""

    def __init__(self, title: str, subtitle: str, theme: Theme):
        self.title = title
        self.subtitle = subtitle
        self.theme = theme
        self.pages: dict[str, list[tuple[go.Figure | str, str]]] = {}

    def add(self, page: str, figure: go.Figure, width: str = "w12") -> Dashboard:
        self.pages.setdefault(page, []).append((figure, width))
        return self

    def add_note(self, page: str, html: str) -> Dashboard:
        self.pages.setdefault(page, []).append((html, "note"))
        return self

    # ------------------------------------------------------------------
    def render(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        nav, body, first = [], [], True
        for i, (page, items) in enumerate(self.pages.items()):
            pid = f"page{i}"
            nav.append(
                f'<button class="{"active" if first else ""}" '
                f"onclick=\"showPage('{pid}', this)\">{page}</button>"
            )

            blocks = []
            for item, width in items:
                if isinstance(item, str):
                    blocks.append(f'<div class="note">{item}</div>')
                    continue
                html = pio.to_html(
                    item, include_plotlyjs=False, full_html=False,
                    config={"displayModeBar": True, "displaylogo": False, "responsive": True},
                )
                blocks.append(f'<div class="card {width}">{html}</div>')

            body.append(
                f'<section class="page {"active" if first else ""}" id="{pid}">'
                f'<div class="grid">{"".join(blocks)}</div></section>'
            )
            first = False

        css = _CSS.format(
            primary=self.theme.primary, secondary=self.theme.secondary,
            accent=self.theme.accent, grid=self.theme.grid,
            neutral=self.theme.neutral, font=self.theme.font,
        )
        stamp = datetime.now().strftime("%d %b %Y %H:%M")

        html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{self.title}</title>
<script>{_plotlyjs()}</script>
<style>{css}</style></head>
<body>
<header><h1>{self.title}</h1><p>{self.subtitle}</p></header>
<nav>{''.join(nav)}</nav>
<main>{''.join(body)}</main>
<footer>Generated {stamp} &middot; Source: AdventureWorksDW2025 &middot;
All figures validated by the Python data-quality suite before rendering.</footer>
<script>{_JS}</script>
</body></html>"""

        path.write_text(html, encoding="utf-8")
        return path
