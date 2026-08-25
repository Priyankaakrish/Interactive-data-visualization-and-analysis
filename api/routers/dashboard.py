"""Self-contained live dashboard served straight from the API.

One HTML document, no build step, no separate front end. It polls the /live
endpoints on a timer and redraws. The batch figures are fetched once - they only
change when the pipeline runs - while the live figures refresh on the interval.
That separation is deliberate and is stated on the page itself.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["dashboard"])

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Retail BI - Live</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
 :root{--bg:#0d1729;--panel:#152238;--line:#24344f;--ink:#e8eef7;--muted:#8fa3bf;
       --accent:#f0a92b;--good:#39b87a;--bad:#e05a5a;--blue:#5b9bd5;}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 "Segoe UI",system-ui,sans-serif}
 header{padding:20px 28px;border-bottom:1px solid var(--line);display:flex;
        align-items:baseline;gap:18px;flex-wrap:wrap}
 h1{margin:0;font-size:19px}
 .pill{font-size:12px;padding:3px 10px;border-radius:20px;background:var(--panel);
       border:1px solid var(--line);color:var(--muted)}
 .pill.live{color:var(--good);border-color:#215c42}
 .pill.down{color:var(--bad);border-color:#5c2121}
 main{padding:22px 28px 40px}
 .grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(190px,1fr))}
 .card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px 18px}
 .card .label{font-size:11px;text-transform:uppercase;letter-spacing:.09em;
              color:var(--muted);margin-bottom:8px}
 .card .value{font-size:25px;font-weight:600;font-variant-numeric:tabular-nums}
 .card .sub{font-size:12px;color:var(--muted);margin-top:5px}
 .panels{display:grid;gap:14px;grid-template-columns:1.4fr 1fr;margin-top:16px}
 @media(max-width:900px){.panels{grid-template-columns:1fr}}
 .panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px 18px}
 .panel h2{margin:0 0 4px;font-size:13px;letter-spacing:.05em;text-transform:uppercase;
           color:var(--muted);font-weight:600}
 .panel .note{font-size:11px;color:var(--muted);margin:0 0 12px}
 table{width:100%;border-collapse:collapse;font-size:13px}
 th{text-align:left;font-weight:600;color:var(--muted);font-size:11px;text-transform:uppercase;
    letter-spacing:.06em;padding:6px 4px;border-bottom:1px solid var(--line)}
 td{padding:6px 4px;border-bottom:1px solid #1b283f;font-variant-numeric:tabular-nums}
 td.num{text-align:right}
 #windows{max-height:240px}
 .panels{align-items:start}
 footer{padding:0 28px 30px;color:var(--muted);font-size:12px}
 .batch{border-left:3px solid var(--blue)} .live{border-left:3px solid var(--accent)}
 .sec{font-size:12px;letter-spacing:.09em;text-transform:uppercase;color:var(--muted);margin:0 0 10px}
</style></head><body>
<header><h1>Online Retail II &mdash; Live</h1>
 <span class="pill" id="status">connecting</span>
 <span class="pill" id="updated">&mdash;</span></header>
<main>
 <h2 class="sec">Streaming &mdash; approximate, moving</h2>
 <div class="grid" id="liveCards"></div>
 <div class="panels">
  <div class="panel"><h2>Windowed revenue</h2>
   <p class="note">Event-time windows written by Spark, most recent last</p>
   <canvas id="windows" height="120"></canvas></div>
  <div class="panel"><h2>Live revenue by country</h2>
   <p class="note">Top 8 by streamed revenue</p>
   <table id="countries"><thead><tr><th>Country</th><th class="num">Revenue</th>
   <th class="num">Lines</th></tr></thead><tbody></tbody></table></div>
 </div>
 <h2 class="sec" style="margin-top:26px">Batch &mdash; reconciled, final</h2>
 <div class="grid" id="batchCards"></div>
</main>
<footer>Batch figures come from the reconciled warehouse and change only when the
pipeline runs. Live figures come from the Spark stream and are approximate by
design. They answer different questions and are shown apart on purpose.</footer>
<script>
const money=n=>"\u00a3"+(n||0).toLocaleString("en-GB",{minimumFractionDigits:2,maximumFractionDigits:2});
const num=n=>(n||0).toLocaleString("en-GB");
const pct=n=>(n||0).toFixed(2)+"%";
function cards(el,items){document.getElementById(el).innerHTML=items.map(c=>
 '<div class="card '+(c.cls||'')+'"><div class="label">'+c.label+'</div><div class="value">'+
 c.value+'</div>'+(c.sub?'<div class="sub">'+c.sub+'</div>':'')+'</div>').join("");}
let chart;
function drawWindows(rows){
 const d=rows.slice().reverse();
 const labels=d.map(r=>r.window_start.replace("T"," ").slice(5,16));
 const values=d.map(r=>r.gross_revenue);
 if(!chart){chart=new Chart(document.getElementById("windows"),{type:"line",
  data:{labels,datasets:[{data:values,borderColor:"#f0a92b",
   backgroundColor:"rgba(240,169,43,.15)",borderWidth:2,fill:true,pointRadius:0,tension:.3}]},
  options:{plugins:{legend:{display:false}},scales:{
   x:{ticks:{color:"#8fa3bf",maxTicksLimit:8,font:{size:10}},grid:{color:"#1b283f"}},
   y:{ticks:{color:"#8fa3bf",font:{size:10},callback:v=>"\u00a3"+(v/1000).toFixed(0)+"k"},
      grid:{color:"#1b283f"}}},animation:false,maintainAspectRatio:false}});}
 else{chart.data.labels=labels;chart.data.datasets[0].data=values;chart.update("none");}}
async function get(p){const r=await fetch(p,{cache:"no-store"});
 if(!r.ok)throw new Error(p);return r.json();}
async function refreshLive(){const b=document.getElementById("status");
 try{const[s,c,w]=await Promise.all([get("/api/v1/live/summary"),
  get("/api/v1/live/countries?limit=8"),get("/api/v1/live/windows?limit=40")]);
  if(!s.streaming){b.textContent="stream detached";b.className="pill down";}
  else{b.textContent="streaming";b.className="pill live";}
  cards("liveCards",[
   {label:"Gross revenue",value:money(s.gross_revenue),cls:"live"},
   {label:"Net revenue",value:money(s.net_revenue),cls:"live"},
   {label:"Returns",value:money(s.returns_value),sub:pct(s.return_rate_pct)+" of gross",cls:"live"},
   {label:"Orders",value:num(s.order_count),sub:"approximate distinct",cls:"live"},
   {label:"Lines",value:num(s.line_count),cls:"live"},
   {label:"Units",value:num(s.units_sold),cls:"live"}]);
  document.querySelector("#countries tbody").innerHTML=c.map(r=>
   "<tr><td>"+r.country+"</td><td class='num'>"+money(r.gross_revenue)+
   "</td><td class='num'>"+num(r.line_count)+"</td></tr>").join("")||
   "<tr><td colspan='3' style='color:#8fa3bf'>no windows yet</td></tr>";
  if(w.length)drawWindows(w);
  document.getElementById("updated").textContent="updated "+new Date().toLocaleTimeString("en-GB");
 }catch(e){b.textContent="api unreachable";b.className="pill down";}}
async function loadBatch(){try{const k=await get("/api/v1/kpi/summary");
 cards("batchCards",[
  {label:"Gross revenue",value:money(k.gross_revenue),cls:"batch"},
  {label:"Orders",value:num(k.orders),cls:"batch"},
  {label:"Avg order",value:money(k.avg_order_value),cls:"batch"},
  {label:"Units sold",value:num(k.units_sold),cls:"batch"},
  {label:"Customers",value:num(k.identified_customers),
   sub:pct(k.guest_checkout_share_pct*100)+" guest",cls:"batch"},
  {label:"Return rate",value:pct(k.return_rate_pct*100),cls:"batch"}]);
 }catch(e){}}
loadBatch();refreshLive();setInterval(refreshLive,5000);
</script></body></html>
"""


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False,
            summary="Live dashboard")
def dashboard() -> HTMLResponse:
    """Serve the live dashboard.

    Excluded from the OpenAPI schema deliberately - it is a page, not an API
    contract, and listing it in Swagger would imply a machine-readable response.
    """
    return HTMLResponse(content=PAGE)

