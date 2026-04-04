"""HTML report generation and ticker health reporting."""

import html as _html_mod
import json
from datetime import datetime


def _esc(val):
    """Escape a value for safe HTML insertion."""
    return _html_mod.escape(str(val)) if val else ""

import pandas as pd

from reporting.calcs import (
    PERIOD_COLS, ANNUAL_COLS, FUND_COLS, VAL_COLS,
    FUND_PCT_COLS, FUND_DISPLAY_NAMES,
    get_html_color, format_mkt_cap, format_price,
)
from logging_utils import get_logger

logger = get_logger("perf_html")


def build_ticker_health_data(df_unique, yf_tickers, ticker_map, all_results, all_fundamentals):
    """Build health report data from already-collected information (no extra API calls)."""
    no_price = [ticker_map[t] for t in yf_tickers if t not in all_results]

    no_fundamentals = []
    for yf_t in yf_tickers:
        fund = all_fundamentals.get(yf_t, {})
        if all(v is None for v in fund.values()):
            no_fundamentals.append(ticker_map.get(yf_t, yf_t))

    missing_exchange = []
    missing_company_name = []
    for _, row in df_unique.iterrows():
        ticker = str(row.get("Ticker", "")).strip()
        if not ticker or ticker == "#N/A":
            continue
        exchange = str(row.get("Exchange", "")).strip()
        if not exchange or exchange == "nan":
            missing_exchange.append(ticker)
        company = str(row.get("Company Name", "")).strip()
        if not company or company == "nan":
            missing_company_name.append(ticker)

    total = len(no_price) + len(no_fundamentals) + len(missing_exchange) + len(missing_company_name)
    return {
        "no_price": sorted(no_price),
        "no_fundamentals": sorted(no_fundamentals),
        "missing_exchange": sorted(missing_exchange),
        "missing_company_name": sorted(missing_company_name),
        "total_issues": total,
    }


def generate_health_html(health_data):
    """Generate collapsible HTML section for ticker health report."""
    total = health_data["total_issues"]
    if total == 0:
        badge_class = "health-badge ok"
        badge_text = "All clear"
    elif health_data["no_price"]:
        badge_class = "health-badge critical"
        badge_text = f"{total} issues"
    else:
        badge_class = "health-badge warning"
        badge_text = f"{total} issues"

    def ticker_list_html(tickers, max_show=50):
        if not tickers:
            return "<p>None</p>"
        items = ", ".join(tickers[:max_show])
        extra = f" ... and {len(tickers) - max_show} more" if len(tickers) > max_show else ""
        return f"<p>{items}{extra}</p>"

    sections = []

    if health_data["no_price"]:
        sections.append(f'''
  <div class="health-section critical">
    <h4>No Price Data — Potential Delistings ({len(health_data["no_price"])})</h4>
    {ticker_list_html(health_data["no_price"])}
  </div>''')

    if health_data["no_fundamentals"]:
        sections.append(f'''
  <div class="health-section warning">
    <h4>No Fundamental Data ({len(health_data["no_fundamentals"])})</h4>
    {ticker_list_html(health_data["no_fundamentals"])}
  </div>''')

    if health_data["missing_exchange"]:
        sections.append(f'''
  <div class="health-section info">
    <h4>Missing Exchange ({len(health_data["missing_exchange"])})</h4>
    {ticker_list_html(health_data["missing_exchange"])}
  </div>''')

    if health_data["missing_company_name"]:
        sections.append(f'''
  <div class="health-section info">
    <h4>Missing Company Name ({len(health_data["missing_company_name"])})</h4>
    {ticker_list_html(health_data["missing_company_name"])}
  </div>''')

    return f'''<details class="ticker-health">
  <summary>Ticker Health Report <span class="{badge_class}">{badge_text}</span></summary>
{"".join(sections)}
</details>'''


def write_html_report(seg_df, html_path, report_title, health_data=None):
    """Write a standalone HTML performance report for a segment DataFrame."""
    html_info_cols = ["Ticker", "Company Name"] + VAL_COLS + ["Sector (JP)", "Subsector (JP)", "Core", "YF Sector", "YF Industry", "Country (ISO)", "Exchange"]
    html_fund_cols = FUND_COLS

    html_rows = []
    for _, row in seg_df.iterrows():
        cells = []
        currency = row.get("_currency", "")
        val = row.get("Ticker", "")
        if pd.isna(val) or str(val) == "nan":
            val = ""
        cells.append(f'<td class="info">{_esc(val)}</td>')
        val = row.get("Company Name", "")
        if pd.isna(val) or str(val) == "nan":
            val = ""
        cells.append(f'<td class="info">{_esc(val)}</td>')
        for col in VAL_COLS:
            v = row.get(col)
            if col == "Price":
                cells.append(f'<td class="fund">{format_price(v, currency)}</td>')
            elif col == "% 52Wk Hi":
                if v is not None and not pd.isna(v):
                    pct = float(v)
                    bg = get_html_color(pct - 100)  # color relative to 100%
                    cells.append(f'<td class="fund" style="background-color:{bg}">{pct:.1f}%</td>')
                else:
                    cells.append('<td class="fund na">-</td>')
            else:
                cells.append(f'<td class="fund">{format_mkt_cap(v, currency)}</td>')
        for col in ["Sector (JP)", "Subsector (JP)", "Core", "YF Sector", "YF Industry", "Country (ISO)", "Exchange"]:
            val = row.get(col, "")
            if pd.isna(val) or str(val) == "nan":
                val = ""
            cells.append(f'<td class="info">{_esc(val)}</td>')
        for col in PERIOD_COLS:
            val = row.get(col)
            if val is not None and not pd.isna(val):
                bg = get_html_color(val)
                text_color = "#8B0000" if val < 0 else "#006400" if val > 0 else "#333"
                cells.append(f'<td class="ret" style="background-color:{bg};color:{text_color}">{val:.1f}</td>')
            else:
                cells.append('<td class="ret na">-</td>')
        for col in ANNUAL_COLS:
            val = row.get(col)
            if val is not None and not pd.isna(val):
                bg = get_html_color(val)
                text_color = "#8B0000" if val < 0 else "#006400" if val > 0 else "#333"
                cells.append(f'<td class="ret annual-col" style="background-color:{bg};color:{text_color}">{val:.1f}</td>')
            else:
                cells.append('<td class="ret annual-col na">-</td>')
        for col in html_fund_cols:
            val = row.get(col)
            needs_asterisk = False
            if col == "Rev Grw" and not row.get("_is_ttm_rev", False):
                needs_asterisk = True
            elif col == "EPS Grw" and not row.get("_is_ttm_eps", False):
                needs_asterisk = True
            if val is not None and not pd.isna(val):
                if col in FUND_PCT_COLS:
                    asterisk = "*" if needs_asterisk else ""
                    try:
                        num_val = float(val)
                        bg = get_html_color(num_val)
                        text_color = "#8B0000" if num_val < 0 else "#006400" if num_val > 0 else "#333"
                        cells.append(f'<td class="fund" style="background-color:{bg};color:{text_color}">{num_val:.1f}{asterisk}</td>')
                    except (TypeError, ValueError):
                        cells.append(f'<td class="fund">{_esc(val)}{asterisk}</td>')
                else:
                    try:
                        cells.append(f'<td class="fund">{float(val):.1f}x</td>')
                    except (TypeError, ValueError):
                        cells.append(f'<td class="fund">{_esc(val)}</td>')
            else:
                cells.append('<td class="fund na">-</td>')
        raw_mktcap = row.get("Mkt Cap")
        mktcap_attr = f' data-mktcap="{float(raw_mktcap)}"' if raw_mktcap is not None and not pd.isna(raw_mktcap) else ' data-mktcap="0"'
        html_rows.append(f"<tr{mktcap_attr}>" + "".join(cells) + "</tr>")

    def split_header(name):
        idx = name.find(" (")
        if idx > 0:
            return name[:idx] + "<br>" + name[idx+1:]
        return name

    html_info_display = [split_header(FUND_DISPLAY_NAMES.get(c, c)) for c in html_info_cols]
    info_headers = "".join(f"<th>{c}</th>" for c in html_info_display)
    period_headers = "".join(f'<th>{c}<br>%</th>' for c in PERIOD_COLS)
    annual_headers = "".join(f'<th class="annual-col">{c}<br>%</th>' for c in ANNUAL_COLS)
    fund_display = []
    for c in html_fund_cols:
        name = split_header(FUND_DISPLAY_NAMES.get(c, c))
        if c in FUND_PCT_COLS:
            name += "<br>%"
        fund_display.append(name)
    fund_headers = "".join(f'<th class="fund-hdr">{d}</th>' for d in fund_display)
    header_cells = info_headers + period_headers + annual_headers + fund_headers

    num_info = len(html_info_cols)
    val_offset = len(VAL_COLS)
    filterable_cols = {0: "Ticker", 1: "Company Name",
                       2 + val_offset: "Sector (JP)", 3 + val_offset: "Subsector (JP)",
                       4 + val_offset: "Core",
                       5 + val_offset: "YF Sector", 6 + val_offset: "YF Industry",
                       7 + val_offset: "Country (ISO)", 8 + val_offset: "Exchange"}
    col_unique_values = {}
    for col_idx, col_name in filterable_cols.items():
        if col_name in seg_df.columns:
            vals = sorted(set(
                str(v).strip() for v in seg_df[col_name] if pd.notna(v) and str(v).strip() and str(v).strip() != "nan"
            ), key=str.lower)
            col_unique_values[col_idx] = vals
    filter_options_json = json.dumps(col_unique_values)

    mktcap_col_idx = 2  # Mkt Cap is first in VAL_COLS, after Ticker and Company Name
    filter_cells_list = []
    for i in range(num_info):
        if i in filterable_cols:
            filter_cells_list.append(
                f'<th><div class="ms-dropdown" data-col="{i}">'
                f'<button class="ms-trigger" type="button">All &#9660;</button>'
                f'<div class="ms-panel" style="display:none;"></div>'
                f'</div></th>')
        elif i == mktcap_col_idx:
            filter_cells_list.append(
                '<th><input type="number" id="minMktCap" placeholder="Min $B" step="0.1" '
                'style="width:70px;font-size:10px;padding:2px 4px;border:1px solid #6a8aa8;'
                'border-radius:3px;background:#2c3e50;color:#fff;text-align:center;" '
                'title="Minimum market cap in USD $B (e.g. 1 = $1B)"></th>')
        else:
            filter_cells_list.append("<th></th>")
    filter_cells = "".join(filter_cells_list)
    filter_cells += "".join("<th></th>" for _ in PERIOD_COLS)
    filter_cells += "".join('<th class="annual-col"></th>' for _ in ANNUAL_COLS)
    filter_cells += "".join("<th></th>" for _ in html_fund_cols)

    timestamp = datetime.now().strftime("%B %d, %Y %H:%M")
    health_html = generate_health_html(health_data) if health_data else ""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{report_title}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
  h1 {{ color: #2c3e50; font-size: 22px; }}
  p.timestamp {{ color: #888; font-size: 12px; margin-bottom: 15px; }}
  .table-wrapper {{ overflow-x: auto; max-height: 85vh; overflow-y: auto; }}
  table {{ border-collapse: collapse; font-size: 11px; table-layout: fixed; }}
  thead {{ position: sticky; top: 0; z-index: 2; }}
  th {{ background: #2c3e50; color: white; padding: 4px 3px; text-align: center; border: 1px solid #1a252f;
       font-weight: 600; cursor: pointer; user-select: none; overflow: hidden; resize: horizontal;
       font-size: 10px; line-height: 1.2; }}
  th:hover {{ background: #3e5871; }}
  th.sort-asc::after {{ content: " ▲"; font-size: 8px; }}
  th.sort-desc::after {{ content: " ▼"; font-size: 8px; }}
  td {{ padding: 3px 4px; border: 1px solid #ddd; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  td.info {{ text-align: left; background: #fff; }}
  td.ret {{ text-align: center; font-weight: 500; }}
  td.fund {{ text-align: center; font-weight: 500; }}
  td.na {{ color: #bbb; background: #fafafa; }}
  th.fund-hdr {{ background: #1a5276; }}
  tr:hover td {{ box-shadow: inset 0 0 0 999px rgba(30, 90, 160, 0.12); outline: 1px solid rgba(30, 90, 160, 0.25); }}
  tr:nth-child(even) td.info {{ background: #f9f9f9; }}
  .row-count {{ margin-bottom: 8px; font-size: 12px; color: #888; }}
  .filter-row th {{ background: #3e5871; padding: 4px; }}
  .ms-dropdown {{ position: relative; display: inline-block; }}
  .ms-trigger {{ background: #fff; border: 1px solid #aaa; border-radius: 3px; padding: 2px 6px; font-size: 10px;
    cursor: pointer; max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: block; }}
  .ms-trigger:hover {{ background: #f0f0f0; }}
  .ms-panel {{ position: fixed; width: 220px; max-height: 320px; background: #fff; border: 1px solid #ccc;
    border-radius: 4px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); z-index: 1000; display: flex; flex-direction: column; }}
  .ms-search {{ width: calc(100% - 12px); margin: 6px; padding: 4px 6px; font-size: 11px; border: 1px solid #ccc; border-radius: 3px; color: #333; }}
  .ms-actions {{ display: flex; gap: 8px; padding: 0 8px 4px; font-size: 10px; }}
  .ms-actions a {{ color: #2c7be5; cursor: pointer; text-decoration: none; }}
  .ms-actions a:hover {{ text-decoration: underline; }}
  .ms-list {{ overflow-y: auto; max-height: 240px; padding: 0 6px 6px; }}
  .ms-list label {{ display: block; padding: 2px 0; font-size: 11px; cursor: pointer; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: #333; }}
  .ms-list label:hover {{ background: #f0f4ff; }}
  .ms-list input[type="checkbox"] {{ margin-right: 4px; }}
  .footnote {{ font-size: 11px; color: #888; margin-top: 8px; font-style: italic; }}
  .ticker-health {{ margin-top: 20px; background: #fff; border: 1px solid #ddd; border-radius: 6px; padding: 10px 15px; }}
  .ticker-health summary {{ font-size: 14px; font-weight: 600; color: #2c3e50; cursor: pointer; }}
  .health-badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; margin-left: 8px; }}
  .health-badge.ok {{ background: #d4edda; color: #155724; }}
  .health-badge.warning {{ background: #fff3cd; color: #856404; }}
  .health-badge.critical {{ background: #f8d7da; color: #721c24; }}
  .health-section {{ margin: 10px 0; padding: 8px 12px; border-radius: 4px; }}
  .health-section h4 {{ margin: 0 0 4px 0; font-size: 12px; }}
  .health-section p {{ margin: 4px 0; font-size: 11px; word-break: break-all; }}
  .health-section.critical {{ background: #f8d7da; border-left: 3px solid #dc3545; }}
  .health-section.warning {{ background: #fff3cd; border-left: 3px solid #ffc107; }}
  .health-section.info {{ background: #f0f0f0; border-left: 3px solid #999; }}
  .annual-col {{ }}
  .annual-col.hidden {{ display: none; }}
  .toggle-annual {{ background: #2c3e50; color: white; border: 1px solid #1a252f; border-radius: 4px;
    padding: 4px 10px; font-size: 11px; cursor: pointer; margin-left: 10px; }}
  .toggle-annual:hover {{ background: #3e5871; }}
</style>
</head>
<body>
<h1>{report_title}</h1>
<p class="timestamp">Generated: {timestamp}
  <button class="toggle-annual" onclick="toggleAnnual()">Hide Annual Returns</button>
</p>
<p class="footnote">* Value reflects quarterly YoY growth (yfinance) rather than TTM YoY (Finnhub). TTM YoY data was not available for this ticker.</p>
<div class="row-count" id="rowCount"></div>
<div class="table-wrapper">
<table>
<thead>
<tr>{header_cells}</tr>
<tr class="filter-row">{filter_cells}</tr>
</thead>
<tbody>
{"".join(html_rows)}
</tbody>
</table>
</div>
<script>
var FILTER_OPTIONS = {filter_options_json};
var selectedFilters = {{}};

function updateCount() {{
  var tbody = document.querySelector("tbody");
  var visible = tbody.querySelectorAll("tr:not([style*='display: none'])").length;
  var total = tbody.querySelectorAll("tr").length;
  document.getElementById("rowCount").textContent = visible + " of " + total + " companies";
}}

function updateTriggerLabel(colIdx, checked, total) {{
  var dd = document.querySelector('.ms-dropdown[data-col="' + colIdx + '"]');
  if (!dd) return;
  var btn = dd.querySelector(".ms-trigger");
  if (checked === total || checked === 0) btn.textContent = "All \u25BC";
  else if (checked === 1) {{
    var boxes = dd.querySelectorAll('.ms-list input[type="checkbox"]:checked');
    btn.textContent = boxes[0].parentElement.textContent.trim() + " \u25BC";
  }} else btn.textContent = checked + " sel \u25BC";
}}

function applyFilters() {{
  var minCapInput = document.getElementById("minMktCap");
  var minCap = minCapInput ? parseFloat(minCapInput.value) : NaN;
  document.querySelectorAll("tbody tr").forEach(function(row) {{
    var show = true;
    for (var colIdx in selectedFilters) {{
      var allowed = selectedFilters[colIdx];
      if (!allowed || allowed.size === 0) continue;
      var cellText = row.children[parseInt(colIdx)].textContent.trim().toLowerCase();
      if (!allowed.has(cellText)) {{ show = false; break; }}
    }}
    if (show && !isNaN(minCap)) {{
      var mc = parseFloat(row.getAttribute("data-mktcap") || "0");
      if (mc < minCap * 1e9) show = false;
    }}
    row.style.display = show ? "" : "none";
  }});
  updateCount();
}}

function syncFilter(colIdx, panel) {{
  var boxes = panel.querySelectorAll('.ms-list input[type="checkbox"]');
  var checked = new Set();
  var total = boxes.length;
  var checkedCount = 0;
  boxes.forEach(function(cb) {{
    if (cb.checked) {{ checked.add(cb.value.toLowerCase()); checkedCount++; }}
  }});
  if (checkedCount === total) delete selectedFilters[colIdx];
  else selectedFilters[colIdx] = checked;
  updateTriggerLabel(colIdx, checkedCount, total);
  applyFilters();
}}

function buildPanel(panel, colIdx) {{
  if (panel.dataset.built) return;
  panel.dataset.built = "1";
  var vals = FILTER_OPTIONS[colIdx] || [];
  var searchInput = document.createElement("input");
  searchInput.className = "ms-search";
  searchInput.placeholder = "Search...";
  panel.appendChild(searchInput);

  var actions = document.createElement("div");
  actions.className = "ms-actions";
  var selAll = document.createElement("a");
  selAll.textContent = "Select All";
  var clearAll = document.createElement("a");
  clearAll.textContent = "Clear";
  actions.appendChild(selAll);
  actions.appendChild(clearAll);
  panel.appendChild(actions);

  var list = document.createElement("div");
  list.className = "ms-list";
  vals.forEach(function(v) {{
    var lbl = document.createElement("label");
    var cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = true;
    cb.value = v;
    lbl.appendChild(cb);
    lbl.appendChild(document.createTextNode(v));
    list.appendChild(lbl);
    cb.addEventListener("change", function() {{ syncFilter(colIdx, panel); }});
  }});
  panel.appendChild(list);

  searchInput.addEventListener("input", function() {{
    var q = searchInput.value.trim().toLowerCase();
    list.querySelectorAll("label").forEach(function(lbl) {{
      lbl.style.display = lbl.textContent.toLowerCase().indexOf(q) !== -1 ? "" : "none";
    }});
  }});
  selAll.addEventListener("click", function(e) {{
    e.preventDefault();
    list.querySelectorAll('input[type="checkbox"]').forEach(function(cb) {{ cb.checked = true; }});
    syncFilter(colIdx, panel);
  }});
  clearAll.addEventListener("click", function(e) {{
    e.preventDefault();
    list.querySelectorAll('input[type="checkbox"]').forEach(function(cb) {{ cb.checked = false; }});
    syncFilter(colIdx, panel);
  }});
}}

function positionPanel(panel, trigger) {{
  var rect = trigger.getBoundingClientRect();
  panel.style.top = rect.bottom + 2 + "px";
  panel.style.left = rect.left + "px";
}}

document.querySelectorAll(".ms-dropdown").forEach(function(dd) {{
  var trigger = dd.querySelector(".ms-trigger");
  var panel = dd.querySelector(".ms-panel");
  var colIdx = dd.getAttribute("data-col");
  trigger.addEventListener("click", function(e) {{
    e.stopPropagation();
    document.querySelectorAll(".ms-panel").forEach(function(p) {{
      if (p !== panel) p.style.display = "none";
    }});
    buildPanel(panel, colIdx);
    var isOpen = panel.style.display !== "none";
    panel.style.display = isOpen ? "none" : "flex";
    if (!isOpen) {{
      positionPanel(panel, trigger);
      panel.querySelector(".ms-search").focus();
    }}
  }});
}});

var minCapEl = document.getElementById("minMktCap");
if (minCapEl) {{
  minCapEl.addEventListener("input", function() {{ applyFilters(); }});
}}

document.addEventListener("click", function(e) {{
  if (!e.target.closest(".ms-dropdown")) {{
    document.querySelectorAll(".ms-panel").forEach(function(p) {{ p.style.display = "none"; }});
  }}
}});

document.querySelector(".table-wrapper").addEventListener("scroll", function() {{
  document.querySelectorAll(".ms-dropdown").forEach(function(dd) {{
    var panel = dd.querySelector(".ms-panel");
    if (panel.style.display !== "none") {{
      positionPanel(panel, dd.querySelector(".ms-trigger"));
    }}
  }});
}});

document.querySelectorAll("thead tr:first-child th").forEach(function(th, colIdx) {{
  th.addEventListener("click", function() {{
    var table = th.closest("table");
    var tbody = table.querySelector("tbody");
    var rows = Array.from(tbody.querySelectorAll("tr"));
    var asc = !th.classList.contains("sort-asc");
    table.querySelectorAll("thead tr:first-child th").forEach(function(h) {{ h.classList.remove("sort-asc","sort-desc"); }});
    th.classList.add(asc ? "sort-asc" : "sort-desc");
    rows.sort(function(a, b) {{
      var aText = a.children[colIdx].textContent.trim();
      var bText = b.children[colIdx].textContent.trim();
      var aNum = parseFloat(aText.replace(/[^0-9.\\-]/g, ""));
      var bNum = parseFloat(bText.replace(/[^0-9.\\-]/g, ""));
      if (aText.includes("T")) aNum *= 1e12; else if (aText.includes("B")) aNum *= 1e9; else if (aText.match(/M/)) aNum *= 1e6;
      if (bText.includes("T")) bNum *= 1e12; else if (bText.includes("B")) bNum *= 1e9; else if (bText.match(/M/)) bNum *= 1e6;
      var aNA = isNaN(aNum); var bNA = isNaN(bNum);
      if (aNA && bNA) return aText.localeCompare(bText) * (asc ? 1 : -1);
      if (aNA) return 1;
      if (bNA) return -1;
      return asc ? aNum - bNum : bNum - aNum;
    }});
    rows.forEach(function(r) {{ tbody.appendChild(r); }});
  }});
}});
updateCount();

function toggleAnnual() {{
  var els = document.querySelectorAll(".annual-col");
  var btn = document.querySelector(".toggle-annual");
  var hiding = !els[0].classList.contains("hidden");
  els.forEach(function(el) {{
    if (hiding) el.classList.add("hidden");
    else el.classList.remove("hidden");
  }});
  btn.textContent = hiding ? "Show Annual Returns" : "Hide Annual Returns";
}}
</script>
{health_html}
</body>
</html>"""

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info("Saved: %s", html_path)
