import io
from unittest import mock

from fastapi.testclient import TestClient

from src.web.main import create_app
from src.web.settings import Settings
import src.web.routers.generate as gen


def _client(tmp_path):
    s = Settings(auth_disabled=True, s3_bucket="b",
                 ledger_local_path=str(tmp_path / "led.json"),
                 hsn_local_path=str(tmp_path / "hsn.json"),
                 sku_registry_local_path=str(tmp_path / "reg.json"))
    return TestClient(create_app(s)), s


def _pass_hsn_and_wait(client, job_id, hsn="12345678"):
    """Submit the single-signature HSN review, then poll until the sheet is ready.
    The default test CSV (Handle,Title only) yields one signature: saree|unknown."""
    import time
    poll = client.post(f"/generate/hsn/{job_id}", data={"hsn__0": hsn})
    for _ in range(20):
        if "Download" in poll.text:
            return poll
        time.sleep(0.05)
        poll = client.get(f"/jobs/{job_id}")
    return poll


def test_generate_rejects_non_csv(tmp_path):
    client, _ = _client(tmp_path)
    r = client.post("/generate", files={"file": ("notes.txt", b"hi", "text/plain")})
    assert r.status_code == 400


def test_generate_runs_job_and_confirm_advances_ledger(tmp_path, monkeypatch):
    client, settings = _client(tmp_path)

    # Stub the heavy pipeline: pretend it wrote a file for 3 products.
    def fake_main(csv_path=None, out_dir=None, style_group_id_start=None, **kw):
        with open(f"{out_dir}/myntra_filled.xlsx", "wb") as fh:
            fh.write(b"xlsx-bytes")
        with open(f"{out_dir}/report.txt", "w") as fh:
            fh.write("3 rows\n1 vocab flag: Ivory\n")
        return {"filled": f"{out_dir}/myntra_filled.xlsx",
                "report": f"{out_dir}/report.txt", "products": 3, "uploaded": 9}

    monkeypatch.setattr(gen, "pipeline_main", fake_main)
    # count products from CSV deterministically (3 data rows)
    monkeypatch.setattr(gen, "count_products", lambda path: 3)

    csv = b"Handle,Title\na,A\nb,B\nc,C\n"
    r = client.post("/generate", files={"file": ("products_export.csv", csv, "text/csv")})
    assert r.status_code == 200
    assert "One-time HSN" in r.text                 # pre-scan paused for HSN
    job_id = r.headers["x-job-id"]

    poll = _pass_hsn_and_wait(client, job_id)
    assert poll.status_code == 200
    assert "Download" in poll.text
    assert "1 –" in poll.text or "1 - 3" in poll.text or "1 – 3" in poll.text  # range shown

    # ledger started empty (next id 1) -> reserve was [1,3]; confirm advances to 4
    rc = client.post(f"/generate/confirm/{job_id}")
    assert rc.status_code == 200
    from src.myntra.groupid_ledger import read_ledger
    from src.web.settings import ledger_store
    led = read_ledger(ledger_store(settings))
    assert led["next_style_group_id"] == 4


def test_confirm_then_undo_rolls_ledger_back(tmp_path, monkeypatch):
    client, settings = _client(tmp_path)

    def fake_main(csv_path=None, out_dir=None, style_group_id_start=None, **kw):
        with open(f"{out_dir}/myntra_filled.xlsx", "wb") as fh:
            fh.write(b"x")
        with open(f"{out_dir}/report.txt", "w") as fh:
            fh.write("r\n")
        return {"filled": f"{out_dir}/myntra_filled.xlsx",
                "report": f"{out_dir}/report.txt", "products": 3, "uploaded": 0}

    monkeypatch.setattr(gen, "pipeline_main", fake_main)
    monkeypatch.setattr(gen, "count_products", lambda path: 3)

    csv = b"Handle,Title\na,A\nb,B\nc,C\n"
    r = client.post("/generate", files={"file": ("products_export.csv", csv, "text/csv")})
    job_id = r.headers["x-job-id"]
    _pass_hsn_and_wait(client, job_id)

    from src.myntra.groupid_ledger import read_ledger
    from src.web.settings import ledger_store

    rc = client.post(f"/generate/confirm/{job_id}")
    assert "Undo" in rc.text
    assert read_ledger(ledger_store(settings))["next_style_group_id"] == 4

    ru = client.post(f"/generate/unconfirm/{job_id}")
    assert "Mark upload successful" in ru.text
    assert read_ledger(ledger_store(settings))["next_style_group_id"] == 1


def test_result_screen_shows_verify_notice(tmp_path, monkeypatch):
    client, settings = _client(tmp_path)

    def fake_main(csv_path=None, out_dir=None, style_group_id_start=None, **kw):
        with open(f"{out_dir}/myntra_filled.xlsx", "wb") as fh:
            fh.write(b"xlsx-bytes")
        with open(f"{out_dir}/report.txt", "w") as fh:
            fh.write("3 rows\n")
        return {"filled": f"{out_dir}/myntra_filled.xlsx",
                "report": f"{out_dir}/report.txt", "products": 3, "uploaded": 9}

    monkeypatch.setattr(gen, "pipeline_main", fake_main)
    monkeypatch.setattr(gen, "count_products", lambda path: 3)

    csv = b"Handle,Title\na,A\nb,B\nc,C\n"
    r = client.post("/generate", files={"file": ("products_export.csv", csv, "text/csv")})
    poll = _pass_hsn_and_wait(client, r.headers["x-job-id"])
    assert "verify the downloaded file yourself" in poll.text.lower()


def test_style_start_set_and_undo(tmp_path):
    client, settings = _client(tmp_path)
    from src.myntra.groupid_ledger import read_ledger
    from src.web.settings import ledger_store

    r = client.post("/generate/style-start", data={"last_used": "40"})
    assert r.status_code == 200
    assert "41" in r.text
    assert read_ledger(ledger_store(settings))["next_style_group_id"] == 41

    ru = client.post("/generate/style-start/undo")
    assert ru.status_code == 200
    assert read_ledger(ledger_store(settings))["next_style_group_id"] == 1


def test_hsn_review_lists_signature_and_learns_on_submit(tmp_path, monkeypatch):
    client, settings = _client(tmp_path)

    def fake_main(csv_path=None, out_dir=None, style_group_id_start=None,
                  hsn_by_signature=None, **kw):
        # the learned map reaches the pipeline
        assert hsn_by_signature == {"saree|unknown": "63079090"}
        with open(f"{out_dir}/myntra_filled.xlsx", "wb") as fh:
            fh.write(b"x")
        with open(f"{out_dir}/report.txt", "w") as fh:
            fh.write("r\n")
        return {"filled": f"{out_dir}/myntra_filled.xlsx",
                "report": f"{out_dir}/report.txt", "products": 1, "uploaded": 0}

    monkeypatch.setattr(gen, "pipeline_main", fake_main)
    monkeypatch.setattr(gen, "count_products", lambda path: 1)

    csv = b"Handle,Title\na,Plain Saree\n"
    r = client.post("/generate", files={"file": ("products_export.csv", csv, "text/csv")})
    assert "saree|unknown" in r.text
    job_id = r.headers["x-job-id"]

    ready = _pass_hsn_and_wait(client, job_id, hsn="63079090")
    assert "Download" in ready.text

    from src.myntra.hsn_kb import read_kb, suggest
    from src.web.settings import hsn_store
    kb = read_kb(hsn_store(settings))
    assert suggest(kb, "saree|unknown")[0]["hsn"] == "63079090"


def test_hsn_invalid_code_rerenders_with_error(tmp_path, monkeypatch):
    client, settings = _client(tmp_path)
    monkeypatch.setattr(gen, "count_products", lambda path: 1)

    csv = b"Handle,Title\na,Plain Saree\n"
    r = client.post("/generate", files={"file": ("products_export.csv", csv, "text/csv")})
    job_id = r.headers["x-job-id"]

    bad = client.post(f"/generate/hsn/{job_id}", data={"hsn__0": "123"})   # not 8 digits
    assert "exactly 8 digits" in bad.text
    assert 'value="123"' in bad.text                    # entered value preserved
    from src.myntra.groupid_ledger import read_ledger
    from src.web.settings import ledger_store
    assert read_ledger(ledger_store(settings))["next_style_group_id"] == 1  # not built


def test_generate_form_still_renders(tmp_path):
    client, _ = _client(tmp_path)
    assert client.get("/generate").status_code == 200


def test_static_assets_are_cache_busted(tmp_path):
    # The stylesheet link must carry a version token, otherwise browsers cache
    # app.css and CSS edits never show without a manual hard refresh.
    client, _ = _client(tmp_path)
    import re
    assert re.search(r"app\.css\?v=\d+", client.get("/generate").text)


def test_build_records_registry(tmp_path, monkeypatch):
    client, settings = _client(tmp_path)

    def fake_main(csv_path=None, out_dir=None, style_group_id_start=None,
                  hsn_by_signature=None, only_skus=None, **kw):
        with open(f"{out_dir}/myntra_filled.xlsx", "wb") as fh:
            fh.write(b"x")
        with open(f"{out_dir}/report.txt", "w") as fh:
            fh.write("r\n")
        return {"filled": f"{out_dir}/myntra_filled.xlsx", "report": f"{out_dir}/report.txt",
                "products": 1, "uploaded": 0,
                "records": [{"sku": "S1", "style_group_id": 13, "hsn": "50072010",
                             "content_hash": "h1"}]}

    monkeypatch.setattr(gen, "pipeline_main", fake_main)
    monkeypatch.setattr(gen, "count_products", lambda path: 1)

    csv = b"Handle,Title\na,Plain Saree\n"   # SKU empty -> partition NEW, proceeds
    r = client.post("/generate", files={"file": ("products_export.csv", csv, "text/csv")})
    _pass_hsn_and_wait(client, r.headers["x-job-id"])

    from src.myntra.sku_registry import read_registry
    from src.web.settings import sku_registry_store
    reg = read_registry(sku_registry_store(settings))
    assert reg["S1"]["style_group_id"] == 13 and reg["S1"]["hsn"] == "50072010"


def test_repeat_upload_warns_and_skips_hsn(tmp_path):
    client, settings = _client(tmp_path)
    # Pre-seed the registry with the fixture's real hashes so the re-upload is a repeat.
    from src.myntra.pipeline import scan_content_hashes
    from src.myntra.sku_registry import record
    from src.web.settings import sku_registry_store
    store = sku_registry_store(settings)
    for sku, h in scan_content_hashes("tests/fixtures/products_export.csv"):
        record(store, sku, h, 55, "50072010")

    with open("tests/fixtures/products_export.csv", "rb") as fh:
        csv = fh.read()
    r = client.post("/generate", files={"file": ("products_export.csv", csv, "text/csv")})
    assert "already generated" in r.text.lower()
    assert "One-time HSN" not in r.text          # HSN review skipped for a pure repeat


def test_rebuild_download_serves_xlsx_with_pinned_values(tmp_path, monkeypatch):
    client, settings = _client(tmp_path)
    from src.myntra.pipeline import scan_content_hashes
    from src.myntra.sku_registry import record
    from src.web.settings import sku_registry_store
    store = sku_registry_store(settings)
    pinned = {}
    for i, (sku, h) in enumerate(scan_content_hashes("tests/fixtures/products_export.csv")):
        record(store, sku, h, 55 + i, "50072010")
        pinned[sku] = 55 + i

    seen = {}

    def fake_main(csv_path=None, out_dir=None, only_skus=None,
                  style_group_id_by_sku=None, hsn_by_sku=None, **kw):
        seen["ids"] = style_group_id_by_sku
        seen["hsn"] = hsn_by_sku
        with open(f"{out_dir}/myntra_filled.xlsx", "wb") as fh:
            fh.write(b"xlsx")
        return {"filled": f"{out_dir}/myntra_filled.xlsx", "report": "", "products": 2,
                "uploaded": 0, "records": []}

    monkeypatch.setattr(gen, "pipeline_main", fake_main)

    with open("tests/fixtures/products_export.csv", "rb") as fh:
        csv = fh.read()
    r = client.post("/generate", files={"file": ("products_export.csv", csv, "text/csv")})
    job_id = r.headers["x-job-id"]
    dl = client.get(f"/generate/rebuild/{job_id}")
    assert dl.status_code == 200
    assert dl.content == b"xlsx"
    assert seen["ids"] == pinned                       # pinned styleGroupIds forced
    assert set(seen["hsn"].values()) == {"50072010"}   # pinned HSN forced
    # ledger untouched by a rebuild
    from src.myntra.groupid_ledger import read_ledger
    from src.web.settings import ledger_store
    assert read_ledger(ledger_store(settings))["next_style_group_id"] == 1


def test_generate_form_has_no_hidden_required_field(tmp_path):
    # A `required` input inside the hidden style-edit div blocks the whole Generate
    # form: the browser can't focus a display:none required field, so the submit is
    # silently aborted. The styleGroupId input must NOT be `required`.
    client, _ = _client(tmp_path)
    html = client.get("/generate").text
    assert 'name="last_used"' in html
    marker = html.index('name="last_used"')
    input_tag = html[html.rindex("<input", 0, marker):html.index(">", marker)]
    assert "required" not in input_tag
