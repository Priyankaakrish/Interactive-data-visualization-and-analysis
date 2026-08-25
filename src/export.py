"""Excel and CSV export layer.

Produces the stakeholder workbook: one formatted sheet per KPI area, with
frozen headers, autofilters, number formats and column widths applied so the
file is usable the moment it is opened - no manual formatting step.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .config import Theme

# Column-name -> Excel number format. Matched case-sensitively on exact name,
# then by suffix, so new KPI columns pick up sensible formats automatically.
EXACT_FORMATS = {
    "Revenue": "$#,##0", "Cost": "$#,##0", "GrossProfit": "$#,##0",
    "Monetary": "$#,##0", "InventoryValue": "$#,##0", "COGSLTM": "$#,##0",
    "AvgOrderValue": "$#,##0.00", "UnitCost": "$#,##0.00", "ListPrice": "$#,##0.00",
    "StandardCost": "$#,##0.00", "RevenuePerCustomer": "$#,##0",
    "GrossMarginPct": "0.0%", "RevenueShare": "0.0%", "CumulativeRevenueShare": "0.0%",
    "MoMGrowthPct": "0.0;[Red]-0.0", "YoYGrowthPct": "0.0;[Red]-0.0",
    "DaysOfSupply": "#,##0.0", "InventoryTurnover": "#,##0.00",
    "FailRatePct": "0.00",
}
SUFFIX_FORMATS = {"Pct": "0.0%", "Count": "#,##0", "Units": "#,##0",
                  "Days": "#,##0", "Value": "$#,##0"}


def _format_for(column: str) -> str:
    if column in EXACT_FORMATS:
        return EXACT_FORMATS[column]
    for suffix, fmt in SUFFIX_FORMATS.items():
        if column.endswith(suffix):
            return fmt
    return "#,##0.00"


def write_workbook(sheets: dict[str, pd.DataFrame], path: str | Path,
                   theme: Theme) -> Path:
    """Write a formatted multi-sheet workbook."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with pd.ExcelWriter(path, engine="xlsxwriter",
                        datetime_format="yyyy-mm-dd", date_format="yyyy-mm-dd") as xl:
        book = xl.book
        header_fmt = book.add_format({
            "bold": True, "font_color": "white", "bg_color": theme.primary,
            "border": 0, "align": "left", "valign": "vcenter", "text_wrap": True,
        })
        title_fmt = book.add_format({"bold": True, "font_size": 13,
                                     "font_color": theme.primary})

        for name, df in sheets.items():
            sheet_name = name[:31]
            df = df.copy()
            # Excel cannot store timezone-aware datetimes.
            for c in df.select_dtypes(include=["datetimetz"]).columns:
                df[c] = df[c].dt.tz_localize(None)

            df.to_excel(xl, sheet_name=sheet_name, index=False, startrow=2)
            ws = xl.sheets[sheet_name]

            ws.write(0, 0, name.replace("_", " ").title(), title_fmt)
            for col_idx, col in enumerate(df.columns):
                ws.write(2, col_idx, col, header_fmt)
                width = max(len(str(col)) + 3,
                            min(34, int(df[col].astype(str).str.len().max() or 10) + 2))
                numeric = pd.api.types.is_numeric_dtype(df[col])
                fmt = book.add_format({"num_format": _format_for(col)}) if numeric else None
                ws.set_column(col_idx, col_idx, width, fmt)

            ws.freeze_panes(3, 0)
            if len(df):
                ws.autofilter(2, 0, 2 + len(df), max(len(df.columns) - 1, 0))

            # Conditional data bars make the primary measure scannable.
            for measure in ("Revenue", "Monetary", "InventoryValue", "FailedRows"):
                if measure in df.columns and len(df):
                    ci = df.columns.get_loc(measure)
                    ws.conditional_format(3, ci, 2 + len(df), ci, {
                        "type": "data_bar", "bar_color": theme.secondary,
                        "bar_solid": False,
                    })
                    break
    return path


def write_csvs(frames: dict[str, pd.DataFrame], directory: str | Path) -> list[Path]:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, df in frames.items():
        p = directory / f"{name}.csv"
        df.to_csv(p, index=False)
        written.append(p)
    return written
