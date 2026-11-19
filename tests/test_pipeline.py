import json

from chargeplan import pipeline


def test_end_to_end_on_synthetic_city(full_raw, tmp_path):
    out = tmp_path / "out"
    s = pipeline.run(raw=full_raw, out=out)
    assert s["points"] == 7 and s["devices_raw"] == 6 and s["devices_used"] == 5
    assert s["bev_stock"][2026] == 49000 and s["car_stock"] == 1_220_000
    assert s["allego"]["hpc"] == 2
    assert {c["check_id"] for c in s["quality"]} >= {"Q01", "Q02", "Q06", "Q12"}
    for f in ("areas.geojson", "points.json", "shortlist.json", "summary.json", "operators.json", "history.json"):
        assert (out / "site" / "data" / f).exists()
    assert (out / "site" / "downloads" / "hpc-business-case.xlsx").exists()
    short = json.loads((out / "site" / "data" / "shortlist.json").read_text())
    assert all(r["host_access"] == "Open to a host partnership" for r in short["hpc"])
    assert set(s["forecast"]["scenarios"]) == {"slow", "base", "fast"}
