"""The reusable visualization library.

Every chart factory has the same shape:

    fig = chart_name(df, ...) -> plotly.graph_objects.Figure

They all inherit one registered Plotly template, so a change to Theme in
config.yaml restyles the entire dashboard - the point of the library is that
no individual chart carries its own hard-coded styling.

Chart types
-----------
kpi_card_row      headline metric strip
trend_line        time series with optional rolling average and target band
grouped_bar       category comparison, optionally stacked
pareto            bars + cumulative-share line (ABC / 80-20)
waterfall         revenue -> cost -> profit bridge
scatter_bubble    margin vs revenue, size = volume
heatmap           two-dimensional density (RFM grid, month x category)
donut             mix contribution
bullet            actual vs target vs threshold band
data_table        formatted tabular visual
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots

from .config import Theme

TEMPLATE_NAME = "bi_library"

# Currency symbol used by every formatter and axis. Set once from config so a
# single change re-denominates the whole dashboard.
_CURRENCY = "£"


def set_currency(symbol: str) -> None:
    """Set the currency symbol used across all charts and formatters."""
    global _CURRENCY
    _CURRENCY = symbol


def currency() -> str:
    return _CURRENCY


# --------------------------------------------------------------------------
# Theme registration
# --------------------------------------------------------------------------
def register_template(theme: Theme) -> str:
    """Register the shared Plotly template and make it the default."""
    tpl = go.layout.Template()
    tpl.layout = go.Layout(
        font={"family": f"{theme.font}, Inter, Helvetica, Arial, sans-serif",
                  "size": 13, "color": "#243746"},
        title={"font": {"size": 17, "color": theme.primary}, "x": 0.01, "xanchor": "left"},
        paper_bgcolor=theme.background,
        plot_bgcolor=theme.background,
        colorway=theme.categorical,
        margin={"l": 60, "r": 30, "t": 60, "b": 50},
        xaxis={"showgrid": False, "linecolor": theme.grid, "ticks": "outside",
                   "tickcolor": theme.grid, "automargin": True},
        yaxis={"showgrid": True, "gridcolor": theme.grid, "zerolinecolor": theme.grid,
                   "automargin": True},
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.0, "xanchor": "right", "x": 1,
                    "bgcolor": "rgba(0,0,0,0)"},
        hoverlabel={"bgcolor": "white", "bordercolor": theme.grid,
                        "font": {"family": theme.font, "size": 12}},
    )
    pio.templates[TEMPLATE_NAME] = tpl
    pio.templates.default = TEMPLATE_NAME
    return TEMPLATE_NAME


# --------------------------------------------------------------------------
# Formatting helpers - shared by every visual so number formats never diverge
# --------------------------------------------------------------------------
def fmt_currency(v: float, decimals: int = 0) -> str:
    if pd.isna(v):
        return "-"
    sign = "-" if v < 0 else ""
    a = abs(v)
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if a >= div:
            return f"{sign}{_CURRENCY}{a / div:,.1f}{unit}"
    return f"{sign}{_CURRENCY}{a:,.{decimals}f}"


def fmt_percent(v: float, decimals: int = 1) -> str:
    return "-" if pd.isna(v) else f"{v * 100:,.{decimals}f}%"


def fmt_integer(v: float) -> str:
    return "-" if pd.isna(v) else f"{v:,.0f}"


FORMATTERS = {"currency": fmt_currency, "percent": fmt_percent, "integer": fmt_integer}


def format_value(value: float, kind: str = "integer") -> str:
    return FORMATTERS.get(kind, fmt_integer)(value)


# --------------------------------------------------------------------------
# 1. KPI card row
# --------------------------------------------------------------------------
def kpi_card_row(cards: pd.DataFrame, theme: Theme, columns: int | None = None,
                 title: str = "") -> go.Figure:
    """Indicator strip. `cards` needs Metric / Value / Format [/ Comparison]."""
    n = len(cards)
    columns = columns or min(n, 5)
    rows = int(np.ceil(n / columns))

    fig = make_subplots(
        rows=rows, cols=columns,
        specs=[[{"type": "indicator"}] * columns for _ in range(rows)],
        vertical_spacing=0.25 / max(rows, 1),
    )

    for i, rec in enumerate(cards.to_dict("records")):
        r, c = divmod(i, columns)
        kind = rec.get("Format", "integer")
        value = rec["Value"]
        comparison = rec.get("Comparison", np.nan)

        mode = "number+delta" if pd.notna(comparison) else "number"
        indicator = go.Indicator(
            mode=mode,
            value=0 if pd.isna(value) else float(value),
            number={
                "font": {"size": 26, "color": theme.primary},
                "valueformat": ".1%" if kind == "percent" else ",.0f",
                "prefix": _CURRENCY if kind == "currency" else "",
            },
            title={"text": f"<span style='font-size:12px;color:{theme.neutral}'>"
                            f"{rec['Metric'].upper()}</span>"},
        )
        if pd.notna(comparison):
            indicator.delta = {"reference": float(comparison), "relative": True,
                                   "increasing": {"color": theme.positive},
                                   "decreasing": {"color": theme.negative},
                                   "valueformat": ".1%"}
        fig.add_trace(indicator, row=r + 1, col=c + 1)

    fig.update_layout(title=title, height=130 * rows, margin={"l": 10, "r": 10, "t": 50, "b": 10})
    return fig


# --------------------------------------------------------------------------
# 2. Trend line
# --------------------------------------------------------------------------
def trend_line(df: pd.DataFrame, x: str, y: str, theme: Theme,
               series: str | None = None, rolling: str | None = None,
               title: str = "", y_format: str = "currency",
               target: float | None = None) -> go.Figure:
    fig = go.Figure()

    if series:
        for i, (name, grp) in enumerate(df.groupby(series)):
            colour = theme.categorical[i % len(theme.categorical)]
            fig.add_trace(go.Scatter(
                x=grp[x], y=grp[y], name=str(name), mode="lines+markers",
                line={"width": 2.5, "color": colour}, marker={"size": 5},
                hovertemplate=f"<b>{name}</b><br>%{{x}}<br>{y}: %{{y:,.0f}}<extra></extra>",
            ))
    else:
        fig.add_trace(go.Scatter(
            x=df[x], y=df[y], name=y, mode="lines",
            line={"width": 3, "color": theme.primary},
            fill="tozeroy", fillcolor=_rgba(theme.primary, 0.08),
            hovertemplate=f"%{{x}}<br>{y}: %{{y:,.0f}}<extra></extra>",
        ))

    if rolling and rolling in df.columns:
        fig.add_trace(go.Scatter(
            x=df[x], y=df[rolling], name=rolling, mode="lines",
            line={"width": 2, "dash": "dot", "color": theme.accent},
            hovertemplate=f"{rolling}: %{{y:,.0f}}<extra></extra>",
        ))

    if target is not None:
        fig.add_hline(y=target, line={"color": theme.neutral, "dash": "dash", "width": 1.5},
                      annotation_text="Target", annotation_position="top left")

    fig.update_layout(title=title, hovermode="x unified",
                      yaxis={"tickprefix": _CURRENCY if y_format == "currency" else "",
                                 "tickformat": ",.0f"})
    return fig


# --------------------------------------------------------------------------
# 3. Grouped / stacked bar
# --------------------------------------------------------------------------
def grouped_bar(df: pd.DataFrame, x: str, y: str, theme: Theme,
                series: str | None = None, stacked: bool = False,
                title: str = "", horizontal: bool = False,
                value_format: str = "currency", top_n: int | None = None) -> go.Figure:
    data = df.copy()
    if top_n:
        keep = data.groupby(x)[y].sum().nlargest(top_n).index
        data = data[data[x].isin(keep)]

    fig = go.Figure()
    groups = data.groupby(series) if series else [(y, data)]

    cat_axis, val_axis = ("y", "x") if horizontal else ("x", "y")
    hover = (f"<b>%{{{cat_axis}}}</b><br>{y}: %{{{val_axis}:,.0f}}<extra></extra>")

    for i, (name, grp) in enumerate(groups):
        grp = grp.sort_values(y, ascending=horizontal)
        colour = theme.categorical[i % len(theme.categorical)]
        labels = [format_value(v, value_format) for v in grp[y]]
        fig.add_trace(go.Bar(
            x=grp[y] if horizontal else grp[x],
            y=grp[x] if horizontal else grp[y],
            name=str(name), orientation="h" if horizontal else "v",
            marker={"color": colour, "line": {"width": 0}},
            text=labels if not series else None,
            textposition="outside", cliponaxis=False,
            hovertemplate=hover,
        ))

    fig.update_layout(title=title, barmode="stack" if stacked else "group",
                      showlegend=bool(series))
    return fig


# --------------------------------------------------------------------------
# 4. Pareto / ABC
# --------------------------------------------------------------------------
def pareto(df: pd.DataFrame, category: str, value: str, theme: Theme,
           cumulative: str | None = None, top_n: int = 20,
           title: str = "", cutoff: float = 0.80) -> go.Figure:
    data = df.nlargest(top_n, value).copy()
    if cumulative and cumulative in data.columns:
        cum = data[cumulative] * 100
    else:
        cum = data[value].cumsum() / df[value].sum() * 100

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(
        x=data[category], y=data[value], name=value,
        marker={"color": theme.primary},
        hovertemplate=f"<b>%{{x}}</b><br>{value}: %{{y:$,.0f}}<extra></extra>",
    ), secondary_y=False)
    fig.add_trace(go.Scatter(
        x=data[category], y=cum, name="Cumulative %", mode="lines+markers",
        line={"color": theme.accent, "width": 2.5}, marker={"size": 6},
        hovertemplate="Cumulative: %{y:.1f}%<extra></extra>",
    ), secondary_y=True)

    fig.add_hline(y=cutoff * 100, line={"color": theme.negative, "dash": "dash", "width": 1.5},
                  secondary_y=True,
                  annotation_text=f"{cutoff:.0%} cutoff", annotation_position="bottom right")

    fig.update_yaxes(title_text=value, tickprefix=_CURRENCY, secondary_y=False)
    fig.update_yaxes(title_text="Cumulative share", ticksuffix="%", range=[0, 105],
                     showgrid=False, secondary_y=True)
    fig.update_layout(title=title, xaxis={"tickangle": -35})
    return fig


# --------------------------------------------------------------------------
# 5. Waterfall bridge
# --------------------------------------------------------------------------
def waterfall(labels: Sequence[str], values: Sequence[float], theme: Theme,
              measures: Sequence[str] | None = None, title: str = "") -> go.Figure:
    measures = measures or (["relative"] * (len(values) - 1) + ["total"])
    fig = go.Figure(go.Waterfall(
        orientation="v", measure=list(measures), x=list(labels), y=list(values),
        text=[fmt_currency(v) for v in values], textposition="outside",
        connector={"line": {"color": theme.grid}},
        increasing={"marker": {"color": theme.positive}},
        decreasing={"marker": {"color": theme.negative}},
        totals={"marker": {"color": theme.primary}},
        hovertemplate="<b>%{x}</b><br>%{y:,.0f}<extra></extra>",
    ))
    fig.update_layout(title=title, yaxis={"tickprefix": _CURRENCY, "tickformat": ",.0f"},
                      showlegend=False)
    return fig


# --------------------------------------------------------------------------
# 6. Scatter bubble
# --------------------------------------------------------------------------
def scatter_bubble(df: pd.DataFrame, x: str, y: str, size: str, theme: Theme,
                   colour: str | None = None, hover_name: str | None = None,
                   title: str = "", x_format: str = "currency",
                   y_format: str = "percent") -> go.Figure:
    data = df.copy()
    sizes = data[size].clip(lower=0)
    sizeref = 2.0 * sizes.max() / (40.0 ** 2) if sizes.max() else 1

    fig = go.Figure()
    groups = data.groupby(colour) if colour else [("All", data)]
    for i, (name, grp) in enumerate(groups):
        fig.add_trace(go.Scatter(
            x=grp[x], y=grp[y], mode="markers", name=str(name),
            text=grp[hover_name] if hover_name else None,
            marker={"size": grp[size].clip(lower=0), "sizemode": "area", "sizeref": sizeref,
                        "sizemin": 4, "opacity": 0.75,
                        "color": theme.categorical[i % len(theme.categorical)],
                        "line": {"width": 1, "color": "white"}},
            hovertemplate=("<b>%{text}</b><br>" if hover_name else "")
                          + f"{x}: %{{x:,.0f}}<br>{y}: %{{y:.1%}}<extra></extra>",
        ))

    median_y = data[y].median()
    fig.add_hline(y=median_y, line={"color": theme.neutral, "dash": "dot", "width": 1},
                  annotation_text="Median margin", annotation_position="right")
    fig.update_layout(
        title=title,
        xaxis={"title": x, "tickprefix": _CURRENCY if x_format == "currency" else "",
                   "tickformat": ",.0f"},
        yaxis={"title": y, "tickformat": ".0%" if y_format == "percent" else ",.0f"},
    )
    return fig


# --------------------------------------------------------------------------
# 7. Heatmap
# --------------------------------------------------------------------------
def heatmap(df: pd.DataFrame, x: str, y: str, value: str, theme: Theme,
            title: str = "", agg: str = "sum", value_format: str = "integer") -> go.Figure:
    pivot = df.pivot_table(index=y, columns=x, values=value, aggfunc=agg, fill_value=0)
    text = [[format_value(v, value_format) for v in row] for row in pivot.values]

    fig = go.Figure(go.Heatmap(
        z=pivot.values, x=[str(c) for c in pivot.columns], y=[str(i) for i in pivot.index],
        colorscale=[[0, "#FFFFFF"], [0.5, theme.secondary], [1, theme.primary]],
        text=text, texttemplate="%{text}", textfont={"size": 11},
        hovertemplate=f"{y}: %{{y}}<br>{x}: %{{x}}<br>{value}: %{{z:,.0f}}<extra></extra>",
        colorbar={"title": value, "thickness": 12},
    ))
    fig.update_layout(title=title, yaxis={"autorange": "reversed", "showgrid": False},
                      xaxis={"side": "top"})
    return fig


# --------------------------------------------------------------------------
# 8. Donut
# --------------------------------------------------------------------------
def donut(df: pd.DataFrame, names: str, values: str, theme: Theme,
          title: str = "", centre_label: str = "") -> go.Figure:
    total = df[values].sum()
    fig = go.Figure(go.Pie(
        labels=df[names], values=df[values], hole=0.62,
        marker={"colors": theme.categorical, "line": {"color": "white", "width": 2}},
        textinfo="percent", textposition="outside",
        hovertemplate="<b>%{label}</b><br>%{value:,.0f} (%{percent})<extra></extra>",
    ))
    fig.add_annotation(
        text=f"<b>{fmt_currency(total)}</b><br>"
             f"<span style='font-size:11px;color:{theme.neutral}'>{centre_label}</span>",
        x=0.5, y=0.5, showarrow=False, font={"size": 18, "color": theme.primary},
    )
    fig.update_layout(title=title)
    return fig


# --------------------------------------------------------------------------
# 9. Bullet chart
# --------------------------------------------------------------------------
def bullet(metrics: Iterable[dict], theme: Theme, title: str = "") -> go.Figure:
    metrics = list(metrics)
    fig = make_subplots(rows=len(metrics), cols=1,
                        specs=[[{"type": "indicator"}] for _ in metrics],
                        vertical_spacing=0.35 / max(len(metrics), 1))
    for i, m in enumerate(metrics):
        target = m["target"]
        fig.add_trace(go.Indicator(
            mode="number+gauge+delta",
            value=m["actual"],
            delta={"reference": target, "relative": True, "valueformat": ".0%"},
            title={"text": f"<span style='font-size:12px'>{m['label']}</span>"},
            gauge={
                "shape": "bullet",
                "axis": {"range": [0, max(m["actual"], target) * 1.25]},
                "threshold": {"line": {"color": theme.negative, "width": 2.5},
                               "thickness": 0.8, "value": target},
                "steps": [
                    {"range": [0, target * 0.7], "color": "#F2F5F9"},
                    {"range": [target * 0.7, target], "color": "#E1E8F0"},
                ],
                "bar": {"color": theme.primary, "thickness": 0.55},
            },
        ), row=i + 1, col=1)
    fig.update_layout(title=title, height=90 * len(metrics),
                      margin={"l": 140, "r": 40, "t": 60, "b": 20})
    return fig


# --------------------------------------------------------------------------
# 10. Data table
# --------------------------------------------------------------------------
def data_table(df: pd.DataFrame, theme: Theme, title: str = "",
               formats: dict[str, str] | None = None, max_rows: int = 15) -> go.Figure:
    data = df.head(max_rows).copy()
    formats = formats or {}
    cells = []
    for col in data.columns:
        kind = formats.get(col)
        cells.append([format_value(v, kind) if kind else v for v in data[col]])

    fig = go.Figure(go.Table(
        header={"values": [f"<b>{c}</b>" for c in data.columns],
                    "fill_color": theme.primary, "font": {"color": "white", "size": 12},
                    "align": "left", "height": 30},
        cells={"values": cells,
                   "fill_color": [["#FFFFFF", "#F7F9FC"] * (len(data) // 2 + 1)],
                   "align": "left", "height": 26, "font": {"size": 11.5}},
    ))
    fig.update_layout(title=title, height=60 + 27 * (len(data) + 1),
                      margin={"l": 10, "r": 10, "t": 50, "b": 10})
    return fig


def _rgba(hex_colour: str, alpha: float) -> str:
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"
