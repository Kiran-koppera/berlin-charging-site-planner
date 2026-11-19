"""The macro workbook is built without Excel, so its binary parts are checked against
independent readers: olefile (compound file) and olevba (VBA project and source)."""

import random
import zipfile

import pandas as pd
import pytest

from chargeplan import excel, vba

olefile = pytest.importorskip("olefile")


@pytest.mark.parametrize("n", [0, 1, 3, 100, 4095, 4096, 4097, 10_000])
def test_compression_round_trip(n):
    data = bytes(random.Random(n).choice(b"Dim x As Long\r\n ' abc") for _ in range(n))
    assert vba.decompress(vba.compress(data)) == data


def test_compression_matches_spec_example():
    # [MS-OVBA] 3.2.1: "abcdefghijklmnopqrstuv." compresses to literal tokens only
    data = b"abcdefghijklmnopqrstuv."
    out = vba.compress(data)
    assert out[0] == 1 and vba.decompress(out) == data
    # 3.2.2: repeated text uses copy tokens and is shorter than its input
    rep = b"#aaabcdefaaaaghijaaaaaklaaamnopqaaaaaaaaaaaarstuvwxyzaaa"
    assert len(vba.compress(rep)) < len(rep) + 3 and vba.decompress(vba.compress(rep)) == rep


def test_encryption_round_trip():
    pid = "{12345678-ABCD-1234-ABCD-1234567890AB}"
    for d in (b"\x00\x00\x00\x00", b"\x00", b"\xff"):
        assert vba.decrypt(vba.encrypt(d, pid, seed=4)) == d


def test_vba_project_is_a_valid_compound_file():
    src = (vba.Path(vba.__file__).parent / "vba" / "ScenarioRunner.bas").read_text()
    blob = vba.build_vba_project({"ScenarioRunner": src}, ["Sheet1", "Sheet2"])
    ole = olefile.OleFileIO(blob)
    assert sorted("/".join(e) for e in ole.listdir()) == sorted(
        ["PROJECT", "PROJECTwm", "VBA/_VBA_PROJECT", "VBA/dir", "VBA/ThisWorkbook", "VBA/Sheet1", "VBA/Sheet2",
         "VBA/ScenarioRunner"])
    assert ole.openstream("VBA/_VBA_PROJECT").read()[:2] == b"\xcc\x61"
    stored = vba.decompress(ole.openstream("VBA/ScenarioRunner").read()).decode("cp1252")
    assert "Public Sub RunAllScenarios()" in stored and "\r\n" in stored
    project = ole.openstream("PROJECT").read().decode("cp1252")
    assert "Module=ScenarioRunner" in project and "Document=Sheet2/&H00000000" in project
    dpb = project.split('DPB="')[1].split('"')[0]
    assert vba.decrypt(dpb) == b"\x00"


def test_macro_workbook(tmp_path):
    olevba = pytest.importorskip("oletools.olevba")
    sl = pd.DataFrame({"rank": [1, 2], "name": ["A", "B"], "poi_label": ["Fuel station", "Hotel"], "street": ["X", "Y"],
                       "house_no": ["1", ""], "district": ["Mitte"] * 2, "score": [90.0, 80.0], "traffic_dtv": [40000, 20000]})
    xlsm = tmp_path / "case.xlsm"
    excel.build_workbook(tmp_path / "case.xlsx", sl, {"register_date": "2026-11-12"}, xlsm=xlsm)
    z = zipfile.ZipFile(xlsm)
    assert "xl/vbaProject.bin" in z.namelist()
    assert "macroEnabled.main+xml" in z.read("[Content_Types].xml").decode()
    assert 'codeName="ThisWorkbook"' in z.read("xl/workbook.xml").decode()
    parser = olevba.VBA_Parser(str(xlsm))
    assert parser.detect_vba_macros()
    modules = {name: code for _, _, name, code in parser.extract_macros()}
    assert "ScenarioRunner.bas" in modules and "Sub BreakEvenPrice()" in modules["ScenarioRunner.bas"]
    assert {f"Sheet{i}.cls" for i in range(1, 7)} <= set(modules)
