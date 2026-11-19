"""Small synthetic inputs so tests run without the 75 MB of public data."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box, mapping

from chargeplan import areas

HEADER = ("Ladeeinrichtungs-ID;Betreiber;Anzeigename (Karte);Status;Art der Ladeeinrichtung;Anzahl Ladepunkte;"
          "Nennleistung Ladeeinrichtung [kW];Inbetriebnahmedatum;Straße;Hausnummer;Adresszusatz;Postleitzahl;Ort;"
          "Kreis/kreisfreie Stadt;Bundesland;Breitengrad;Längengrad;Standortbezeichnung;Informationen zum Parkraum;"
          "Bezahlsysteme;Öffnungszeiten;Öffnungszeiten: Wochentage;Öffnungszeiten: Tageszeiten;"
          + ";".join(f"Steckertypen{k};Nennleistung Stecker{k};EVSE-ID{k};Public Key{k}" for k in range(1, 7)))


def reg_row(i, op, typ, n, kw, date, lat, lon, plugs, state="Berlin", city="Berlin", plz="10115"):
    cells = [str(i), op, op, "In Betrieb", typ, str(n), str(kw).replace(".", ","), date, "Teststr.", "1", "", plz, city,
             "Kreisfreie Stadt Berlin", state, f"{lat:.6f}".replace(".", ","), f"{lon:.6f}".replace(".", ","), "", "",
             "RFID-Karte", "247", "", ""]
    for k in range(6):
        if k < len(plugs):
            cells += [plugs[k][0], str(plugs[k][1]).replace(".", ","), f"DE*X*{i}{k}", ""]
        else:
            cells += ["", "", "", ""]
    return ";".join(cells)


# two planning areas side by side around central Berlin: west (inner city) and east (outer city)
WEST = box(13.38, 52.50, 13.40, 52.52)
EAST = box(13.40, 52.50, 13.42, 52.52)


@pytest.fixture
def raw_dir(tmp_path):
    lines = ["Ladesäulenregister Bundesnetzagentur" + ";" * 46] + [";" * 46] * 9 + ["Allgemeine Informationen;;;"] + [HEADER]
    rows = [
        reg_row(1, "Allego GmbH", "Schnellladeeinrichtung", 2, 300, "01.03.2024", 52.51, 13.39,
                [("DC Fahrzeugkupplung Typ Combo 2 (CCS)", 300), ("DC Fahrzeugkupplung Typ Combo 2 (CCS)", 300)]),
        reg_row(2, "ubitricity Gesellschaft für verteilte Energiesysteme mbH", "Normalladeeinrichtung", 1, 11,
                "01.06.2022", 52.505, 13.385, [("AC Typ 2 Steckdose", 11)]),
        reg_row(3, "E.ON Drive Germany GmbH", "Normalladeeinrichtung", 2, 22, "01.06.2023", 52.515, 13.395,
                [("AC Typ 2 Steckdose", 22), ("AC Typ 2 Steckdose", 22)]),
        reg_row(4, "E.ON Drive Infrastructure GmbH", "Schnellladeeinrichtung", 1, 50, "15.01.2025", 52.505, 13.41,
                [("DC Fahrzeugkupplung Typ Combo 2 (CCS)", 50)]),
        # point power above device power, wrong city, postcode outside Berlin
        reg_row(5, "Shell Deutschland GmbH", "Schnellladeeinrichtung", 1, 150, "01.02.2025", 52.515, 13.415,
                [("DC Fahrzeugkupplung Typ Combo 2 (CCS)", 175)], city="Essen", plz="45127"),
        # outside the Berlin boundary (Potsdam)
        reg_row(6, "Allego GmbH", "Normalladeeinrichtung", 1, 22, "01.01.2021", 52.39, 13.06, [("AC Typ 2 Steckdose", 22)]),
        # another state: ignored
        reg_row(7, "EnBW mobility+ AG und Co.KG", "Normalladeeinrichtung", 1, 22, "01.01.2021", 48.1, 9.1,
                [("AC Typ 2 Steckdose", 22)], state="Baden-Württemberg"),
    ]
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "ladesaeulenregister.csv").write_bytes(("\r\n".join(lines + rows) + "\r\n").encode("latin-1"))
    feats = [
        {"type": "Feature", "geometry": mapping(WEST), "properties": {"plr_id": "01100101", "plr_name": "West", "bzr_name": "W",
                                                                      "bez": "01 - Mitte", "stadtraum_name": "Innere Stadt"}},
        {"type": "Feature", "geometry": mapping(EAST), "properties": {"plr_id": "02200202", "plr_name": "East", "bzr_name": "E",
                                                                      "bez": "02 - Friedrichshain-Kreuzberg",
                                                                      "stadtraum_name": "Äußere Stadt"}},
    ]
    (raw / "lor_plr_2021.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    for _ in range(10):
        ws.append(["header"])
    ws.append(["01100101", "West", 20000])
    ws.append(["02200202", "East", 10000])
    wb.save(raw / "mss2025_plr.xlsx")
    roads = [
        {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[13.38, 52.51], [13.42, 52.51]]},
         "properties": {"str_name": "Hauptstraße", "strklasse": "I", "dtvw_kfz": 40000}},
        {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[13.39, 52.50], [13.39, 52.52]]},
         "properties": {"str_name": "Nebenstraße", "strklasse": "III", "dtvw_kfz": 8000}},
    ]
    (raw / "traffic_kfz_2023.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": roads}))
    els = [
        {"type": "node", "id": 1, "lat": 52.5101, "lon": 13.405, "tags": {"amenity": "fuel", "brand": "Aral", "name": "Aral"}},
        {"type": "node", "id": 2, "lat": 52.5102, "lon": 13.415, "tags": {"amenity": "fuel", "brand": "Sprint", "name": "Sprint"}},
        {"type": "node", "id": 3, "lat": 52.512, "lon": 13.385, "tags": {"shop": "supermarket", "brand": "REWE", "name": "REWE"}},
        {"type": "node", "id": 4, "lat": 52.502, "lon": 13.418, "tags": {"shop": "hardware", "name": "Kleiner Laden"}},
        {"type": "way", "id": 5, "center": {"lat": 52.518, "lon": 13.41}, "tags": {"shop": "doityourself", "brand": "OBI"}},
        {"type": "node", "id": 6, "lat": 52.501, "lon": 13.401, "tags": {"amenity": "fast_food", "name": "Imbiss"}},
        {"type": "node", "id": 7, "lat": 52.5105, "lon": 13.395, "tags": {"amenity": "fast_food", "brand": "McDonald's"}},
        {"type": "node", "id": 8, "lat": 52.519, "lon": 13.381, "tags": {"tourism": "hotel", "name": "Hotel"}},
    ]
    (raw / "osm_pois.json").write_text(json.dumps({"elements": els}))
    return raw


@pytest.fixture
def geo(raw_dir):
    return areas.load_planning_areas(raw_dir / "lor_plr_2021.geojson", raw_dir / "mss2025_plr.xlsx")


@pytest.fixture
def rng():
    return np.random.default_rng(7)


def kba_book(path, year, bev, cars):
    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.title = "FZ1.1"
    ws = wb.create_sheet("FZ1.2")
    ws.append([None, "BADEN-WUERTTEMBERG", "RB STUTTGART", "08111 STUTTGART", 1, 1, 1, 1, 1, 1, 999])
    ws.append([None, "BERLIN INSGESAMT", None, None, cars, 0, 0, 0, 0, 0, bev])
    wb.save(path / f"fz1_{year}.xlsx")


@pytest.fixture
def full_raw(raw_dir):
    for y, b in {2023: 25000, 2024: 36000, 2025: 42000, 2026: 49000}.items():
        kba_book(raw_dir, y, b, 1_220_000)
    return raw_dir


__all__ = ["pd"]
