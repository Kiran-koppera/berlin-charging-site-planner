# Methodology

All settings and assumptions live in [`src/chargeplan/config.py`](../src/chargeplan/config.py), each with its basis.

## 1. Supply: the charging register

The Bundesnetzagentur register (12 Nov 2026) lists one row per *charging device* with up to six
charge points. For Berlin: 5,104 devices → 12 quality checks ([data-quality.md](data-quality.md))
→ 5,102 usable devices → **7,829 charge points** at **3,573 sites** (devices of one operator within
~10 m grouped into a site). Operators' legal entities are grouped into brands (for example
*E.ON Drive Germany GmbH* and *E.ON Drive Infrastructure GmbH* → E.ON Drive).

| Class | Rule | Points |
|---|---|---|
| AC | point power < 50 kW | 6,523 |
| DC | 50–149 kW | 260 |
| HPC | ≥ 150 kW | 1,046 |

## 2. Demand per planning area

Berlin's 542 LOR planning areas (median ~0.9 km²) with residents on 31 Dec 2024.

* **BEVs** = residents × Berlin BEVs per resident (48,888 / 3.90 million, KBA FZ1, 1 Jan 2026).
* **Daily energy per BEV** = 11,000 km/yr × 0.19 kWh/km / 365 ≈ 5.7 kWh.
* **Public share**: 60 % in the inner city (street parking, few wallboxes), 35 % outside.
* **Split**: 45 % of public energy is fast charging (DC/HPC), the rest AC.
* **Through traffic**: vehicle-km on counted roads inside the area (2023 weekday counts × road
  length inside) × BEV share of the car stock (4.0 %) × 0.19 kWh/km × 25 % bought locally → DC demand.

## 3. Capacity and the relative gap

Deliverable energy per day: 18 kWh per AC point; 1.6 full-power hours per day for DC/HPC.
City-wide, modelled demand is well below capacity (implied utilisation 1.9 % of installed power
over 24 h), partly because cars registered outside Berlin are not counted. Siting therefore uses
the **relative gap**: what an area would have if Berlin's capacity were distributed in proportion
to demand, minus what it has. Positive = under-served. Relative gaps sum to zero across Berlin, separately for AC and DC.

## 4. Coverage

Residents are spread evenly over 250 m grid cells inside their planning area (14,117 cells). A
cell is covered if the nearest AC point is within 300 m (walking) or the nearest HPC point within
1.5 km (a few minutes' drive). Result: 71 % and 91 % of residents.

## 5. Competition

Shares of charge points, installed kW and HPC points by operator; Herfindahl-Hirschman index
(HHI, 0–10,000; below 1,500 = unconcentrated). Berlin: 962 (points), 570 (kW), 917 (HPC points).

## 6. Candidate sites and score

Candidates: OpenStreetMap fuel stations, drive-ins (drive-through or McDonald's / Burger King /
KFC), shopping centres, branded DIY, furniture and department stores, branded supermarkets,
public car parks and hotels: **4,601** inside Berlin. Each host type maps to a charger by dwell
time (short stop → HPC; 20–40 min shop → DC 50–150 kW; long stay → AC).

| Component | Weight | Measure (percentile rank) |
|---|---|---|
| Gap | 35 % | relative gap per km² of the planning area (DC gap for fast chargers, AC gap for AC) |
| Traffic | 25 % | highest weekday volume on a counted road within 200 m |
| Whitespace | 25 % | distance to the nearest HPC (60 %) and few HPC within 1.5 km (40 %) |
| Host fit | 15 % | suitability of the host type |

Sites that already have ≥ 4 fast points within 500 m get × 0.6. Forecourts of fuel brands that run
their own HPC network (Aral, Shell, TotalEnergies, bp) are marked *competitor-controlled* and
excluded. The shortlist is picked greedily by score with **≥ 1.5 km between sites**, so recommended
sites do not cannibalise each other.

## 7. Validation

**Backtest.** Candidates are scored with the network as it stood on 1 Jan 2025 and compared with
where 445 HPC points opened since (hit = new HPC within 300 m). AUC: score 0.52, traffic 0.58,
host type 0.57, gap 0.48. The opportunity score targets under-served demand and does not predict
competitor behaviour. That is a finding in itself: operators chase traffic.

**Competitor-entry model** ([`ml.py`](../src/chargeplan/ml.py)). L2-regularised logistic regression
on traffic, distance to HPC, HPC and fast points nearby, gap and host type. Trained on the 2023
network with 2023–24 openings, tested out-of-time on the 2025 network with 2025–26 openings:
**AUC 0.72 train, 0.61 test**; hit rate 14.0 % in the top fifth vs 8.2 % elsewhere. Refitted on
the latest period and applied today, it flags shortlisted sites as *secure first* (top quartile
of entry probability).

## 8. Forecast and business case

See [system-dynamics.md](system-dynamics.md) and [business-case.md](business-case.md).
