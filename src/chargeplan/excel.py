"""HPC site business case workbook (Excel, live formulas) and the same model in Python.

Sheets
* Read me      what the model does, how to use it, colour legend, sources
* Assumptions  every input with Pessimistic / Base / Optimistic values; a dropdown picks the
               scenario and column F (CHOOSE) feeds the model
* Cash flow    one 4-point HPC site over 10 years: utilisation ramp, energy, revenue, costs,
               EBITDA, capex, free cash flow, NPV, IRR, payback, break-even utilisation
* Sites        the 25 shortlisted HPC locations; each site's location score scales utilisation,
               and its own 10-year cash flow, NPV and IRR are formulas
* Sensitivity  tornado and price x utilisation grid, computed with the Python twin of the model
               (Excel what-if data tables cannot be written by openpyxl); tests check that the
               Python model and the workbook formulas give the same NPV

All money is in euros, net of VAT, before tax and financing (project view).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule, ColorScaleRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

SCEN = ["Pessimistic", "Base", "Optimistic"]
YEARS = 10

# key, label, pessimistic, base, optimistic, unit, number format, basis
ASSUMPTIONS = [
    ("points", "HPC charge points per site", 4, 4, 4, "points", "0", "Typical urban hub size"),
    ("power", "Rated power per charge point", 300, 300, 300, "kW", "0", "Shared power cabinet, 2 x 300 kW dispensers"),
    ("hw", "Charger hardware per point", 75_000, 60_000, 50_000, "€", "#,##0", "Assumption: DC 300 kW hardware incl. dispenser"),
    ("grid", "Grid connection and network contribution (site)", 200_000, 140_000, 90_000, "€", "#,##0",
     "Assumption: medium-voltage connection, transformer, Baukostenzuschuss"),
    ("civil", "Civil works and installation (site)", 120_000, 90_000, 70_000, "€", "#,##0",
     "Assumption: foundations, cabling, bays, canopy, signage"),
    ("dev", "Planning, permits, project management (site)", 30_000, 20_000, 15_000, "€", "#,##0", "Assumption"),
    ("subsidy", "Public funding share of capex", 0.0, 0.0, 0.0, "%", "0%", "Set to 0: no grant assumed"),
    ("u1", "Utilisation in year 1 (share of 24 h charging)", 0.04, 0.06, 0.08, "%", "0.0%",
     "Assumption; new urban HPC sites typically start in single digits"),
    ("ug", "Utilisation growth per year", 0.010, 0.015, 0.020, "pp", "0.0%", "Assumption: BEV fleet growth, site awareness"),
    ("ucap", "Utilisation ceiling", 0.12, 0.16, 0.20, "%", "0.0%", "Assumption: above ~20 % drivers queue"),
    ("avgkw", "Average power while charging", 80, 95, 110, "kW", "0", "Assumption: vehicle charging curves cap the average"),
    ("price", "Charging price, net of VAT", 0.50, 0.55, 0.60, "€/kWh", "0.00",
     "Assumption: equals €0.59-0.71 gross; check current ad-hoc HPC prices in Berlin"),
    ("thg", "GHG-quota revenue (THG-Quote)", 0.00, 0.02, 0.04, "€/kWh", "0.00", "Assumption: quota prices are volatile"),
    ("ecost", "Electricity cost incl. grid fees and levies", 0.27, 0.24, 0.21, "€/kWh", "0.00",
     "Assumption: commercial medium-voltage supply, net of VAT"),
    ("fees", "Payment and roaming fees", 0.10, 0.08, 0.06, "% of revenue", "0%", "Assumption: eMSP and card fees"),
    ("gridfix", "Grid capacity charge (Leistungspreis)", 70, 50, 35, "€/kW per year", "0",
     "Assumption: applied to contracted capacity below"),
    ("contract", "Contracted grid capacity", 600, 600, 600, "kW", "0", "Assumption: 4 x 300 kW with load management"),
    ("maint", "Maintenance and service per point", 4_000, 3_000, 2_500, "€/yr", "#,##0", "Assumption: service contract"),
    ("backend", "Backend, connectivity, hotline (site)", 3_000, 2_500, 2_000, "€/yr", "#,##0", "Assumption"),
    ("lease", "Site lease", 12_000, 8_000, 5_000, "€/yr", "#,##0", "Assumption: host retail partner"),
    ("esc", "Price and cost escalation", 0.02, 0.02, 0.02, "% per yr", "0.0%", "Assumption: applied to price and opex"),
    ("wacc", "Discount rate (WACC)", 0.09, 0.08, 0.07, "%", "0.0%", "Assumption: project cost of capital"),
]
KEYS = [a[0] for a in ASSUMPTIONS]
ROW0 = 6  # first assumption row on the Assumptions sheet
AVG_UTIL_ROW = 28  # "Average utilisation, years 1-10" on the Cash flow sheet (checked in tests)

HEAD = PatternFill("solid", fgColor="1E3A5F")
SUB = PatternFill("solid", fgColor="DCE6F0")
INPUT = PatternFill("solid", fgColor="FFF4D6")
WHITE = Font(color="FFFFFF", bold=True)
BOLD = Font(bold=True)
BLUE = Font(color="1F4FBF")
THIN = Border(bottom=Side(style="thin", color="C9D3DE"))
EUR = '#,##0;[Red]-#,##0'


def params(scenario: str = "Base") -> dict:
    j = SCEN.index(scenario) + 2
    return {a[0]: a[j] for a in ASSUMPTIONS}


# --------------------------------------------------------------------------------------------
# Python twin of the workbook
# --------------------------------------------------------------------------------------------
@dataclass
class Result:
    npv: float
    irr: float
    payback: int | None
    fcf: np.ndarray
    energy_mwh: np.ndarray
    utilisation: np.ndarray


def capex(p: dict) -> float:
    return (p["points"] * p["hw"] + p["grid"] + p["civil"] + p["dev"]) * (1 - p["subsidy"])


def model(p: dict, mult: float = 1.0) -> Result:
    y = np.arange(1, YEARS + 1)
    util = np.minimum(p["ucap"], p["u1"] + p["ug"] * (y - 1)) * mult
    energy = p["points"] * 24 * 365 * util * p["avgkw"]  # kWh
    esc = (1 + p["esc"]) ** (y - 1)
    revenue = energy * (p["price"] * esc + p["thg"])
    var_cost = energy * p["ecost"] * esc + energy * p["price"] * esc * p["fees"]
    fixed = (p["gridfix"] * p["contract"] + p["maint"] * p["points"] + p["backend"] + p["lease"]) * esc
    fcf = np.concatenate([[-capex(p)], revenue - var_cost - fixed])
    disc = (1 + p["wacc"]) ** np.arange(0, YEARS + 1)
    npv = float((fcf / disc).sum())
    cum = np.cumsum(fcf)
    neg = int((cum < 0).sum())
    return Result(npv, irr(fcf), neg if neg <= YEARS else None, fcf, energy / 1000, util)


def irr(cf: np.ndarray) -> float:
    """IRR by bisection (cash flows: one outflow then inflows)."""
    f = lambda r: float((cf / (1 + r) ** np.arange(len(cf))).sum())
    lo, hi = -0.99, 1.0
    if f(lo) * f(hi) > 0:
        return float("nan")
    for _ in range(200):
        mid = (lo + hi) / 2
        if f(lo) * f(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def breakeven_mult(p: dict) -> float:
    """Utilisation multiplier at which NPV = 0 (NPV is linear in the multiplier)."""
    a, b = model(p, 0.0).npv, model(p, 1.0).npv
    return -a / (b - a)


def score_to_mult(scores: pd.Series) -> pd.Series:
    """Shortlist score -> utilisation multiplier between 0.8 and 1.2 (rank within the shortlist)."""
    r = scores.rank(pct=True)
    return 0.8 + 0.4 * (r - r.min()) / max(r.max() - r.min(), 1e-9)


# --------------------------------------------------------------------------------------------
# Workbook
# --------------------------------------------------------------------------------------------
def _hdr(ws, row: int, values: list, widths: list | None = None) -> None:
    for i, v in enumerate(values, 1):
        c = ws.cell(row=row, column=i, value=v)
        c.fill, c.font = HEAD, WHITE
        c.alignment = Alignment(vertical="center", wrap_text=True)
        if widths:
            ws.column_dimensions[get_column_letter(i)].width = widths[i - 1]
    ws.row_dimensions[row].height = 30


def ref(key: str) -> str:
    """Absolute reference to the selected value of an assumption."""
    return f"Assumptions!$F${ROW0 + KEYS.index(key)}"


def build_workbook(path: Path, shortlist: pd.DataFrame, summary: dict, xlsm: Path | None = None) -> None:
    """Write the .xlsx and, if `xlsm` is given, a macro-enabled copy with the VBA macros."""
    wb = Workbook()
    _readme(wb.active, summary)
    _assumptions(wb.create_sheet("Assumptions"))
    _cashflow(wb.create_sheet("Cash flow"))
    _sites(wb.create_sheet("Sites"), shortlist)
    _sensitivity(wb.create_sheet("Sensitivity"))
    wb.save(path)
    if xlsm is None:
        return
    from . import vba

    _macro_readme(wb["Read me"])
    res = wb.create_sheet("Scenario results")
    res["A1"] = "Scenario results"
    res["A1"].font = Font(bold=True, size=13)
    res["A2"] = "Press Ctrl+Shift+R (or Alt+F8 > RunAllScenarios) to fill this sheet with all three scenarios."
    res.column_dimensions["A"].width = 90
    wb.code_name = "ThisWorkbook"
    codenames = []
    for i, ws in enumerate(wb.worksheets, 1):
        ws.sheet_properties.codeName = f"Sheet{i}"
        codenames.append(f"Sheet{i}")
    tmp = xlsm.with_suffix(".tmp.xlsx")
    wb.save(tmp)
    source = (Path(__file__).parent / "vba" / "ScenarioRunner.bas").read_text()
    vba.make_xlsm(tmp, xlsm, vba.build_vba_project({"ScenarioRunner": source}, codenames))
    tmp.unlink()


def _macro_readme(ws) -> None:
    r = ws.max_row + 2
    rows = [("Macros (this .xlsm version)", "h"),
            ("Enable content when Excel asks. The VBA code is in the ScenarioRunner module (Alt+F11 to view).", ""),
            ("Ctrl+Shift+R  RunAllScenarios: runs Pessimistic, Base and Optimistic through the live model and writes", ""),
            ("              NPV, IRR, payback, sites with NPV > 0 and every site's NPV to the 'Scenario results' sheet.", ""),
            ("Ctrl+Shift+B  BreakEvenPrice: Goal Seek on the Cash flow NPV to find the net price where NPV = 0.", ""),
            ("Both macros restore the inputs they change and only touch this workbook.", "")]
    for i, (t, kind) in enumerate(rows):
        c = ws.cell(row=r + i, column=1, value=t)
        if kind == "h":
            c.font = BOLD


def _readme(ws, summary: dict) -> None:
    ws.title = "Read me"
    ws.column_dimensions["A"].width = 120
    lines = [
        ("HPC charging site business case – Berlin", "title"),
        ("Project view of one 4-point high-power charging (HPC) site, and of the 25 shortlisted Berlin locations.", ""),
        (f"Shortlist from the Berlin charging-site planner (charging register of {summary['register_date']}).", ""),
        ("", ""),
        ("How to use", "h"),
        ("1. Assumptions: pick a scenario in cell C3 (Pessimistic / Base / Optimistic). Column F is what the model uses.", ""),
        ("2. Yellow cells are inputs. Change any Pessimistic / Base / Optimistic value; everything recalculates.", ""),
        ("3. Cash flow: one site with the selected scenario: NPV, IRR, payback and break-even utilisation.", ""),
        ("4. Sites: each shortlisted location scales utilisation by its location score (0.8x to 1.2x) and gets", ""),
        ("   its own NPV and IRR. Sort by NPV to see which locations carry the investment case.", ""),
        ("5. Sensitivity: which assumptions move NPV most (values from the Python twin of this model).", ""),
        ("", ""),
        ("Conventions", "h"),
        ("Euros, net of VAT, before tax and financing. Year 0 = construction; years 1-10 = operation, no residual value.", ""),
        ("Utilisation = share of 24 hours a charge point is charging. Energy = points x 8,760 h x utilisation x avg kW.", ""),
        ("All cost and price inputs are planning assumptions with a stated basis, not quotes. Replace them with", ""),
        ("supplier offers, grid operator connection costs and the host's lease terms before an investment decision.", ""),
        ("", ""),
        ("Sources", "h"),
        ("Bundesnetzagentur Ladesäulenregister (CC BY 4.0); KBA FZ1 vehicle stock; LOR planning areas © Amt für", ""),
        ("Statistik Berlin-Brandenburg (CC BY 3.0 DE); MSS 2025 population; Berlin traffic volumes 2023 (dl-de/zero-2-0);", ""),
        ("© OpenStreetMap contributors (ODbL).", ""),
    ]
    for i, (t, kind) in enumerate(lines, 1):
        c = ws.cell(row=i, column=1, value=t)
        if kind == "title":
            c.font = Font(bold=True, size=14)
        elif kind == "h":
            c.font = BOLD


def _assumptions(ws) -> None:
    ws["A1"] = "Assumptions"
    ws["A1"].font = Font(bold=True, size=13)
    ws["A3"], ws["B3"] = "Scenario", ""
    ws["A3"].font = BOLD
    ws["C3"] = "Base"
    ws["C3"].fill, ws["C3"].font = INPUT, BOLD
    dv = DataValidation(type="list", formula1='"Pessimistic,Base,Optimistic"', allow_blank=False)
    ws.add_data_validation(dv)
    dv.add("C3")
    ws["D3"] = '=MATCH(C3,{"Pessimistic","Base","Optimistic"},0)'
    ws["E3"] = "← scenario index used by CHOOSE in column F"
    ws["E3"].font = Font(italic=True, color="666666")
    _hdr(ws, ROW0 - 1, ["Key", "Assumption", "Pessimistic", "Base", "Optimistic", "Selected", "Unit", "Basis"],
         [10, 46, 13, 13, 13, 13, 14, 70])
    for i, (key, label, lo, base, hi, unit, fmt, basis) in enumerate(ASSUMPTIONS):
        r = ROW0 + i
        ws.cell(row=r, column=1, value=key).font = Font(color="888888")
        ws.cell(row=r, column=2, value=label)
        for j, v in enumerate((lo, base, hi), 3):
            c = ws.cell(row=r, column=j, value=v)
            c.fill, c.number_format, c.font = INPUT, fmt, BLUE
        c = ws.cell(row=r, column=6, value=f"=CHOOSE($D$3,C{r},D{r},E{r})")
        c.number_format, c.font = fmt, BOLD
        ws.cell(row=r, column=7, value=unit)
        ws.cell(row=r, column=8, value=basis).alignment = Alignment(wrap_text=False)
        for col in range(1, 9):
            ws.cell(row=r, column=col).border = THIN
    r = ROW0 + len(ASSUMPTIONS) + 1
    ws.cell(row=r, column=2, value="Total capex per site (after funding)").font = BOLD
    c = ws.cell(row=r, column=6, value=f"=({ref('points')}*{ref('hw')}+{ref('grid')}+{ref('civil')}+{ref('dev')})"
                                       f"*(1-{ref('subsidy')})")
    c.number_format, c.font = EUR, BOLD
    ws.freeze_panes = f"C{ROW0}"


CF_ROWS = ["Year", "Utilisation", "Energy sold (MWh)", "Escalation factor", "Charging revenue", "GHG-quota revenue",
           "Electricity cost", "Payment and roaming fees", "Grid capacity charge", "Maintenance and service",
           "Backend and connectivity", "Site lease", "EBITDA", "Capex", "Free cash flow", "Cumulative cash flow",
           "Discount factor", "Discounted cash flow"]


def _cashflow(ws) -> None:
    ws["A1"] = "Cash flow – one HPC site, selected scenario"
    ws["A1"].font = Font(bold=True, size=13)
    ws["A2"] = '="Scenario: "&Assumptions!C3'
    _hdr(ws, 4, ["Line item"] + [f"Y{y}" for y in range(YEARS + 1)], [30] + [12] * (YEARS + 1))
    row = {name: 5 + i for i, name in enumerate(CF_ROWS)}
    for name, r in row.items():
        ws.cell(row=r, column=1, value=name).font = BOLD if name in ("EBITDA", "Free cash flow") else Font()
    for y in range(YEARS + 1):
        col = get_column_letter(2 + y)
        c = lambda name, f, fmt=EUR, col=col: _set(ws, f"{col}{row[name]}", f, fmt)
        c("Year", y, "0")
        if y == 0:
            c("Capex", f"=-Assumptions!$F${ROW0 + len(ASSUMPTIONS) + 1}")
            for name in CF_ROWS[1:13]:
                c(name, 0, "0.0%" if name == "Utilisation" else EUR)
        else:
            c("Utilisation", f"=MIN({ref('ucap')},{ref('u1')}+{ref('ug')}*({col}{row['Year']}-1))", "0.0%")
            c("Energy sold (MWh)", f"={ref('points')}*24*365*{col}{row['Utilisation']}*{ref('avgkw')}/1000", "#,##0")
            c("Escalation factor", f"=(1+{ref('esc')})^({col}{row['Year']}-1)", "0.000")
            e, x = f"{col}{row['Energy sold (MWh)']}*1000", f"{col}{row['Escalation factor']}"
            c("Charging revenue", f"={e}*{ref('price')}*{x}")
            c("GHG-quota revenue", f"={e}*{ref('thg')}")
            c("Electricity cost", f"=-{e}*{ref('ecost')}*{x}")
            c("Payment and roaming fees", f"=-{col}{row['Charging revenue']}*{ref('fees')}")
            c("Grid capacity charge", f"=-{ref('gridfix')}*{ref('contract')}*{x}")
            c("Maintenance and service", f"=-{ref('maint')}*{ref('points')}*{x}")
            c("Backend and connectivity", f"=-{ref('backend')}*{x}")
            c("Site lease", f"=-{ref('lease')}*{x}")
            c("EBITDA", f"=SUM({col}{row['Charging revenue']}:{col}{row['Site lease']})")
            c("Capex", 0)
        c("Free cash flow", f"={col}{row['EBITDA']}+{col}{row['Capex']}")
        prev = get_column_letter(1 + y)
        c("Cumulative cash flow", f"={col}{row['Free cash flow']}" if y == 0
          else f"={prev}{row['Cumulative cash flow']}+{col}{row['Free cash flow']}")
        c("Discount factor", f"=1/(1+{ref('wacc')})^{col}{row['Year']}", "0.000")
        c("Discounted cash flow", f"={col}{row['Free cash flow']}*{col}{row['Discount factor']}")
    for name in ("EBITDA", "Free cash flow"):
        for y in range(YEARS + 1):
            ws.cell(row=row[name], column=2 + y).fill = SUB
    last = get_column_letter(2 + YEARS)
    k = row["Discounted cash flow"] + 2
    kpis = [
        ("Capex per site", f"=-B{row['Capex']}", EUR),
        ("NPV (10 years)", f"=SUM(B{row['Discounted cash flow']}:{last}{row['Discounted cash flow']})", EUR),
        ("IRR", f'=IFERROR(IRR(B{row["Free cash flow"]}:{last}{row["Free cash flow"]}),"n/a")', "0.0%"),
        ("Payback (years)", f'=IF(COUNTIF(B{row["Cumulative cash flow"]}:{last}{row["Cumulative cash flow"]},"<0")>{YEARS},'
                            f'"> {YEARS}",COUNTIF(B{row["Cumulative cash flow"]}:{last}{row["Cumulative cash flow"]},"<0"))', "0"),
        ("Average utilisation, years 1-10", f"=AVERAGE(C{row['Utilisation']}:{last}{row['Utilisation']})", "0.0%"),
        ("Energy sold, 10 years (MWh)", f"=SUM(C{row['Energy sold (MWh)']}:{last}{row['Energy sold (MWh)']})", "#,##0"),
    ]
    ws.cell(row=k - 1, column=1, value="Results").font = Font(bold=True, size=12)
    for i, (label, f, fmt) in enumerate(kpis):
        ws.cell(row=k + i, column=1, value=label).font = BOLD
        _set(ws, f"B{k + i}", f, fmt).font = BOLD
    ws.freeze_panes = "B5"
    ws.conditional_formatting.add(f"B{row['Free cash flow']}:{last}{row['Free cash flow']}",
                                  CellIsRule(operator="lessThan", formula=["0"], font=Font(color="C00000")))


def _set(ws, addr: str, value, fmt):
    c = ws[addr]
    c.value = value
    c.number_format = fmt
    return c


def _site_fcf(col_year: int, mult: str) -> str:
    """Free cash flow of year `col_year` for a site whose utilisation multiplier is in `mult`."""
    y = col_year
    u = f"MIN({ref('ucap')},{ref('u1')}+{ref('ug')}*{y - 1})*{mult}"
    e = f"{ref('points')}*8760*{u}*{ref('avgkw')}"
    x = f"(1+{ref('esc')})^{y - 1}"
    return (f"={e}*({ref('price')}*{x}*(1-{ref('fees')})+{ref('thg')}-{ref('ecost')}*{x})"
            f"-({ref('gridfix')}*{ref('contract')}+{ref('maint')}*{ref('points')}+{ref('backend')}+{ref('lease')})*{x}")


def _sites(ws, sl: pd.DataFrame) -> None:
    ws["A1"] = "Shortlisted HPC locations – per-site business case (selected scenario)"
    ws["A1"].font = Font(bold=True, size=13)
    ws["A2"] = ("Utilisation multiplier = 0.8 + 0.4 x the site's rank within the shortlist by location score (edit "
                "column H to override). Year cash flows in columns L-V.")
    ws["A2"].font = Font(italic=True, color="555555")
    head = ["Rank", "Location", "Type", "Address", "District", "Score", "Cars/weekday nearby", "Util. multiplier",
            "NPV", "IRR", "Avg utilisation", "FCF Y0 (capex)"] + [f"FCF Y{y}" for y in range(1, YEARS + 1)]
    _hdr(ws, 4, head, [6, 30, 22, 30, 22, 8, 12, 11, 13, 9, 11, 12] + [11] * YEARS)
    mult = score_to_mult(sl["score"]).round(2)
    cap = f"Assumptions!$F${ROW0 + len(ASSUMPTIONS) + 1}"
    for i, r in enumerate(sl.itertuples()):
        row = 5 + i
        addr = f"{r.street} {r.house_no}".strip()
        vals = [r.rank, r.name or r.poi_label, r.poi_label, addr, r.district, round(r.score, 1), int(r.traffic_dtv),
                float(mult.iloc[i])]
        for j, v in enumerate(vals, 1):
            c = ws.cell(row=row, column=j, value=v)
            if j == 7:
                c.number_format = "#,##0"
            if j == 8:
                c.fill, c.font, c.number_format = INPUT, BLUE, "0.00"
        for y in range(1, YEARS + 1):
            _set(ws, f"{get_column_letter(12 + y)}{row}", _site_fcf(y, f"$H{row}"), EUR)
        first, last = get_column_letter(13), get_column_letter(12 + YEARS)
        _set(ws, f"L{row}", f"=-{cap}", EUR)
        _set(ws, f"I{row}", f"=NPV({ref('wacc')},{first}{row}:{last}{row})+L{row}", EUR).font = BOLD
        _set(ws, f"J{row}", f'=IFERROR(IRR(L{row}:{last}{row}),"n/a")', "0.0%")
        _set(ws, f"K{row}", f"='Cash flow'!$B${AVG_UTIL_ROW}*H{row}", "0.0%")
    n = len(sl)
    ws.conditional_formatting.add(f"I5:I{4 + n}", ColorScaleRule(start_type="min", start_color="F8D7D3",
                                                                 mid_type="num", mid_value=0, mid_color="FFFFFF",
                                                                 end_type="max", end_color="CDEBD7"))
    t = 6 + n
    ws.cell(row=t, column=2, value="Sites with positive NPV").font = BOLD
    ws.cell(row=t, column=9, value=f'=COUNTIF(I5:I{4 + n},">0")').font = BOLD
    ws.cell(row=t + 1, column=2, value="Total NPV of the shortlist").font = BOLD
    _set(ws, f"I{t + 1}", f"=SUM(I5:I{4 + n})", EUR).font = BOLD
    ws.cell(row=t + 2, column=2, value="Total capex of the shortlist").font = BOLD
    _set(ws, f"I{t + 2}", f"=-SUM(L5:L{4 + n})", EUR).font = BOLD
    ws.freeze_panes = "C5"
    ws.auto_filter.ref = f"A4:{get_column_letter(12 + YEARS)}{4 + n}"


def sensitivity_tables(scenario: str = "Base") -> tuple[pd.DataFrame, pd.DataFrame]:
    base = params(scenario)
    base_npv = model(base).npv
    rows = []
    for key, label, lo, _b, hi, *_ in ASSUMPTIONS:
        if lo == hi:
            continue
        p_lo, p_hi = dict(base, **{key: lo}), dict(base, **{key: hi})
        rows.append((label, model(p_lo).npv - base_npv, model(p_hi).npv - base_npv))
    tor = pd.DataFrame(rows, columns=["Assumption", "NPV change at pessimistic value", "NPV change at optimistic value"])
    tor["swing"] = (tor.iloc[:, 1] - tor.iloc[:, 2]).abs()
    tor = tor.sort_values("swing", ascending=False).drop(columns="swing")
    prices = [0.45, 0.50, 0.55, 0.60, 0.65]
    mults = [0.6, 0.8, 1.0, 1.2, 1.4]
    avg = model(base).utilisation.mean()
    grid = pd.DataFrame([[model(dict(base, price=pr), m).npv for pr in prices] for m in mults],
                        index=[f"{avg * m:.1%}" for m in mults], columns=[f"€{p:.2f}" for p in prices])
    return tor, grid


def _sensitivity(ws) -> None:
    ws["A1"] = "Sensitivity – Base scenario, one site (values from the Python model, same logic as Cash flow)"
    ws["A1"].font = Font(bold=True, size=13)
    tor, grid = sensitivity_tables()
    _hdr(ws, 3, list(tor.columns), [46, 22, 22, 4, 16, 12, 12, 12, 12, 12])
    for i, r in enumerate(tor.itertuples(index=False), 4):
        ws.cell(row=i, column=1, value=r[0])
        for j in (1, 2):
            _set(ws, f"{get_column_letter(1 + j)}{i}", round(r[j]), EUR)
    g0 = 4 + len(tor) + 2
    ws.cell(row=g0, column=1, value="NPV by net price (columns) and average utilisation over 10 years (rows)").font = BOLD
    ws.cell(row=g0 + 1, column=1, value="Avg utilisation \\ net price").font = BOLD
    for j, c in enumerate(grid.columns, 2):
        ws.cell(row=g0 + 1, column=j, value=c).font = BOLD
    for i, (idx, vals) in enumerate(grid.iterrows(), g0 + 2):
        ws.cell(row=i, column=1, value=idx).font = BOLD
        for j, v in enumerate(vals, 2):
            _set(ws, f"{get_column_letter(j)}{i}", round(v), EUR)
    rng = f"B{g0 + 2}:{get_column_letter(1 + len(grid.columns))}{g0 + 1 + len(grid)}"
    ws.conditional_formatting.add(rng, ColorScaleRule(start_type="min", start_color="F8D7D3", mid_type="num",
                                                      mid_value=0, mid_color="FFFFFF", end_type="max",
                                                      end_color="CDEBD7"))
    s = g0 + 3 + len(grid)
    ws.cell(row=s, column=1, value="Scenario results (one site)").font = BOLD
    _hdr(ws, s + 1, ["Scenario", "NPV", "IRR", "Payback (years)", "Break-even utilisation multiplier"])
    for i, name in enumerate(SCEN, s + 2):
        res = model(params(name))
        ws.cell(row=i, column=1, value=name)
        _set(ws, f"B{i}", round(res.npv), EUR)
        _set(ws, f"C{i}", res.irr, "0.0%")
        ws.cell(row=i, column=4, value=res.payback if res.payback is not None else f"> {YEARS}")
        _set(ws, f"E{i}", breakeven_mult(params(name)), "0.00")
