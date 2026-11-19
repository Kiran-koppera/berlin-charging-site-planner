# Data sources

| Data | Publisher | Version | Licence |
|---|---|---|---|
| Ladesäulenregister (public charging devices) | Bundesnetzagentur | 12 Nov 2026 | CC BY 4.0 |
| FZ1 vehicle stock by registration district, fuel type | Kraftfahrt-Bundesamt | 1 Jan 2023–2026 | KBA terms of use, source cited |
| LOR planning areas (Planungsräume), 542 areas | Amt für Statistik Berlin-Brandenburg, via Geoportal Berlin (WFS `lor_2021`) | LOR 01/2021 | CC BY 3.0 DE |
| Residents per planning area (Einwohnerregisterstatistik, report A I 16) | Amt für Statistik Berlin-Brandenburg | 30 June 2026 (published Aug 2026) | CC BY 3.0 DE |
| Ad-hoc DC charging price (business-case input) | Ladechef price index, as reported in the press | November 2026 | cited, not redistributed |
| Average weekday traffic (DTVw) 2023, cars | Land Berlin, Geoportal (WFS `verkehrsmengen_2023`) | 2023 | dl-de/zero-2-0 |
| Points of interest (hosts) | © OpenStreetMap contributors, Overpass API | snapshot 19 Nov 2026 | ODbL |

`python -m chargeplan download` fetches all of them (URLs in [`config.py`](../src/chargeplan/config.py)).
OpenStreetMap is read from a committed snapshot so results are reproducible; `--refresh-osm`
queries it live.
