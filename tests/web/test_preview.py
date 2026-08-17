import os

from fastapi.testclient import TestClient

from src.web.main import create_app
from src.web.settings import Settings
from src.myntra.template_reader import read_template
from src.myntra.fill import fill_template
from src.core.models import MappedRow, ImageResult
import src.web.routers.generate as gen
from src.web.jobs import store

V13 = "templates/myntra/Myntra-Sku-Template-2026-07-24.xlsx"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


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


def test_nav_links_to_preview(tmp_path):
    """The screen was only reachable from a fresh generate result; every page's
    nav must offer it, or a re-check of an already-filled sheet needs a typed URL."""
    r = _client(tmp_path).get("/")
    assert r.status_code == 200
    assert '<a href="/preview">' in r.text


def test_preview_form_renders(tmp_path):
    r = _client(tmp_path).get("/preview")
    assert r.status_code == 200
    assert "Preview" in r.text


def test_preview_rejects_non_xlsx(tmp_path):
    r = _client(tmp_path).post(
        "/preview", files={"file": ("x.csv", b"a,b", "text/csv")})
    assert r.status_code == 400


def test_upload_adopts_the_workbook_as_an_editable_job(tmp_path, monkeypatch):
    """The uploaded file becomes a job, so every Fill-attributes surface works on
    it unchanged. Without this the sheet is only viewable, never editable."""
    monkeypatch.setattr(gen, "RUNTIME", str(tmp_path / "runtime"))
    out = _filled(tmp_path)
    client = _client(tmp_path)
    with open(out, "rb") as fh:
        r = client.post("/preview", files={"file": ("mysheet.xlsx", fh.read(), XLSX)})
    assert r.status_code == 200
    target = r.headers["hx-redirect"]
    assert target.startswith("/generate/attributes/")
    job = store.get(target.rsplit("/", 1)[1])
    assert job.result["origin"] == "upload"
    assert job.result["filename"] == "mysheet.xlsx"
    assert client.get(target).status_code == 200


def test_uploaded_session_names_the_file_being_edited(tmp_path, monkeypatch):
    """Two sheets look identical on screen. The owner must be able to tell which
    copy he is editing before he saves into it."""
    monkeypatch.setattr(gen, "RUNTIME", str(tmp_path / "runtime"))
    out = _filled(tmp_path)
    client = _client(tmp_path)
    with open(out, "rb") as fh:
        r = client.post("/preview", files={"file": ("august-batch.xlsx", fh.read(), XLSX)})
    page = client.get(r.headers["hx-redirect"]).text
    assert "Preview &amp; edit" in page or "Preview & edit" in page
    assert "august-batch.xlsx" in page
    assert "Fill attributes" not in page


def test_upload_with_no_sku_rows_is_rejected_and_creates_no_job(tmp_path, monkeypatch):
    """The bare template has no data rows. Adopting it would present an empty
    accordion with no explanation; the user needs to know it was the wrong file."""
    monkeypatch.setattr(gen, "RUNTIME", str(tmp_path / "runtime"))
    before = len(store._jobs)
    with open(V13, "rb") as fh:
        r = _client(tmp_path).post(
            "/preview", files={"file": ("template.xlsx", fh.read(), XLSX)})
    assert r.status_code == 200
    assert "hx-redirect" not in r.headers
    assert "no products" in r.text.lower()
    assert len(store._jobs) == before


def test_clear_forgets_the_job_and_removes_its_directory(tmp_path, monkeypatch):
    """Clear is how the owner moves to the next file. A job left behind would keep
    the uploaded sheet on disk for the life of the process."""
    monkeypatch.setattr(gen, "RUNTIME", str(tmp_path / "runtime"))
    out = _filled(tmp_path)
    client = _client(tmp_path)
    with open(out, "rb") as fh:
        r = client.post("/preview", files={"file": ("s.xlsx", fh.read(), XLSX)})
    job_id = r.headers["hx-redirect"].rsplit("/", 1)[1]
    job_dir = os.path.join(gen.RUNTIME, job_id)
    assert os.path.isdir(job_dir)

    c = client.post(f"/preview/clear/{job_id}")
    assert c.status_code == 200
    assert store.get(job_id) is None
    assert not os.path.exists(job_dir)
    assert c.headers["hx-redirect"] == "/preview"


def test_clear_on_an_unknown_job_is_not_an_error(tmp_path, monkeypatch):
    """Double-click, or a Clear after a restart. Neither should show a 404 page."""
    monkeypatch.setattr(gen, "RUNTIME", str(tmp_path / "runtime"))
    r = _client(tmp_path).post("/preview/clear/" + "a" * 32)
    assert r.status_code == 200
    assert r.headers["hx-redirect"] == "/preview"


def test_clear_asks_for_confirmation_once_edits_are_saved(tmp_path, monkeypatch):
    """The server copy is the only copy of a save that hasn't been downloaded.
    Before the first save Clear stays instant — that is the common flow."""
    monkeypatch.setattr(gen, "RUNTIME", str(tmp_path / "runtime"))
    out = _filled(tmp_path)
    client = _client(tmp_path)
    with open(out, "rb") as fh:
        r = client.post("/preview", files={"file": ("s.xlsx", fh.read(), XLSX)})
    target = r.headers["hx-redirect"]
    job_id = target.rsplit("/", 1)[1]
    assert "hx-confirm" not in client.get(target).text

    saved = client.post(f"/generate/attributes/{job_id}/one",
                        data={"ordinal": "0", "sku__0": "S1", "attr__0__0": ""})
    assert saved.status_code == 200
    assert "hx-confirm" in saved.text
    assert 'id="clear-slot"' in saved.text
