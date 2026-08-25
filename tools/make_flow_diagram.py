"""Render the end-to-end pipeline diagram into docs/pipeline_flow.png."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

fig, ax = plt.subplots(figsize=(11.6, 13.2))
ax.set_xlim(0, 100); ax.set_ylim(0, 100); ax.axis("off")

PAL = {"blue": ("#eef4fd", "#1a5fb4"), "orange": ("#fdf1e3", "#c2691a"),
       "teal": ("#e6f5f6", "#0f7b8a"), "green": ("#eaf7ee", "#2d7d46"),
       "purple": ("#f4ecfa", "#6b2fa0"), "red": ("#fdecec", "#c0392b"),
       "amber": ("#fdf6e0", "#9a7b12"), "grey": ("#eef0f2", "#4a5560")}

def box(x, y, w, h, title, sub, colour, ts=11.5, ss=8.2, lw=1.8):
    fc, ec = PAL[colour]
    ax.add_patch(FancyBboxPatch((x, y), w, h,
        boxstyle="round,pad=0,rounding_size=0.9",
        facecolor=fc, edgecolor=ec, linewidth=lw, zorder=2))
    if sub:
        ax.text(x+w/2, y+h*0.63, title, ha="center", va="center",
                fontsize=ts, fontweight="bold", color=ec, zorder=3)
        ax.text(x+w/2, y+h*0.26, sub, ha="center", va="center",
                fontsize=ss, color="#3a424b", zorder=3, linespacing=1.4)
    else:
        ax.text(x+w/2, y+h/2, title, ha="center", va="center",
                fontsize=ts, fontweight="bold", color=ec, zorder=3)

def down(x, y0, y1):
    ax.add_patch(FancyArrowPatch((x, y0), (x, y1), arrowstyle="-|>",
        mutation_scale=18, linewidth=2.0, color="#3a424b", zorder=1))

MAIN_X, MAIN_W = 20.0, 44.0
CX = MAIN_X + MAIN_W / 2

box(MAIN_X, 91.0, MAIN_W, 7.4, "Retail Data Sources",
    "Online Retail II  |  1,067,371 invoice lines  |  43 countries", "blue")
down(CX, 91.0, 87.4)
box(MAIN_X, 80.0, MAIN_W, 7.4, "Kafka",
    "topic retail.invoices  |  KRaft, no ZooKeeper", "orange")
down(CX, 80.0, 76.4)
box(MAIN_X, 69.0, MAIN_W, 7.4, "Spark Structured Streaming",
    "event-time windows  |  2h watermark  |  foreachBatch", "teal")
down(CX, 69.0, 65.4)
box(MAIN_X, 58.0, MAIN_W, 7.4, "Data Validation / Transformation",
    "25 rules, ERROR aborts  |  10 cleaning decisions", "green")
down(CX, 58.0, 54.4)
box(MAIN_X, 45.6, MAIN_W, 8.8, "PostgreSQL",
    "snowflake schema: 9 dimensions + fact\nrow-level security  |  idempotent merge on window key",
    "purple", 12.5, 8.2, 2.2)

ax.plot([CX, CX], [45.6, 41.0], color="#3a424b", lw=2.0, zorder=1)
ax.plot([23.0, 61.0], [41.0, 41.0], color="#3a424b", lw=2.0, zorder=1)
for x in (23.0, 61.0):
    ax.add_patch(FancyArrowPatch((x, 41.0), (x, 37.0), arrowstyle="-|>",
        mutation_scale=18, linewidth=2.0, color="#3a424b", zorder=1))

box(6.0, 29.0, 34.0, 8.0, "FastAPI + Docker",
    "13 REST endpoints  |  OpenAPI\n6-service compose stack", "red", 11.5, 8.0)
box(44.0, 29.0, 34.0, 8.0, "Power BI",
    "4 pages  |  48 DAX measures\nreport-level RLS", "amber", 11.5, 8.0)

down(23.0, 29.0, 25.0); down(61.0, 29.0, 25.0)
ax.plot([23.0, 61.0], [25.0, 25.0], color="#3a424b", lw=2.0, zorder=1)
ax.add_patch(FancyArrowPatch((42.0, 25.0), (42.0, 21.0), arrowstyle="-|>",
    mutation_scale=18, linewidth=2.0, color="#3a424b", zorder=1))

box(18.0, 13.0, 48.0, 8.0, "Real-Time Dashboard",
    "live windowed KPIs  |  batch figures reconciled to source", "blue", 12.5, 8.2, 2.2)

ax.add_patch(FancyBboxPatch((70.0, 44.0), 27.0, 42.0,
    boxstyle="round,pad=0,rounding_size=1.2", facecolor="none",
    edgecolor="#8c96a1", linewidth=1.3, linestyle=(0, (6, 4)), zorder=0))
ax.text(83.5, 84.0, "CROSS-CUTTING", ha="center", va="center",
        fontsize=8.4, fontweight="bold", color="#6b7480", zorder=3)

box(72.0, 71.0, 23.0, 9.0, "Prefect",
    "incremental ETL\nwatermark + UPSERT\nretries, scheduling", "grey", 10.5, 7.6)
box(72.0, 59.0, 23.0, 9.0, "Alerting",
    "AMBER / RED gate\nwebhook | email | file", "grey", 10.5, 7.6)
box(72.0, 47.0, 23.0, 9.0, "GitHub Actions",
    "tests | lint | SQL\nimage build", "grey", 10.5, 7.6)

for y in (75.5, 63.5, 51.5):
    ax.plot([72.0, 66.5], [y, y], color="#8c96a1", lw=1.4,
            linestyle=(0, (4, 3)), zorder=1)

fig.savefig("docs/pipeline_flow.png", dpi=200, bbox_inches="tight",
            facecolor="white", pad_inches=0.25)
print("wrote docs/pipeline_flow.png")
