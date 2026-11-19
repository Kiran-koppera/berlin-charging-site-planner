"""The workbook's live formulas must give the same answers as the Python model."""

import openpyxl
import pandas as pd
import pytest

from chargeplan import excel

formulas = pytest.importorskip("formulas")


@pytest.fixture(scope="module")
def book(tmp_path_factory):
    sl = pd.DataFrame({"rank": [1, 2, 3], "name": ["A", "B", ""], "poi_label": ["Fuel station", "Shopping centre", "Hotel"],
                       "street": ["X", "Y", "Z"], "house_no": ["1", "", "3"], "district": ["Mitte"] * 3,
                       "score": [90.0, 80.0, 70.0], "traffic_dtv": [40000, 30000, 20000]})
    path = tmp_path_factory.mktemp("xl") / "case.xlsx"
    excel.build_workbook(path, sl, {"register_date": "2026-11-12"})
    sol = formulas.ExcelModel().loads(str(path)).finish().calculate()

    def get(sheet, cell):
        for k, v in sol.items():
            if k.upper().endswith(f"{sheet.upper()}'!{cell}"):
                return v.value[0][0]
        raise KeyError(cell)

    return path, sl, get


def test_cash_flow_sheet_matches_python(book):
    _, _, get = book
    r = excel.model(excel.params("Base"))
    assert get("Cash flow", "B24") == pytest.approx(excel.capex(excel.params("Base")))
    assert get("Cash flow", "B25") == pytest.approx(r.npv, rel=1e-9)
    assert get("Cash flow", "B26") == pytest.approx(r.irr, abs=1e-6)
    assert get("Cash flow", "B27") == r.payback
    assert get("Cash flow", f"B{excel.AVG_UTIL_ROW}") == pytest.approx(r.utilisation.mean())


def test_site_rows_match_python(book):
    _, sl, get = book
    mult = excel.score_to_mult(sl["score"]).round(2)
    for i in range(3):
        assert get("Sites", f"I{5 + i}") == pytest.approx(excel.model(excel.params("Base"), mult.iloc[i]).npv, rel=1e-9)


def test_workbook_structure(book):
    path, _, _ = book
    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames == ["Read me", "Assumptions", "Cash flow", "Sites", "Sensitivity"]
    a = wb["Assumptions"]
    assert a["C3"].value == "Base" and a.data_validations.dataValidation
    assert str(a[f"F{excel.ROW0}"].value).startswith("=CHOOSE(")
    assert wb["Cash flow"]["A28"].value == "Average utilisation, years 1-10"
