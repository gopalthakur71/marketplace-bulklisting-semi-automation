import io
import openpyxl
from fastapi.testclient import TestClient

from src.web.main import create_app
from src.web.settings import Settings
import src.web.routers.fix as fixmod


def _client(tmp_path):
    return TestClient(create_app(Settings(
        auth_disabled=True, s3_bucket="b",
        explanation_store_path=str(tmp_path / "expl.json"),
        correction_log_path=str(tmp_path / "log.json"))))


def _sku_xlsx_bytes():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sarees"
    headers = ["STATUS", "SYSTEM ERROR MESSAGE", "vendorSkuCode", "brand",
               "Manufacturer Name and Address with Pincode",
               "Packer Name and Address with Pincode", "Front Image"]
    for c, h in enumerate(headers, start=1):
        ws.cell(row=3, column=c, value=h)
    # AAA: address auto-fix (correctable); IMG: flat-shot image (explain-only)
    ws.append([]) if False else None
    ws.cell(row=4, column=1, value="SKU_VALIDATION_FAILED")
    ws.cell(row=4, column=2, value="Manufacturer and packer information is incomplete")
    ws.cell(row=4, column=3, value="AAA")
    ws.cell(row=5, column=1, value="SKU_VALIDATION_FAILED")
    ws.cell(row=5, column=2, value="Primary image appears to be a flat shot")
    ws.cell(row=5, column=3, value="IMG")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_surface_a_end_to_end_excludes_explain_only(tmp_path, monkeypatch):
    # Use the real template + constants; keep fill_template light by pointing at
    # the repo template. No network: images come from cells, none present here.
    client = _client(tmp_path)
    up = client.post("/fix", files={"file": ("wLf4susb_file.xlsx", _sku_xlsx_bytes(),
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert up.status_code == 200
    assert "AAA" in up.text            # correctable group
    assert "flat shot" in up.text      # explain-only group
    fix_id = up.headers["x-fix-id"]

    r = client.post(f"/fix/apply/{fix_id}", data={})
    assert r.status_code == 200
    assert "Download corrected xlsx" in r.text
    assert "IMG" in r.text             # surfaced as manual-needed, not in the file

    # correction log recorded the AAA address fix
    from src.myntra.correction_log import read_log
    from src.web.settings import LocalJsonStore
    log = read_log(LocalJsonStore(str(tmp_path / "log.json")))
    assert any(rec["sku"] == "AAA" for rec in log)


def test_surface_b_end_to_end(monkeypatch, tmp_path):
    client = _client(tmp_path)

    def fake_regen(skus, settings, out_dir, csv_path=None):
        path = f"{out_dir}/myntra_filled.xlsx"
        with open(path, "wb") as fh:
            fh.write(b"rebuilt")
        return {"written": 1, "file": path, "fixed": list(skus or []),
                "could_not_rebuild": [], "manual_needed": [], "dropped": [],
                "rejected": {}, "changed": {}}

    monkeypatch.setattr(fixmod, "regenerate_surface_b", fake_regen)

    # "manufacturer and packer information is incomplete" is a real configured
    # rule (auto_fix/address) so this row is genuinely correctable -> non-empty
    # skus set for Surface B. A reason that matches no rule would be explain_only
    # and correctly produce an empty rebuild set (see FIX 1 / test_fix.py).
    listings = (b'"style status","seller sku code","onhold reason","style id"\r\n'
                b'"PMR","127SDE826NSB","manufacturer and packer information is incomplete","43214808"\r\n')
    up = client.post("/fix", files={"file": ("MDirect_Listings_Report.csv", listings, "text/csv")})
    assert up.status_code == 200
    fix_id = up.headers["x-fix-id"]
    r = client.post(f"/fix/apply/{fix_id}", data={})
    assert r.status_code == 200
    assert "Download corrected xlsx" in r.text
