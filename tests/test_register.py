import pandas as pd

from chargeplan import areas, register


def test_reads_only_berlin_and_unpacks_points(raw_dir):
    df = register.read_register(raw_dir / "ladesaeulenregister.csv")
    assert len(df) == 6
    first = df.set_index("device_id").loc["1"]
    assert first["device_kw"] == 300 and first["has_dc"] and len(first["points"]) == 2
    assert first["commissioned"] == pd.Timestamp("2024-03-01")
    assert abs(first["lat"] - 52.51) < 1e-9


def test_operator_names_group_legal_entities():
    n = register.normalise_operator
    assert n("E.ON Drive Germany GmbH") == n("E.ON Drive Infrastructure GmbH") == "E.ON Drive"
    assert n("ubitricity Gesellschaft für verteilte Energiesysteme mbH") == "ubitricity"
    assert n("EnBW mobility+ AG und Co.KG") == "EnBW"
    assert n("Allego GmbH") == "Allego"
    assert n("Lidl Dienstleistung GmbH & Co. KG") == "Lidl"
    assert n("Walter Fritz deutsche Gesellschaft für Energieversorgung mbH") == "Walter Fritz"


def test_quality_checks_flag_the_planted_problems(raw_dir, geo):
    df = register.read_register(raw_dir / "ladesaeulenregister.csv")
    checks = {c.check_id: c for c in register.quality_checks(df, areas.berlin_outline(geo))}
    assert checks["Q02"].count == 1  # Potsdam coordinates
    assert checks["Q03"].count == 1 and checks["Q04"].count == 1  # Essen postcode and city
    assert checks["Q06"].count == 1  # 175 kW point on a 150 kW device
    assert checks["Q09"].count == 0
    assert checks["Q02"].examples[0]["device_id"] == "6"


def test_clean_caps_power_and_classifies(raw_dir, geo):
    df = register.read_register(raw_dir / "ladesaeulenregister.csv")
    dev = register.clean(df, areas.berlin_outline(geo))
    assert set(dev["device_id"]) == {"1", "2", "3", "4", "5"}
    shell = dev.set_index("device_id").loc["5"]
    assert shell["point_kw"] == [150] and shell["hpc_points"] == 1
    pts = register.points_table(dev)
    assert pts["cls"].value_counts().to_dict() == {"AC": 3, "HPC": 3, "DC": 1}
    s = register.sites(dev)
    assert s["points"].sum() == 7
