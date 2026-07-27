import os
import warnings

from fastapi.testclient import TestClient

from src.core.models import MappedRow, ImageResult
from src.myntra.fill import fill_template
from src.myntra.template_reader import read_template
from src.web.jobs import store
from src.web.main import create_app
from src.web.settings import Settings
import src.web.routers.generate as gen

V13 = "templates/myntra/Myntra-Sku-Template-2026-07-24.xlsx"


def _client(tmp_path):
    s = Settings(auth_disabled=True, s3_bucket="b",
                 ledger_local_path=str(tmp_path / "led.json"),
                 hsn_local_path=str(tmp_path / "hsn.json"),
                 sku_registry_local_path=str(tmp_path / "reg.json"))
    return TestClient(create_app(s))


def _job(tmp_path, monkeypatch, skus=("S1", "S2"), with_images=True):
    """A finished job on disk: built workbook + the Shopify export it came from."""
    warnings.filterwarnings("ignore")
    monkeypatch.setattr(gen, "RUNTIME", str(tmp_path / "runtime"))
    job = store.create()
    job_dir = os.path.join(gen.RUNTIME, job.id)
    os.makedirs(job_dir, exist_ok=True)

    t = read_template(V13)
    rows = [(MappedRow(sku=s, cells={"vendorSkuCode": s, "brand": "Ijor"}),
             ImageResult(sku=s)) for s in skus]
    xlsx = os.path.join(job_dir, "myntra_filled.xlsx")
    fill_template(V13, t, rows, xlsx)

    with open(os.path.join(job_dir, "products_export.csv"), "w",
              newline="", encoding="utf-8") as fh:
        fh.write("Handle,Title,Variant SKU,Image Src,Image Position\n")
        for i, s in enumerate(skus, start=1):
            img = f"https://cdn.example/{s}.jpg" if with_images else ""
            fh.write(f"h{i},Product {i},{s},{img},1\n")

    job.status = "done"
    job.result = {"filled": xlsx, "report": "", "products": len(skus), "uploaded": 0}
    return job


def test_screen_renders_a_panel_per_sku_with_twelve_selects(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch)
    r = _client(tmp_path).get(f"/generate/attributes/{job.id}")
    assert r.status_code == 200
    assert r.text.count('class="attr-panel"') == 2
    assert r.text.count('name="attr__0__') == 12          # 12 dropdowns for SKU 1
    assert 'name="sku__0"' in r.text and 'value="S1"' in r.text
    assert "https://cdn.example/S1.jpg" in r.text          # photo from the export
    assert "Product 1" in r.text                           # title from the export


def test_dropdown_options_come_only_from_the_template_vocab(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    r = _client(tmp_path).get(f"/generate/attributes/{job.id}")
    assert "<option value=\"\">— choose —</option>" in r.text
    assert ">Banarasi<" in r.text          # a real Type value
    assert ">Zari<" in r.text              # a real Ornamentation value
    assert ">Salmon Pink<" not in r.text   # not in Myntra's colour list


def test_existing_values_are_preselected(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    from src.myntra.attribute_entry import write_attributes
    t = read_template(V13)
    write_attributes(job.result["filled"], t,
                     [{"ordinal": 0, "sku": "S1", "values": {"Border": "Zari"}}])
    r = _client(tmp_path).get(f"/generate/attributes/{job.id}")
    assert '<option value="Zari" selected>Zari</option>' in r.text
    assert "1/12 filled" in r.text


def test_missing_image_falls_back_to_placeholder(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",), with_images=False)
    r = _client(tmp_path).get(f"/generate/attributes/{job.id}")
    assert r.status_code == 200
    assert "no photo" in r.text


def test_unknown_job_says_session_expired(tmp_path, monkeypatch):
    monkeypatch.setattr(gen, "RUNTIME", str(tmp_path / "runtime"))
    r = _client(tmp_path).get("/generate/attributes/" + "0" * 32)
    assert r.status_code == 404


def test_live_preview_reconstructs_from_posted_values(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    # column indexes: 0 Prominent Colour .. 5 Type .. 9 Print or Pattern Type
    r = _client(tmp_path).post(f"/generate/attributes/{job.id}/preview", data={
        "sku__0": "S1", "attr__0__0": "Blue", "attr__0__3": "Pure Silk",
        "attr__0__5": "Banarasi", "attr__0__9": "Floral"})
    assert r.status_code == 200
    assert "Floral Pure Silk Banarasi Saree" in r.text
    assert "Blue Banarasi sarees" in r.text


def test_save_writes_all_skus_and_download_serves_the_updated_file(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1", "S2"))
    client = _client(tmp_path)
    r = client.post(f"/generate/attributes/{job.id}", data={
        "sku__0": "S1", "attr__0__5": "Banarasi", "attr__0__7": "Zari",
        "sku__1": "S2", "attr__1__5": "Chanderi"})
    assert r.status_code == 200
    assert "Saved" in r.text

    t = read_template(V13)
    import openpyxl
    wb = openpyxl.load_workbook(job.result["filled"], data_only=True)
    ws = wb["Sarees"]
    assert ws.cell(row=t.first_data_row,
                   column=t.col_index_by_header["Type"]).value == "Banarasi"
    assert ws.cell(row=t.first_data_row,
                   column=t.col_index_by_header["Border"]).value == "Zari"
    assert ws.cell(row=t.first_data_row + 1,
                   column=t.col_index_by_header["Type"]).value == "Chanderi"
    assert ws.cell(row=t.first_data_row,
                   column=t.col_index_by_header["Pattern"]).value is None
    wb.close()

    d = client.get(f"/generate/download/{job.id}")
    assert d.status_code == 200


def test_save_reopens_with_values_selected(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    client = _client(tmp_path)
    client.post(f"/generate/attributes/{job.id}",
                data={"sku__0": "S1", "attr__0__7": "Zari"})
    r = client.get(f"/generate/attributes/{job.id}")
    assert '<option value="Zari" selected>Zari</option>' in r.text


def test_save_rejects_off_vocab_value_without_writing(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    client = _client(tmp_path)
    r = client.post(f"/generate/attributes/{job.id}",
                    data={"sku__0": "S1", "attr__0__0": "Salmon Pink"})
    assert r.status_code == 200          # htmx-swappable error panel, not a 500
    assert "Prominent Colour" in r.text
    assert "not one of Myntra" in r.text
    t = read_template(V13)
    import openpyxl
    wb = openpyxl.load_workbook(job.result["filled"], data_only=True)
    v = wb["Sarees"].cell(row=t.first_data_row,
                          column=t.col_index_by_header["Prominent Colour"]).value
    wb.close()
    assert v is None


def test_save_keeps_dropdowns_alive_in_the_downloaded_file(tmp_path, monkeypatch):
    """KEY INVARIANT end-to-end: the owner's Excel check must still pass."""
    import openpyxl
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    wb = openpyxl.load_workbook(job.result["filled"])
    before = len(wb["Sarees"].data_validations.dataValidation)
    wb.close()
    _client(tmp_path).post(f"/generate/attributes/{job.id}",
                           data={"sku__0": "S1", "attr__0__7": "Zari"})
    wb = openpyxl.load_workbook(job.result["filled"])
    assert len(wb["Sarees"].data_validations.dataValidation) == before
    wb.close()


def test_save_on_expired_job_says_session_expired(tmp_path, monkeypatch):
    monkeypatch.setattr(gen, "RUNTIME", str(tmp_path / "runtime"))
    r = _client(tmp_path).post("/generate/attributes/" + "0" * 32,
                               data={"sku__0": "S1"})
    assert r.status_code == 404
