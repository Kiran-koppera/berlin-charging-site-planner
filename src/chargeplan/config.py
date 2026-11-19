"""Settings, data sources and modelling assumptions.

Every number that is not read from a data file lives here, with its basis. Values marked
"assumption" are planning assumptions, not measurements: change them here and re-run.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "output"

# --------------------------------------------------------------------------------------------
# Public data sources (all open licences, see docs/data-sources.md)
# --------------------------------------------------------------------------------------------
REGISTER_DATE = "2026-11-12"
SOURCES = {
    "ladesaeulenregister.csv": (
        "https://data.bundesnetzagentur.de/Bundesnetzagentur/DE/Fachthemen/ElektrizitaetundGas/"
        f"E-Mobilitaet/Ladesaeulenregister_BNetzA_{REGISTER_DATE}.csv"
    ),
    "lor_plr_2021.geojson": (
        "https://gdi.berlin.de/services/wfs/lor_2021?service=WFS&version=2.0.0&request=GetFeature"
        "&typeNames=lor_2021:a_lor_plr_2021&outputFormat=application/json&srsName=EPSG:4326"
    ),
    "mss2025_plr.xlsx": (
        "https://www.berlin.de/sen/stadt/_assets/stadtdaten/stadtwissen/monitoring-soziale-stadtentwicklung/"
        "bericht-2025/21indexind_anteile_plr_mss2025.xlsx"
    ),
    "traffic_kfz_2023.geojson": (
        "https://gdi.berlin.de/services/wfs/verkehrsmengen_2023?service=WFS&version=2.0.0&request=GetFeature"
        "&typeNames=verkehrsmengen_2023:dtvw2023kfz&outputFormat=application/json&srsName=EPSG:4326"
    ),
    **{
        f"fz1_{y}.xlsx": f"https://www.kba.de/SharedDocs/Downloads/DE/Statistik/Fahrzeuge/FZ1/fz1_{y}.xlsx"
        "?__blob=publicationFile"
        for y in (2023, 2024, 2025, 2026)
    },
}
OSM_SNAPSHOT = ROOT / "data" / "snapshot" / "osm_pois_2026-11-19.json"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_QUERY = """[out:json][timeout:180];
area["de:amtlicher_gemeindeschluessel"="11000000"]["admin_level"="4"]->.b;
(
 nwr["shop"~"^(supermarket|doityourself|mall|department_store|furniture|hardware)$"](area.b);
 nwr["amenity"="fuel"](area.b);
 nwr["amenity"="parking"]["access"!~"private|customers"]["parking"~"multi-storey|underground|surface"](area.b);
 nwr["tourism"="hotel"](area.b);
 nwr["amenity"="fast_food"]["drive_through"="yes"](area.b);
 nwr["amenity"="fast_food"]["brand"~"McDonald|Burger King|KFC"](area.b);
);
out center tags;"""

# --------------------------------------------------------------------------------------------
# Charger classes
# --------------------------------------------------------------------------------------------
HPC_KW = 150  # high-power charging: >= 150 kW per charge point (NOW / Nationale Leitstelle definition)
DC_KW = 50  # anything above 22 kW AC is a DC fast charger in practice; >= 50 kW counted as DC

# --------------------------------------------------------------------------------------------
# Demand assumptions
# --------------------------------------------------------------------------------------------
KM_PER_YEAR = 11_000  # assumption: KBA reports ~12,000 km/yr for German cars; city driving assumed lower
KWH_PER_KM = 0.19  # assumption: 17-20 kWh/100 km typical test values plus ~5 % charging losses
# Share of charging energy that has to come from public chargers, by city area. Inner-city
# residents mostly park on the street (no wallbox), outer districts have more garages and houses.
PUBLIC_SHARE = {"Innere Stadt": 0.60, "Äußere Stadt": 0.35}  # assumption
# Fast charging share of public energy: how much of public demand is served by DC/HPC
DC_SHARE_OF_PUBLIC = 0.45  # assumption; Germany's fast-charging share grows as HPC hubs spread
# Through-traffic: fraction of daily car traffic on a road that is a BEV needing a top-up there
TRAFFIC_BEV_SHARE = 0.040  # Berlin BEV share of the car stock on 1 Jan 2026 (KBA FZ1: 48,888 / 1,222,641)
TRAFFIC_TOPUP_RATE = 0.010  # assumption: 1 % of passing BEVs stop to charge
TOPUP_KWH = 25  # assumption: typical fast-charging session energy

# Energy a charge point can realistically deliver per day (sets "capacity")
KWH_PER_DAY_AC = 18  # assumption: ~2 full-power hours at 11 kW, typical for public AC in cities
KWH_PER_DAY_DC_PER_KW = 1.6  # assumption: ~1.6 full-power hours per day (≈ 7 % time-weighted at full power)

# --------------------------------------------------------------------------------------------
# Coverage and siting
# --------------------------------------------------------------------------------------------
GRID_M = 250  # population grid cell size
AC_WALK_M = 300  # residents within 300 m of an AC point count as covered
HPC_REACH_M = 1_500  # residents within 1.5 km (a few minutes' drive) of an HPC point count as covered
SITE_MIN_SPACING_M = 1_500  # recommended sites at least 1.5 km apart (avoid cannibalising each other)
HPC_COMPETITOR_RADIUS_M = 1_500  # existing HPC within this radius reduces a site's score
TRAFFIC_RADIUS_M = 200  # a POI "sees" traffic of roads within 200 m

# POI types and the charger they suit (dwell time decides power)
POI_CLASSES = {
    "fuel": ("Fuel station", "HPC", 1.00),
    "fast_food": ("Drive-in / fast food", "HPC", 0.95),
    "mall": ("Shopping centre", "HPC", 0.90),
    "doityourself": ("DIY / hardware store", "HPC", 0.85),
    "hardware": ("DIY / hardware store", "HPC", 0.80),
    "furniture": ("Furniture store", "DC 50-150 kW", 0.70),
    "department_store": ("Department store", "DC 50-150 kW", 0.70),
    "supermarket": ("Supermarket", "DC 50-150 kW", 0.85),
    "parking": ("Public car park", "AC 11-22 kW + DC", 0.75),
    "hotel": ("Hotel", "AC 11-22 kW", 0.55),
}

# Fuel brands that run their own HPC network at their stations (Aral pulse, Shell Recharge,
# TotalEnergies): a third-party operator cannot lease these forecourts, so they are listed as
# competitor-controlled hosts and left out of the shortlist.
COMPETITOR_HOST_BRANDS = r"^(Aral|Shell|TotalEnergies|Total|bp|BP)\b"

# Score weights (sum to 1). Documented in docs/methodology.md.
WEIGHTS = {"gap": 0.35, "traffic": 0.25, "whitespace": 0.25, "poi": 0.15}

# --------------------------------------------------------------------------------------------
# Forecast (system dynamics) settings
# --------------------------------------------------------------------------------------------
FORECAST_START, FORECAST_END = 2023, 2030
SCENARIOS = {
    # carrying capacity K as share of today's car stock and how strongly adoption reacts to charging
    # availability; adoption rate and build time are calibrated to 2023-2026 for each scenario
    "slow": {"k_share": 0.25, "elasticity": 0.2},
    "base": {"k_share": 0.50, "elasticity": 0.4},
    "fast": {"k_share": 0.80, "elasticity": 0.7},
}
