"""Generate a sample HTML report with mock data to preview formatting."""
import sys, os

from reporting.calcs import (
    FUND_COLS, FUND_DISPLAY_NAMES, FUND_PCT_COLS, FUND_RATIO_COLS,
    FUND_MONEY_COLS, RETURN_COLS, format_mkt_cap, get_html_color,
)
from datetime import datetime
from pathlib import Path
import json
import pandas as pd

REPORTS_DIR = Path(__file__).resolve().parent / "reports"

# Mock rows: Ticker, Company, Sector(JP), Subsector(JP), YF Sector, YF Industry, Country, Exchange, currency, fundamentals, returns
MOCK_DATA = [
    {"Ticker": "AAPL", "Company Name": "Apple Inc.", "Sector (JP)": "Technology", "Subsector (JP)": "Consumer Electronics",
     "YF Sector": "Technology", "YF Industry": "Consumer Electronics", "Country (HQ)": "United States", "Exchange": "NMS",
     "_currency": "USD", "Mkt Cap": 3.2e12, "Fwd P/E": 28.5, "EV/EBITDA": 22.1, "EV/S": 8.3, "PEG": 2.1,
     "Gross Mgn": 46.2, "Op Mgn": 31.5, "ROE": 160.1, "Rev Grw": 5.2, "EPS Grw": 8.1,
     "_is_ttm_rev": True, "_is_ttm_eps": True,
     "1D": 1.2, "1W": -0.5, "QTD": 3.8, "YTD": 12.4, "1Y": 28.3, "3Y": 45.0, "5Y": 180.2, "10Y": 520.0,
     "2019": 86.2, "2020": 82.3, "2021": 34.7, "2022": -26.4, "2023": 48.2, "2024": 30.1, "2025": 12.4, "2026": None},
    {"Ticker": "7203.T", "Company Name": "Toyota Motor Corp", "Sector (JP)": "Autos", "Subsector (JP)": "Auto Mfg",
     "YF Sector": "Consumer Cyclical", "YF Industry": "Auto Manufacturers", "Country (HQ)": "Japan", "Exchange": "JPX",
     "_currency": "JPY", "Mkt Cap": 42.5e12, "Fwd P/E": 10.2, "EV/EBITDA": 8.5, "EV/S": 0.9, "PEG": 0.8,
     "Gross Mgn": 20.1, "Op Mgn": 9.8, "ROE": 14.3, "Rev Grw": 12.8, "EPS Grw": 18.5,
     "_is_ttm_rev": False, "_is_ttm_eps": False,
     "1D": -0.3, "1W": 1.1, "QTD": -2.1, "YTD": -5.2, "1Y": 8.7, "3Y": 62.0, "5Y": 40.1, "10Y": 90.3,
     "2019": 12.0, "2020": -8.5, "2021": 5.3, "2022": 15.7, "2023": 42.8, "2024": -5.2, "2025": -5.2, "2026": None},
    {"Ticker": "MSFT", "Company Name": "Microsoft Corp", "Sector (JP)": "Technology", "Subsector (JP)": "Software",
     "YF Sector": "Technology", "YF Industry": "Software - Infrastructure", "Country (HQ)": "United States", "Exchange": "NMS",
     "_currency": "USD", "Mkt Cap": 2.9e12, "Fwd P/E": 32.1, "EV/EBITDA": 25.3, "EV/S": 13.2, "PEG": 2.5,
     "Gross Mgn": 69.8, "Op Mgn": 44.2, "ROE": 38.5, "Rev Grw": 15.1, "EPS Grw": 20.3,
     "_is_ttm_rev": True, "_is_ttm_eps": True,
     "1D": 0.8, "1W": 2.3, "QTD": 5.1, "YTD": 8.9, "1Y": 22.1, "3Y": 55.0, "5Y": 200.5, "10Y": 680.0,
     "2019": 57.6, "2020": 42.5, "2021": 52.5, "2022": -28.0, "2023": 56.8, "2024": 12.1, "2025": 8.9, "2026": None},
    {"Ticker": "SAP", "Company Name": "SAP SE", "Sector (JP)": "Technology", "Subsector (JP)": "Enterprise Software",
     "YF Sector": "Technology", "YF Industry": "Software - Application", "Country (HQ)": "Germany", "Exchange": "GER",
     "_currency": "EUR", "Mkt Cap": 280e9, "Fwd P/E": 36.8, "EV/EBITDA": 30.2, "EV/S": 10.5, "PEG": 3.1,
     "Gross Mgn": 72.1, "Op Mgn": 22.5, "ROE": 18.9, "Rev Grw": 9.8, "EPS Grw": 12.0,
     "_is_ttm_rev": False, "_is_ttm_eps": False,
     "1D": -1.5, "1W": -3.2, "QTD": 1.0, "YTD": 4.2, "1Y": 35.8, "3Y": 80.0, "5Y": 110.0, "10Y": None,
     "2019": 38.0, "2020": 2.5, "2021": 18.0, "2022": -22.3, "2023": 42.0, "2024": 60.5, "2025": 4.2, "2026": None},
    {"Ticker": "NVDA", "Company Name": "NVIDIA Corp", "Sector (JP)": "Technology", "Subsector (JP)": "Semiconductors",
     "YF Sector": "Technology", "YF Industry": "Semiconductors", "Country (HQ)": "United States", "Exchange": "NMS",
     "_currency": "USD", "Mkt Cap": 2.8e12, "Fwd P/E": 40.5, "EV/EBITDA": 55.2, "EV/S": 30.1, "PEG": 1.2,
     "Gross Mgn": 75.3, "Op Mgn": 62.1, "ROE": 95.0, "Rev Grw": 122.0, "EPS Grw": 150.0,
     "_is_ttm_rev": True, "_is_ttm_eps": True,
     "1D": 3.5, "1W": 8.2, "QTD": 15.0, "YTD": 25.3, "1Y": 180.0, "3Y": 600.0, "5Y": 2400.0, "10Y": 25000.0,
     "2019": 76.3, "2020": 122.3, "2021": 125.4, "2022": -50.3, "2023": 238.9, "2024": 171.2, "2025": 25.3, "2026": None},
    {"Ticker": "BABA", "Company Name": "Alibaba Group", "Sector (JP)": "Technology", "Subsector (JP)": "E-Commerce",
     "YF Sector": "Consumer Cyclical", "YF Industry": "Internet Retail", "Country (HQ)": "China", "Exchange": "NYQ",
     "_currency": "USD", "Mkt Cap": 280e9, "Fwd P/E": 10.8, "EV/EBITDA": 7.5, "EV/S": 1.8, "PEG": 0.6,
     "Gross Mgn": 38.2, "Op Mgn": 15.1, "ROE": 12.5, "Rev Grw": 8.3, "EPS Grw": -5.2,
     "_is_ttm_rev": False, "_is_ttm_eps": False,
     "1D": -2.1, "1W": -5.0, "QTD": -8.3, "YTD": -12.0, "1Y": -20.5, "3Y": -55.0, "5Y": -60.0, "10Y": None,
     "2019": 55.0, "2020": 10.2, "2021": -48.9, "2022": -20.1, "2023": 5.0, "2024": 15.3, "2025": -12.0, "2026": None},
]

result_df = pd.DataFrame(MOCK_DATA)

html_info_cols = ["Ticker", "Mkt Cap", "Company Name", "Sector (JP)", "Subsector (JP)", "YF Sector", "YF Industry", "Country (HQ)", "Exchange"]
html_fund_cols = [c for c in FUND_COLS if c != "Mkt Cap"]

html_rows = []
for _, row in result_df.iterrows():
    cells = []
    val = row.get("Ticker", "")
    cells.append(f'<td class="info">{val}</td>')
    mcap = row.get("Mkt Cap")
    currency = row.get("_currency", "")
    cells.append(f'<td class="fund">{format_mkt_cap(mcap, currency)}</td>')
    for col in ["Company Name", "Sector (JP)", "Subsector (JP)", "YF Sector", "YF Industry", "Country (HQ)", "Exchange"]:
        val = row.get(col, "")
        if pd.isna(val) or str(val) == "nan":
            val = ""
        cells.append(f'<td class="info">{val}</td>')
    for col in RETURN_COLS:
        val = row.get(col)
        if val is not None and not pd.isna(val):
            bg = get_html_color(val)
            text_color = "#8B0000" if val < 0 else "#006400" if val > 0 else "#333"
            cells.append(f'<td class="ret" style="background-color:{bg};color:{text_color}">{val:.1f}%</td>')
        else:
            cells.append('<td class="ret na">N/A</td>')
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
                bg = get_html_color(val)
                text_color = "#8B0000" if val < 0 else "#006400" if val > 0 else "#333"
                cells.append(f'<td class="fund" style="background-color:{bg};color:{text_color}">{val:.1f}%{asterisk}</td>')
            else:
                cells.append(f'<td class="fund">{val:.1f}x</td>')
        else:
            cells.append('<td class="fund na">N/A</td>')
    html_rows.append("<tr>" + "".join(cells) + "</tr>")

html_info_display = [FUND_DISPLAY_NAMES.get(c, c) for c in html_info_cols]
info_headers = "".join(f"<th>{c}</th>" for c in html_info_display)
ret_headers = "".join(f"<th>{c}</th>" for c in RETURN_COLS)
fund_headers = "".join(f'<th class="fund-hdr">{FUND_DISPLAY_NAMES.get(c, c)}</th>' for c in html_fund_cols)
header_cells = info_headers + ret_headers + fund_headers

num_info = len(html_info_cols)
filterable_cols = {0: "Ticker", 2: "Company Name", 3: "Sector (JP)", 4: "Subsector (JP)",
                   5: "YF Sector", 6: "YF Industry", 7: "Country (HQ)", 8: "Exchange"}
col_unique_values = {}
for col_idx, col_name in filterable_cols.items():
    vals = sorted(set(
        str(v).strip() for v in result_df[col_name] if pd.notna(v) and str(v).strip() and str(v).strip() != "nan"
    ), key=str.lower)
    col_unique_values[col_idx] = vals
filter_options_json = json.dumps(col_unique_values)

filter_cells_list = []
for i in range(num_info):
    if i in filterable_cols:
        filter_cells_list.append(
            f'<th><div class="ms-dropdown" data-col="{i}">'
            f'<button class="ms-trigger" type="button">All &#9660;</button>'
            f'<div class="ms-panel" style="display:none;"></div>'
            f'</div></th>')
    else:
        filter_cells_list.append("<th></th>")
filter_cells = "".join(filter_cells_list)
filter_cells += "".join("<th></th>" for _ in RETURN_COLS)
filter_cells += "".join("<th></th>" for _ in html_fund_cols)

timestamp = datetime.now().strftime("%B %d, %Y %H:%M")

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Coverage Universe Performance (SAMPLE)</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
  h1 {{ color: #2c3e50; font-size: 22px; }}
  p.timestamp {{ color: #888; font-size: 12px; margin-bottom: 15px; }}
  .table-wrapper {{ overflow-x: auto; max-height: 85vh; overflow-y: auto; }}
  table {{ border-collapse: collapse; font-size: 11px; width: 100%; }}
  thead {{ position: sticky; top: 0; z-index: 2; }}
  th {{ background: #2c3e50; color: white; padding: 8px 6px; text-align: center; border: 1px solid #1a252f;
       font-weight: 600; white-space: nowrap; cursor: pointer; user-select: none; }}
  th:hover {{ background: #3e5871; }}
  th.sort-asc::after {{ content: " ▲"; font-size: 9px; }}
  th.sort-desc::after {{ content: " ▼"; font-size: 9px; }}
  td {{ padding: 5px 6px; border: 1px solid #ddd; white-space: nowrap; }}
  td.info {{ text-align: left; background: #fff; }}
  td.ret {{ text-align: center; font-weight: 500; }}
  td.fund {{ text-align: center; font-weight: 500; }}
  td.na {{ color: #bbb; background: #fafafa; }}
  th.fund-hdr {{ background: #1a5276; }}
  tr:hover td {{ opacity: 0.85; }}
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
</style>
</head>
<body>
<h1>Coverage Universe Performance (SAMPLE)</h1>
<p class="timestamp">Generated: {timestamp}</p>
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
  document.querySelectorAll("tbody tr").forEach(function(row) {{
    var show = true;
    for (var colIdx in selectedFilters) {{
      var allowed = selectedFilters[colIdx];
      if (!allowed || allowed.size === 0) continue;
      var cellText = row.children[parseInt(colIdx)].textContent.trim().toLowerCase();
      if (!allowed.has(cellText)) {{ show = false; break; }}
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

// Trigger click handlers
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
</script>
</body>
</html>"""

os.makedirs(REPORTS_DIR, exist_ok=True)
out_path = REPORTS_DIR / "sample_preview.html"
with open(out_path, "w", encoding="utf-8") as f:
    f.write(html)
print(f"Sample HTML saved to: {out_path}")
