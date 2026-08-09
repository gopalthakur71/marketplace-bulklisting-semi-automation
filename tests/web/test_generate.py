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


def _wait(client, job_id):
    """Poll until the sheet is ready. There is no HSN question any more — the
    build starts as soon as the duplicate guard is satisfied."""
    import time
    poll = client.get(f"/jobs/{job_id}")
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
    assert "One-time HSN" not in r.text            # no HSN question any more
    job_id = r.headers["x-job-id"]

    poll = _wait(client, job_id)
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
    _wait(client, job_id)

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
    poll = _wait(client, r.headers["x-job-id"])
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


def test_no_hsn_screen_and_the_route_is_gone(tmp_path, monkeypatch):
    client, _ = _client(tmp_path)

    def fake_main(csv_path=None, out_dir=None, style_group_id_start=None, **kw):
        assert "hsn_by_signature" not in kw       # the parameter is gone for good
        with open(f"{out_dir}/myntra_filled.xlsx", "wb") as fh:
            fh.write(b"xlsx-bytes")
        with open(f"{out_dir}/report.txt", "w") as fh:
            fh.write("1 rows\n")
        return {"filled": f"{out_dir}/myntra_filled.xlsx",
                "report": f"{out_dir}/report.txt", "products": 1, "uploaded": 0}

    monkeypatch.setattr(gen, "pipeline_main", fake_main)
    monkeypatch.setattr(gen, "count_products", lambda path: 1)

    csv = b"Handle,Title\na,A\n"
    r = client.post("/generate", files={"file": ("products_export.csv", csv, "text/csv")})
    assert r.status_code == 200
    assert "One-time HSN" not in r.text
    job_id = r.headers["x-job-id"]
    assert "Download" in _wait(client, job_id).text

    # The old route is gone, not merely unused.
    assert client.post(f"/generate/hsn/{job_id}", data={"hsn__0": "12345678"}
                       ).status_code == 404


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
                  only_skus=None, **kw):
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
    _wait(client, r.headers["x-job-id"])

    from src.myntra.sku_registry import read_registry
    from src.web.settings import sku_registry_store
    reg = read_registry(sku_registry_store(settings))
    assert reg["S1"]["style_group_id"] == 13 and reg["S1"]["hsn"] == "50072010"


def test_repeat_upload_warns_instead_of_building(tmp_path):
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
    # The guard stops short of the build: no styleGroupId range was reserved.
    from src.myntra.groupid_ledger import read_ledger
    from src.web.settings import ledger_store
    led = read_ledger(ledger_store(settings))
    assert led["next_style_group_id"] == 1 and led["batches"] == []


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


def test_generate_new_only_builds_and_records_only_new(tmp_path, monkeypatch):
    client, settings = _client(tmp_path)
    from src.myntra.pipeline import scan_content_hashes
    from src.myntra.sku_registry import record, read_registry
    from src.web.settings import sku_registry_store

    pairs = scan_content_hashes("tests/fixtures/products_export.csv")
    store = sku_registry_store(settings)
    # Seed ONLY the first SKU as already-generated -> the file is mixed.
    first_sku, first_hash = pairs[0]
    new_sku = pairs[1][0]
    record(store, first_sku, first_hash, 55, "50072010")

    built = {}

    def fake_main(csv_path=None, out_dir=None, style_group_id_start=None,
                  only_skus=None, **kw):
        built["only_skus"] = only_skus
        with open(f"{out_dir}/myntra_filled.xlsx", "wb") as fh:
            fh.write(b"x")
        with open(f"{out_dir}/report.txt", "w") as fh:
            fh.write("r\n")
        return {"filled": f"{out_dir}/myntra_filled.xlsx", "report": f"{out_dir}/report.txt",
                "products": 1, "uploaded": 0,
                "records": [{"sku": new_sku, "style_group_id": 1, "hsn": "63079090",
                             "content_hash": pairs[1][1]}]}

    monkeypatch.setattr(gen, "pipeline_main", fake_main)

    with open("tests/fixtures/products_export.csv", "rb") as fh:
        csv = fh.read()
    r = client.post("/generate", files={"file": ("products_export.csv", csv, "text/csv")})
    job_id = r.headers["x-job-id"]
    assert "already generated" in r.text.lower()

    # Choose "generate new only" -> the build starts at once, no HSN question.
    r2 = client.post(f"/generate/new-only/{job_id}")
    assert "One-time HSN" not in r2.text
    _wait(client, job_id)

    assert built["only_skus"] == {new_sku}
    reg = read_registry(sku_registry_store(settings))
    assert new_sku in reg                      # new SKU recorded


def test_generate_continue_anyway_rebuilds_all_pinning_repeat_ids(tmp_path, monkeypatch):
    import time
    client, settings = _client(tmp_path)
    from src.myntra.pipeline import scan_content_hashes
    from src.myntra.sku_registry import record, read_registry
    from src.web.settings import sku_registry_store

    pairs = scan_content_hashes("tests/fixtures/products_export.csv")
    store = sku_registry_store(settings)
    repeat_sku, repeat_hash = pairs[0]
    other_sku = pairs[1][0]
    record(store, repeat_sku, repeat_hash, 55, "50072010")

    built = {}

    def fake_main(csv_path=None, out_dir=None, style_group_id_start=None,
                  only_skus=None, style_group_id_by_sku=None, **kw):
        built["only_skus"] = only_skus
        built["ids"] = style_group_id_by_sku
        with open(f"{out_dir}/myntra_filled.xlsx", "wb") as fh:
            fh.write(b"x")
        with open(f"{out_dir}/report.txt", "w") as fh:
            fh.write("r\n")
        return {"filled": f"{out_dir}/myntra_filled.xlsx", "report": f"{out_dir}/report.txt",
                "products": 2, "uploaded": 0,
                "records": [{"sku": s, "style_group_id": 55 + i, "hsn": "63079090",
                             "content_hash": h} for i, (s, h) in enumerate(pairs)]}

    monkeypatch.setattr(gen, "pipeline_main", fake_main)

    with open("tests/fixtures/products_export.csv", "rb") as fh:
        csv = fh.read()
    r = client.post("/generate", files={"file": ("products_export.csv", csv, "text/csv")})
    job_id = r.headers["x-job-id"]
    assert "continue anyway" in r.text.lower()

    r2 = client.post(f"/generate/continue/{job_id}")
    assert "One-time HSN" not in r2.text           # the rebuild starts at once
    poll = client.get(f"/jobs/{job_id}")
    for _ in range(20):
        if "Download" in poll.text:
            break
        time.sleep(0.05)
        poll = client.get(f"/jobs/{job_id}")
    assert "Download" in poll.text

    assert built["only_skus"] is None              # every SKU in the file is rebuilt
    assert built["ids"] == {repeat_sku: 55}        # the repeat keeps its original id
    reg = read_registry(sku_registry_store(settings))
    assert repeat_sku in reg and other_sku in reg


def test_continue_anyway_also_pins_edited_skus(tmp_path, monkeypatch):
    """An 'edited' SKU (content changed since it was generated) is still live on
    Myntra under its original style number, so a rework must keep that number too.

    Asserted on what reaches the pipeline: the pinned ids used to be inspectable
    in the job's hsn.json, but the HSN question — and that file — are gone."""
    client, settings = _client(tmp_path)
    from src.myntra.pipeline import scan_content_hashes
    from src.myntra.sku_registry import record
    from src.web.settings import sku_registry_store

    pairs = scan_content_hashes("tests/fixtures/products_export.csv")
    store = sku_registry_store(settings)
    record(store, pairs[0][0], pairs[0][1], 55, "50072010")        # unchanged -> repeat
    record(store, pairs[1][0], "stalehash", 56, "50072010")        # changed   -> edited

    built = {}

    def fake_main(csv_path=None, out_dir=None, only_skus=None,
                  style_group_id_by_sku=None, **kw):
        built["only_skus"] = only_skus
        built["ids"] = style_group_id_by_sku
        with open(f"{out_dir}/myntra_filled.xlsx", "wb") as fh:
            fh.write(b"x")
        with open(f"{out_dir}/report.txt", "w") as fh:
            fh.write("r\n")
        return {"filled": f"{out_dir}/myntra_filled.xlsx", "report": f"{out_dir}/report.txt",
                "products": 2, "uploaded": 0, "records": []}

    monkeypatch.setattr(gen, "pipeline_main", fake_main)

    with open("tests/fixtures/products_export.csv", "rb") as fh:
        csv = fh.read()
    r = client.post("/generate", files={"file": ("products_export.csv", csv, "text/csv")})
    job_id = r.headers["x-job-id"]
    client.post(f"/generate/continue/{job_id}")
    _wait(client, job_id)

    assert built["ids"] == {pairs[0][0]: 55, pairs[1][0]: 56}
    assert built["only_skus"] is None


def test_continue_anyway_without_dedup_session_is_404(tmp_path):
    client, _ = _client(tmp_path)
    r = client.post("/generate/continue/" + "0" * 32)
    assert r.status_code == 404


def _start_blocking_build(client, monkeypatch, tmp_path):
    """Kick off a build whose pipeline parks at its cancel checkpoint, like the real
    one does between products. Returns the job id, with the build still running."""
    import time
    from src.myntra.pipeline import BuildCancelled

    def fake_main(csv_path=None, out_dir=None, should_cancel=None, **kw):
        # a part-written workbook exists when the stop lands, as in a real run
        with open(f"{out_dir}/myntra_filled.xlsx", "wb") as fh:
            fh.write(b"half-written")
        for _ in range(400):
            if should_cancel is not None and should_cancel():
                raise BuildCancelled()
            time.sleep(0.01)
        raise AssertionError("cancel was never signalled to the pipeline")

    monkeypatch.setattr(gen, "pipeline_main", fake_main)
    monkeypatch.setattr(gen, "count_products", lambda path: 3)

    csv = b"Handle,Title\na,A\nb,B\nc,C\n"
    r = client.post("/generate", files={"file": ("products_export.csv", csv, "text/csv")})
    return r.headers["x-job-id"]        # the upload itself starts the build now


def _poll_until(client, job_id, needle, tries=60):
    import time
    poll = client.get(f"/jobs/{job_id}")
    for _ in range(tries):
        if needle in poll.text.lower():
            return poll
        time.sleep(0.05)
        poll = client.get(f"/jobs/{job_id}")
    return poll


def test_stop_ends_the_run_without_consuming_ids_or_recording_skus(tmp_path, monkeypatch):
    import os
    client, settings = _client(tmp_path)
    job_id = _start_blocking_build(client, monkeypatch, tmp_path)

    rc = client.post(f"/generate/cancel/{job_id}")
    assert rc.status_code == 200

    poll = _poll_until(client, job_id, "stopped")
    assert "stopped" in poll.text.lower()
    assert gen.store.get(job_id).status == "cancelled"
    assert gen.store.get(job_id).error is None          # cancelled is not a failure

    # the half-written workbook is gone, so nothing broken can be downloaded
    assert not os.path.exists(os.path.join(gen.RUNTIME, job_id, "myntra_filled.xlsx"))

    from src.myntra.groupid_ledger import read_ledger
    from src.web.settings import ledger_store, sku_registry_store
    from src.myntra.sku_registry import read_registry
    led = read_ledger(ledger_store(settings))
    assert [b["status"] for b in led["batches"]] == ["cancelled"]
    assert led["next_style_group_id"] == 1              # no ids burned
    assert read_registry(sku_registry_store(settings)) == {}   # no SKUs recorded


def test_cancel_unknown_job_is_404(tmp_path):
    client, _ = _client(tmp_path)
    assert client.post(f"/generate/cancel/{'a' * 32}").status_code == 404


def test_stop_appears_beside_generate_while_running_and_goes_when_done(tmp_path, monkeypatch):
    client, _ = _client(tmp_path)
    job_id = _start_blocking_build(client, monkeypatch, tmp_path)

    running = client.get(f"/jobs/{job_id}")
    assert 'hx-swap-oob' in running.text and "run-controls" in running.text
    assert f"/generate/cancel/{job_id}" in running.text     # Stop is offered

    client.post(f"/generate/cancel/{job_id}")
    done = _poll_until(client, job_id, "stopped")
    assert "run-controls" in done.text                      # slot is still swapped…
    assert "/generate/cancel/" not in done.text             # …but Stop is withdrawn


def test_stop_does_not_re_upload_the_csv(tmp_path, monkeypatch):
    # Stop lives inside the upload form, and htmx posts an enclosing form's values by
    # default — including the chosen file, under the form's multipart encoding. Without
    # hx-params="none" every Stop click would re-upload the whole CSV.
    client, _ = _client(tmp_path)
    job_id = _start_blocking_build(client, monkeypatch, tmp_path)
    running = client.get(f"/jobs/{job_id}")
    stop_tag = running.text[running.text.index("/generate/cancel/"):]
    stop_tag = stop_tag[:stop_tag.index(">")]
    assert 'hx-params="none"' in stop_tag
    client.post(f"/generate/cancel/{job_id}")      # let the worker finish


def test_generate_form_has_clear_button_and_a_slot_for_stop(tmp_path):
    client, _ = _client(tmp_path)
    html = client.get("/generate").text
    assert 'id="run-controls"' in html      # where Stop is swapped in during a run
    assert "Clear" in html


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
