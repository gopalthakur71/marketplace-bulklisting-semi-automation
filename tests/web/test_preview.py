from fastapi.testclient import TestClient

from src.web.main import create_app
from src.web.settings import Settings
from src.myntra.template_reader import read_template
from src.myntra.fill import fill_template
from src.core.models import MappedRow, ImageResult

V13 = "templates/myntra/Myntra-Sku-Template-2026-07-24.xlsx"


def _client(tmp_path):
    s = Settings(auth_disabled=True, s3_bucket="b",
                 ledger_local_path=str(tmp_path / "led.json"),
                 hsn_local_path=str(tmp_path / "hsn.json"),
                 sku_registry_local_path=str(tmp_path / "reg.json"))
    return TestClient(create_app(s))


def _filled(tmp_path):
    t = read_template(V13)
    row = MappedRow(sku="S1", cells={
        "vendorSkuCode": "S1", "Type": "Banarasi", "Saree Fabric": "Pure Silk",
        "Ornamentation": "Zari", "Print or Pattern Type": "Floral",
        "Prominent Colour": "Blue", "Pattern": "Solid", "Border": "Solid",
        "Blouse Fabric": "Pure Silk"})
    out = tmp_path / "filled.xlsx"
    fill_template(V13, t, [(row, ImageResult(sku="S1"))], str(out))
    return out


def test_preview_form_renders(tmp_path):
    r = _client(tmp_path).get("/preview")
    assert r.status_code == 200
    assert "Preview" in r.text


def test_preview_shows_reconstruction_and_specs(tmp_path):
    out = _filled(tmp_path)
    client = _client(tmp_path)
    with open(out, "rb") as fh:
        r = client.post("/preview", files={"file": (
            "filled.xlsx", fh.read(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 200
    assert "Floral Zari Pure Silk Banarasi Saree With Unstitched Blouse Piece" in r.text
    assert "Blue Banarasi sarees" in r.text
    assert "Wash Care" in r.text          # a spec row label is shown
    assert "best reconstruction" in r.text  # the "approximate" badge is shown


def test_preview_rejects_non_xlsx(tmp_path):
    r = _client(tmp_path).post(
        "/preview", files={"file": ("x.csv", b"a,b", "text/csv")})
    assert r.status_code == 400
