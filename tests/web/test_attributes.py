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


def _job(tmp_path, monkeypatch, skus=("S1", "S2"), with_images=True, tags=None):
    """A finished job on disk: built workbook + the Shopify export it came from."""
    warnings.filterwarnings("ignore")
    monkeypatch.setattr(gen, "RUNTIME", str(tmp_path / "runtime"))
    job = store.create()
    job_dir = os.path.join(gen.RUNTIME, job.id)
    os.makedirs(job_dir, exist_ok=True)

    t = read_template(V13)
    rows = []
    for s in skus:
        cells = {"vendorSkuCode": s, "brand": "Ijor"}
        if tags is not None:
            cells["tags"] = tags
        rows.append((MappedRow(sku=s, cells=cells), ImageResult(sku=s)))
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
    assert "1/13 filled" in r.text


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


def _brand_colour(xlsx, template):
    import openpyxl
    wb = openpyxl.load_workbook(xlsx, data_only=True)
    v = wb["Sarees"].cell(row=template.first_data_row,
                          column=template.col_index_by_header["Brand Colour (Remarks)"]).value
    wb.close()
    return v


def test_save_fills_brand_colour_from_the_prominent_colour(tmp_path, monkeypatch):
    """Myntra rejects a null Brand Colour (Remarks); the app derives it."""
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    _client(tmp_path).post(f"/generate/attributes/{job.id}",
                           data={"sku__0": "S1", "attr__0__0": "Blue"})
    assert _brand_colour(job.result["filled"], read_template(V13)) == "blue"


def test_save_leaves_brand_colour_blank_when_no_colour_chosen(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    _client(tmp_path).post(f"/generate/attributes/{job.id}",
                           data={"sku__0": "S1", "attr__0__7": "Zari"})
    assert _brand_colour(job.result["filled"], read_template(V13)) is None


def test_save_rewrites_brand_colour_when_the_colour_changes(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    client = _client(tmp_path)
    client.post(f"/generate/attributes/{job.id}",
                data={"sku__0": "S1", "attr__0__0": "Blue"})
    client.post(f"/generate/attributes/{job.id}",
                data={"sku__0": "S1", "attr__0__0": "Green"})
    assert _brand_colour(job.result["filled"], read_template(V13)) == "green"


def test_screen_shows_the_derived_brand_colour_read_only(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    client = _client(tmp_path)
    client.post(f"/generate/attributes/{job.id}",
                data={"sku__0": "S1", "attr__0__0": "Blue"})
    r = client.get(f"/generate/attributes/{job.id}")
    assert "Brand Colour (Remarks)" in r.text
    assert "filled automatically" in r.text
    assert ">blue<" in r.text
    # read-only: it must not be a form field the browser posts back
    assert 'name="brand_colour' not in r.text


def test_save_on_expired_job_says_session_expired(tmp_path, monkeypatch):
    monkeypatch.setattr(gen, "RUNTIME", str(tmp_path / "runtime"))
    r = _client(tmp_path).post("/generate/attributes/" + "0" * 32,
                               data={"sku__0": "S1"})
    assert r.status_code == 404


def _cell(xlsx, template, ordinal, header):
    import openpyxl
    wb = openpyxl.load_workbook(xlsx, data_only=True)
    v = wb["Sarees"].cell(row=template.first_data_row + ordinal,
                          column=template.col_index_by_header[header]).value
    wb.close()
    return v


def test_bulk_save_writes_tags_free_text(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    _client(tmp_path).post(f"/generate/attributes/{job.id}", data={
        "sku__0": "S1", "free__0__0": "saree, cotton, handloom"})
    assert _cell(job.result["filled"], read_template(V13), 0,
                 "tags") == "saree, cotton, handloom"


def test_bulk_save_accepts_tags_that_no_vocabulary_would_allow(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    r = _client(tmp_path).post(f"/generate/attributes/{job.id}", data={
        "sku__0": "S1", "free__0__0": "Salmon Pink"})
    assert "Saved" in r.text
    assert _cell(job.result["filled"], read_template(V13), 0, "tags") == "Salmon Pink"


def test_bulk_save_blank_tags_clears_the_cell(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    client = _client(tmp_path)
    client.post(f"/generate/attributes/{job.id}",
                data={"sku__0": "S1", "free__0__0": "keepme"})
    client.post(f"/generate/attributes/{job.id}",
                data={"sku__0": "S1", "free__0__0": "   "})
    assert _cell(job.result["filled"], read_template(V13), 0, "tags") is None


def test_bulk_save_still_writes_dropdowns_unchanged(tmp_path, monkeypatch):
    """Regression: the extracted helper must not change existing behaviour."""
    job = _job(tmp_path, monkeypatch, skus=("S1", "S2"))
    r = _client(tmp_path).post(f"/generate/attributes/{job.id}", data={
        "sku__0": "S1", "attr__0__5": "Banarasi",
        "sku__1": "S2", "attr__1__5": "Chanderi"})
    assert "Saved" in r.text
    t = read_template(V13)
    assert _cell(job.result["filled"], t, 0, "Type") == "Banarasi"
    assert _cell(job.result["filled"], t, 1, "Type") == "Chanderi"


def _free_input_value(html, ordinal=0, index=0):
    """The value= of one free-text input. A bare `'value=""' in html` check would
    pass on any empty <option>, so match the field itself."""
    import re
    m = re.search(r'name="free__%d__%d"\s+value="([^"]*)"' % (ordinal, index), html)
    assert m, "no free-text input rendered for that ordinal"
    return m.group(1)


def test_panel_renders_a_tags_input_prefilled_from_the_sheet(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",), tags="saree, cotton")
    r = _client(tmp_path).get(f"/generate/attributes/{job.id}")
    assert _free_input_value(r.text) == "saree, cotton"


def test_tags_input_is_empty_when_the_sheet_has_none(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    r = _client(tmp_path).get(f"/generate/attributes/{job.id}")
    assert _free_input_value(r.text) == ""


def test_filled_count_is_out_of_thirteen_and_counts_tags(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",), tags="festive")
    r = _client(tmp_path).get(f"/generate/attributes/{job.id}")
    assert "1/13 filled" in r.text


def test_count_span_is_addressable_for_out_of_band_updates(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    r = _client(tmp_path).get(f"/generate/attributes/{job.id}")
    assert 'id="attr-count-0"' in r.text


def test_saving_one_panel_writes_only_that_row(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1", "S2"))
    client = _client(tmp_path)
    client.post(f"/generate/attributes/{job.id}",
                data={"sku__0": "S1", "attr__0__5": "Banarasi",
                      "sku__1": "S2", "attr__1__5": "Chanderi"})
    # Now save ONLY panel 0, changing it. Panel 1 must survive untouched.
    r = client.post(f"/generate/attributes/{job.id}/one",
                    data={"sku__0": "S1", "attr__0__5": "Chanderi"})
    assert r.status_code == 200
    t = read_template(V13)
    assert _cell(job.result["filled"], t, 0, "Type") == "Chanderi"
    assert _cell(job.result["filled"], t, 1, "Type") == "Chanderi"  # unchanged


def test_one_panel_save_returns_the_refreshed_count_out_of_band(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    r = _client(tmp_path).post(f"/generate/attributes/{job.id}/one", data={
        "sku__0": "S1", "attr__0__5": "Banarasi", "free__0__0": "festive"})
    assert 'id="attr-count-0"' in r.text
    assert 'hx-swap-oob="true"' in r.text
    assert "2/13 filled" in r.text
    assert "Saved" in r.text


def test_one_panel_save_rejects_off_vocab_inline_without_writing(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    r = _client(tmp_path).post(f"/generate/attributes/{job.id}/one",
                               data={"sku__0": "S1", "attr__0__0": "Salmon Pink"})
    assert r.status_code == 200                 # inline error, not a 500
    assert "not one of Myntra" in r.text
    assert 'hx-swap-oob' not in r.text          # count must NOT be updated
    assert _cell(job.result["filled"], read_template(V13), 0,
                 "Prominent Colour") is None


def test_one_panel_save_keeps_dropdowns_alive(tmp_path, monkeypatch):
    import openpyxl
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    wb = openpyxl.load_workbook(job.result["filled"])
    before = len(wb["Sarees"].data_validations.dataValidation)
    wb.close()
    _client(tmp_path).post(f"/generate/attributes/{job.id}/one",
                           data={"sku__0": "S1", "attr__0__7": "Zari"})
    wb = openpyxl.load_workbook(job.result["filled"])
    assert len(wb["Sarees"].data_validations.dataValidation) == before
    wb.close()


def test_one_panel_save_holds_the_write_lock(tmp_path, monkeypatch):
    """The lock must be held across the read-modify-write, not merely to exist."""
    import src.web.routers.attributes as attrs
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    seen = {}
    real = attrs.write_attributes

    def spy(*a, **k):
        seen["locked"] = attrs._WRITE_LOCK.locked()
        return real(*a, **k)

    monkeypatch.setattr(attrs, "write_attributes", spy)
    _client(tmp_path).post(f"/generate/attributes/{job.id}/one",
                           data={"sku__0": "S1", "attr__0__7": "Zari"})
    assert seen["locked"] is True


def test_one_panel_save_on_expired_job_says_session_expired(tmp_path, monkeypatch):
    monkeypatch.setattr(gen, "RUNTIME", str(tmp_path / "runtime"))
    r = _client(tmp_path).post("/generate/attributes/" + "0" * 32 + "/one",
                               data={"sku__0": "S1"})
    assert r.status_code == 404


def test_panel_has_a_save_button_that_posts_only_its_own_fields(tmp_path, monkeypatch):
    import re
    job = _job(tmp_path, monkeypatch, skus=("S1", "S2"))
    r = _client(tmp_path).get(f"/generate/attributes/{job.id}")
    buttons = re.findall(r"<button[^>]*hx-post=\"/generate/attributes/[^\"]+/one\"[^>]*>",
                         r.text)
    assert len(buttons) == 2
    # Load-bearing: a default submit button would post every panel at once. Counting
    # type="button" across the whole page would also match unrelated chrome.
    assert all('type="button"' in b for b in buttons)
    assert all('hx-include="closest .attr-panel"' in b for b in buttons)
    assert 'id="attr-save-0"' in r.text and 'id="attr-save-1"' in r.text
