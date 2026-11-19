# Data quality of the charging register

The register is a hand-maintained export: semicolon-separated, Latin-1, ten lines of notes before
the header, up to six charge points per row, and a format that can change between releases. Every
Berlin row passes these checks before it is used ([`register.py`](../src/chargeplan/register.py)).
The decisions are applied in code, so every run gives the same result.

| Check | What | Severity | Devices | Decision |
|---|---|---|---|---|
| Q01 | Missing or unparsable coordinates | error | 0 | Excluded from maps |
| Q02 | Coordinates outside the Berlin boundary | error | 2 | Excluded; operator should correct the location |
| Q03 | Postcode outside Berlin's range (10115-14199) | warning | 7 | Kept (coordinates win); flag to operator |
| Q04 | City field is not 'Berlin' | warning | 1 | Kept if coordinates are inside Berlin |
| Q05 | Fast-charging device with no DC plug | warning | 2 | Classified by plug power, not by label |
| Q06 | Charge point listed above device power | warning | 2 | Point power capped at device power |
| Q07 | Number of charge points differs from plugs listed | info | 0 | Used listed points (each has a power value) |
| Q08 | Commissioning date missing or in the future | warning | 0 | Excluded from history |
| Q09 | Duplicate device id | error | 0 | Kept the first row |
| Q10 | Several devices at identical coordinates (same operator) | info | 1,897 | Grouped into one site |
| Q11 | Parking access not stated | info | 2,291 | Treated as public |
| Q12 | Power implausible (< 3 kW or > 1,000 kW) | error | 0 | Excluded |

**5,104** Berlin devices → **5,102** used → **3,573** sites, **7,829** charge points.

## Findings worth reporting to the data owner

* **Two devices lie outside Berlin** although listed under Berlin. Their coordinates need correcting.
* **Seven devices carry postcodes outside Berlin's range**, for example `01011` for an address on
  Leipziger Platz (10117). The coordinates are right, so the rows are kept.
* **A city field says "Essen"** for a Berlin device.
* **Two devices list a charge point above the device's rated power** (for example a 320 kW point on
  a 50 kW device). Point power is capped at device power, which may misclassify a real HPC point
  as DC. With two cases the effect is negligible, but the operator should confirm.
* **Two "fast-charging" devices have no DC plug.** Classification uses plug power, not the label.
* **1,897 devices share exact coordinates with another device of the same operator**: these are
  multi-device sites, grouped into one site for location analysis.
* **Parking access is blank for 2,291 devices (45 %)**, so access restrictions cannot be analysed reliably.

## Severity

* **error:** the row would be wrong in the analysis; excluded.
* **warning:** needs a decision; the rule applied is in the table.
* **info:** noted, handled by design.
