# Business case: one 4 × 300 kW HPC site

Workbook: [`hpc-business-case.xlsx`](https://kiran-koppera.github.io/berlin-charging-site-planner/downloads/hpc-business-case.xlsx),
built by [`excel.py`](../src/chargeplan/excel.py). Euros, net of VAT, before tax and financing
(project view), 10 years of operation, no residual value.

## Structure

| Sheet | Content |
|---|---|
| Read me | purpose, how to use, conventions, sources |
| Assumptions | 22 inputs with Pessimistic / Base / Optimistic values (yellow = input); a dropdown in C3 picks the scenario, column F (`CHOOSE`) feeds the model |
| Cash flow | utilisation ramp, energy, revenue, electricity, fees, grid charge, maintenance, backend, lease, EBITDA, capex, free cash flow, cumulative and discounted cash flow; NPV, IRR, payback |
| Sites | the 25 shortlisted locations; each site's score sets a utilisation multiplier (0.8×–1.2×, editable) and its own 10-year cash flow, NPV and IRR are formulas; count of sites with NPV > 0 |
| Sensitivity | tornado (each input at its pessimistic / optimistic value), NPV grid by price and average utilisation, scenario results with break-even utilisation |

The Sensitivity sheet is filled by the Python twin of the model, because openpyxl cannot write
Excel what-if data tables. The tests recalculate the workbook formulas with the `formulas` library
and check NPV, IRR, payback and per-site NPV against the Python model.

## Macro version

`hpc-business-case-macros.xlsm` adds a VBA module, [`ScenarioRunner.bas`](../src/chargeplan/vba/ScenarioRunner.bas):

* **RunAllScenarios** (Ctrl+Shift+R): loops the scenario dropdown through all three scenarios with
  `Application.CalculateFull`, collects one-site NPV, IRR and payback plus every shortlisted site's
  NPV, writes a formatted *Scenario results* sheet and restores the original scenario, also on error.
* **BreakEvenPrice** (Ctrl+Shift+B): finds the price input of the selected scenario with
  `Application.Match`, runs `GoalSeek` on the NPV cell to reach zero, reports the break-even price
  and restores the input.

The module contains source only; Excel compiles it on first open. Because there is no Excel in
the build environment, the VBA project is written by [`vba.py`](../src/chargeplan/vba.py) and
checked with olefile and olevba in the tests; open it in Excel and enable content to run it.

## Key assumptions (Base)

| Input | Base | Basis |
|---|---|---|
| Capex | €490k: 4 × €60k hardware + €140k grid connection + €90k civil works + €20k planning | planning assumptions |
| Utilisation | 6 % in year 1, +1.5 pp a year, ceiling 16 % | share of 24 h charging |
| Average charging power | 95 kW | vehicle charging curves |
| Price | €0.62/kWh net (= €0.74 gross) | Ladechef price index, Nov 2026: median ad-hoc DC price €0.74/kWh, middle 80 % €0.49–0.84 |
| Electricity | €0.24/kWh incl. grid fees and levies | commercial medium-voltage supply |
| Fees | 8 % of revenue for payment and roaming | eMSP and card fees |
| Fixed costs | grid capacity charge €50/kW-yr on 600 kW, maintenance €3k/point, backend €2.5k, lease €8k | escalate 2 % a year |
| Discount rate | 8 % | project cost of capital |

## Results

| Scenario | NPV | IRR | Payback |
|---|---|---|---|
| Pessimistic | −€880k | n/a | > 10 years |
| Base | +€104k | 11.3 % | 7 years |
| Optimistic | +€1,498k | 49.2 % | 3 years |

* At base assumptions a typical site breaks even at **89 % of base-case utilisation**, so it clears
  8 % with little headroom.
* Price moves NPV most (−€309k at €0.50 net, +€206k at €0.70 net), then average charging power and
  first-year utilisation (±€120–155k each).
* With location-adjusted utilisation, **19 of the 25 shortlisted sites** have a positive NPV.
  The model therefore ranks sites by investment case, not only by score.

All inputs are planning assumptions, not quotes. Before any decision, replace them with supplier
offers, the grid operator's connection costs and the host's lease terms.
