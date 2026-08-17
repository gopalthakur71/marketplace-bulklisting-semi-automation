import os

from fastapi.testclient import TestClient

from src.web.main import create_app
from src.web.settings import Settings
import src.web.routers.fix as fixmod
import src.web.routers.preview as previewmod
from src.myntra.error_sources import ErrorItem

V13 = "templates/myntra/Myntra-Sku-Template-2026-07-24.xlsx"


def _client(raise_server=True):
    return TestClient(create_app(Settings(auth_disabled=True, s3_bucket="b")),
                      raise_server_exceptions=raise_server)


def _items():
    return [
        ErrorItem(sku="78SAZ", style_id=None, source_type="sku_xlsx", scope="sku",
                  raw_reason="Brand Colour (Remarks) cannot be null",
                  cells={"vendorSkuCode": "78SAZ", "Prominent Colour": "Ivory"}),
        ErrorItem(sku="IMG1", style_id=None, source_type="sku_xlsx", scope="sku",
                  raw_reason="Primary image appears to be a flat shot",
                  cells={"vendorSkuCode": "IMG1"}),
    ]


def _lr_correctable():
    """A Listings-Report item that matches an auto_fix rule -> correctable, so its
    SKU joins the Surface-B rebuild set (which needs the Shopify export)."""
    return [
        ErrorItem(sku="LR1", style_id=None, source_type="listings_report", scope="sku",
                  raw_reason="Pincode is missing", cells={}),
    ]


def _lr_mixed():
    """One correctable (LR1, auto_fix pincode) + one explain-only (IMGX, no rule match)."""
    from src.myntra.error_sources import ErrorItem
    return [
        ErrorItem(sku="LR1", style_id=None, source_type="listings_report", scope="sku",
                  raw_reason="Pincode is missing", cells={}),
        ErrorItem(sku="IMGX", style_id=None, source_type="listings_report", scope="sku",
                  raw_reason="Image resolution is too low and totally unmatched by any rule", cells={}),
    ]


def test_apply_manual_rebuilds_only_explain_only_skus(monkeypatch):
    client = _client()
    monkeypatch.setattr(fixmod, "detect_format", lambda p: ("listings_report", ""))
    monkeypatch.setattr(fixmod, "read_error_file", lambda p, rules: _lr_mixed())

    captured = {}

    def fake_regen(skus, settings, fix_dir, csv_path=None):
        captured["skus"] = list(skus)
        with open(csv_path, "rb") as fh:
            captured["bytes"] = fh.read()
        return {"written": 1, "file": None, "fixed": list(skus), "could_not_rebuild": [],
                "dropped": [], "rejected": {}, "changed": {}, "manual_needed": []}

    monkeypatch.setattr(fixmod, "regenerate_surface_b", fake_regen)

    up = client.post("/fix", files={"file": ("rej.csv", b"x", "text/csv")})
    fix_id = up.headers["x-fix-id"]
    r = client.post(f"/fix/apply/{fix_id}",
                    data={"action": "manual"},
                    files={"products_export": ("products_export.csv", b"Handle\nabc\n", "text/csv")})
    assert r.status_code == 200
    assert captured["skus"] == ["IMGX"]            # only the explain-only SKU, NOT LR1
    assert captured["bytes"] == b"Handle\nabc\n"


def test_apply_manual_without_export_prompts_and_does_not_rebuild(monkeypatch):
    client = _client()
    monkeypatch.setattr(fixmod, "detect_format", lambda p: ("listings_report", ""))
    monkeypatch.setattr(fixmod, "read_error_file", lambda p, rules: _lr_mixed())

    called = {"regen": False}

    def fake_regen(skus, settings, fix_dir, csv_path=None):
        called["regen"] = True
        return {}

    monkeypatch.setattr(fixmod, "regenerate_surface_b", fake_regen)

    up = client.post("/fix", files={"file": ("rej.csv", b"x", "text/csv")})
    fix_id = up.headers["x-fix-id"]
    r = client.post(f"/fix/apply/{fix_id}", data={"action": "manual"})
    assert r.status_code == 200
    assert called["regen"] is False
    assert "products export" in r.text.lower()


def test_apply_fix_action_still_scopes_to_correctable_only(monkeypatch):
    """Regression: the default action=fix must rebuild only correctable SKUs, never
    the explain-only ones."""
    client = _client()
    monkeypatch.setattr(fixmod, "detect_format", lambda p: ("listings_report", ""))
    monkeypatch.setattr(fixmod, "read_error_file", lambda p, rules: _lr_mixed())

    captured = {}

    def fake_regen(skus, settings, fix_dir, csv_path=None):
        captured["skus"] = list(skus)
        return {"written": 1, "file": None, "fixed": list(skus), "could_not_rebuild": [],
                "dropped": [], "rejected": {}, "changed": {}, "manual_needed": []}

    monkeypatch.setattr(fixmod, "regenerate_surface_b", fake_regen)

    up = client.post("/fix", files={"file": ("rej.csv", b"x", "text/csv")})
    fix_id = up.headers["x-fix-id"]
    r = client.post(f"/fix/apply/{fix_id}",
                    data={"action": "fix"},
                    files={"products_export": ("products_export.csv", b"Handle\nabc\n", "text/csv")})
    assert r.status_code == 200
    assert captured["skus"] == ["LR1"]             # only correctable, IMGX excluded


def test_upload_groups_correctable_and_explain_only(monkeypatch):
    client = _client()
    monkeypatch.setattr(fixmod, "detect_format", lambda p: ("sku_xlsx", ""))
    monkeypatch.setattr(fixmod, "read_error_file", lambda p, rules: _items())
    r = client.post("/fix", files={"file": ("rej.xlsx", b"x",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 200
    assert "Download now to fix" in r.text
    assert "Do not make any changes" in r.text
    assert "78SAZ" in r.text and "IMG1" in r.text


def test_upload_explain_only_shows_manual_download_button_and_guidance(monkeypatch):
    """The explain-only group must offer a 'Download listing file' button (action=manual),
    guidance copy, and the shared products_export input — even for a sku_xlsx rejection."""
    client = _client()
    monkeypatch.setattr(fixmod, "detect_format", lambda p: ("sku_xlsx", ""))
    monkeypatch.setattr(fixmod, "read_error_file", lambda p, rules: _items())
    r = client.post("/fix", files={"file": ("rej.xlsx", b"x",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 200
    assert 'value="manual"' in r.text
    assert "Download listing file" in r.text
    assert "re-export just these SKUs" in r.text          # guidance copy
    assert 'name="products_export"' in r.text              # shared export input now shown
    assert 'value="fix"' in r.text                         # correctable button carries action=fix
    assert "Download now to fix" in r.text


def test_unknown_format_shows_guidance(monkeypatch):
    client = _client()
    monkeypatch.setattr(fixmod, "detect_format",
                        lambda p: (None, "This doesn't look like a Myntra rejection"))
    r = client.post("/fix", files={"file": ("weird.csv", b"a,b\n1,2\n", "text/csv")})
    assert r.status_code == 200
    assert "doesn't look like a Myntra rejection" in r.text


def test_apply_surface_a_calls_correct_from_issues(monkeypatch):
    client = _client()
    monkeypatch.setattr(fixmod, "detect_format", lambda p: ("sku_xlsx", ""))
    monkeypatch.setattr(fixmod, "read_error_file", lambda p, rules: _items())
    monkeypatch.setattr(fixmod, "read_template", lambda p: object())
    monkeypatch.setattr(fixmod, "_load_constants", lambda: {})

    captured = {}

    def fake_cfi(issues, template, template_path, constants, answers, out_path,
                 log_store=None, fix_id=None, drops=None):
        captured["answers"] = answers
        captured["drops"] = drops
        with open(out_path, "wb") as fh:
            fh.write(b"corrected")
        return {"written": 1, "manual_needed": [{"sku": "IMG1", "explanation": "flat shot"}],
                "dropped": [], "changed": {"78SAZ": ["Prominent Colour"]},
                "could_not_rebuild": [], "rejected": {}}

    monkeypatch.setattr(fixmod, "correct_from_issues", fake_cfi)

    up = client.post("/fix", files={"file": ("rej.xlsx", b"x",
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    fix_id = up.headers["x-fix-id"]
    r = client.post(f"/fix/apply/{fix_id}",
                    data={"answer__78SAZ__Prominent Colour": "Off White"})
    assert r.status_code == 200
    assert captured["answers"] == {"78SAZ": {"Prominent Colour": "Off White"}}
    assert "IMG1" in r.text  # manual_needed surfaced on the result screen
    assert "78SAZ" not in captured["drops"]  # checkbox not submitted -> not dropped


def test_apply_surface_a_drop_checkbox_is_authoritative(monkeypatch):
    """A submitted drop__<sku> field must reach correct_from_issues via `drops`."""
    client = _client()
    monkeypatch.setattr(fixmod, "detect_format", lambda p: ("sku_xlsx", ""))
    monkeypatch.setattr(fixmod, "read_error_file", lambda p, rules: _items())
    monkeypatch.setattr(fixmod, "read_template", lambda p: object())
    monkeypatch.setattr(fixmod, "_load_constants", lambda: {})

    captured = {}

    def fake_cfi(issues, template, template_path, constants, answers, out_path,
                 log_store=None, fix_id=None, drops=None):
        captured["drops"] = drops
        with open(out_path, "wb") as fh:
            fh.write(b"corrected")
        return {"written": 1, "manual_needed": [], "dropped": list(drops or []),
                "changed": {}, "could_not_rebuild": [], "rejected": {}}

    monkeypatch.setattr(fixmod, "correct_from_issues", fake_cfi)

    up = client.post("/fix", files={"file": ("rej.xlsx", b"x",
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    fix_id = up.headers["x-fix-id"]
    r = client.post(f"/fix/apply/{fix_id}", data={"drop__78SAZ": "on"})
    assert r.status_code == 200
    assert "78SAZ" in captured["drops"]


def test_apply_listings_report_all_dropped_does_not_rebuild_whole_catalog(monkeypatch):
    """If every correctable SKU is dropped/explain_only, the empty rebuild set must
    short-circuit to a 'nothing to rebuild' result instead of passing None (which
    regenerate_surface_b treats as 'rebuild the whole catalog')."""
    client = _client()
    monkeypatch.setattr(fixmod, "detect_format", lambda p: ("listings_report", ""))
    monkeypatch.setattr(fixmod, "read_error_file", lambda p, rules: [
        ErrorItem(sku="ONLYSKU", style_id=None, source_type="listings_report", scope="sku",
                  raw_reason="Something totally unrelated to any configured rule",
                  cells={}),
    ])

    called = {"regen": False}

    def fake_regen(skus, settings, fix_dir):
        called["regen"] = True
        return {"written": 99, "file": None, "fixed": [], "could_not_rebuild": [],
                "dropped": [], "rejected": {}, "changed": {}, "manual_needed": []}

    monkeypatch.setattr(fixmod, "regenerate_surface_b", fake_regen)

    up = client.post("/fix", files={"file": ("rej.csv", b"x", "text/csv")})
    fix_id = up.headers["x-fix-id"]
    r = client.post(f"/fix/apply/{fix_id}", data={})
    assert r.status_code == 200
    assert called["regen"] is False  # must NOT trigger a whole-catalog rebuild
    assert "Download corrected xlsx" not in r.text
    assert "0 row(s) written" in r.text


def test_upload_listings_report_correctable_shows_export_input(monkeypatch):
    """Surface B with correctable SKUs must offer a file input for the Shopify
    products export (prod has no baked-in export), with multipart encoding."""
    client = _client()
    monkeypatch.setattr(fixmod, "detect_format", lambda p: ("listings_report", ""))
    monkeypatch.setattr(fixmod, "read_error_file", lambda p, rules: _lr_correctable())
    r = client.post("/fix", files={"file": ("rej.csv", b"x", "text/csv")})
    assert r.status_code == 200
    assert 'name="products_export"' in r.text
    assert "multipart/form-data" in r.text


def test_apply_surface_b_without_export_prompts_and_does_not_rebuild(monkeypatch):
    """Submitting the Surface-B fix with no export must NOT call the pipeline; it
    returns a 200 panel asking for the products export."""
    client = _client()
    monkeypatch.setattr(fixmod, "detect_format", lambda p: ("listings_report", ""))
    monkeypatch.setattr(fixmod, "read_error_file", lambda p, rules: _lr_correctable())

    called = {"regen": False}

    def fake_regen(skus, settings, fix_dir, csv_path=None):
        called["regen"] = True
        return {"written": 0, "file": None, "fixed": [], "could_not_rebuild": [],
                "dropped": [], "rejected": {}, "changed": {}, "manual_needed": []}

    monkeypatch.setattr(fixmod, "regenerate_surface_b", fake_regen)

    up = client.post("/fix", files={"file": ("rej.csv", b"x", "text/csv")})
    fix_id = up.headers["x-fix-id"]
    r = client.post(f"/fix/apply/{fix_id}", data={})
    assert r.status_code == 200
    assert called["regen"] is False
    assert "products export" in r.text.lower()


def test_apply_surface_b_with_export_passes_csv_path(monkeypatch):
    """When the user uploads the export, it is saved and threaded through to
    regenerate_surface_b as a real csv_path holding the uploaded bytes."""
    client = _client()
    monkeypatch.setattr(fixmod, "detect_format", lambda p: ("listings_report", ""))
    monkeypatch.setattr(fixmod, "read_error_file", lambda p, rules: _lr_correctable())

    captured = {}

    def fake_regen(skus, settings, fix_dir, csv_path=None):
        captured["csv_path"] = csv_path
        with open(csv_path, "rb") as fh:
            captured["bytes"] = fh.read()
        return {"written": 1, "file": None, "fixed": ["LR1"], "could_not_rebuild": [],
                "dropped": [], "rejected": {}, "changed": {}, "manual_needed": []}

    monkeypatch.setattr(fixmod, "regenerate_surface_b", fake_regen)

    up = client.post("/fix", files={"file": ("rej.csv", b"x", "text/csv")})
    fix_id = up.headers["x-fix-id"]
    r = client.post(f"/fix/apply/{fix_id}", files={
        "products_export": ("products_export.csv", b"Handle,Title\nabc,Kurta\n", "text/csv")})
    assert r.status_code == 200
    assert captured["csv_path"] and os.path.exists(captured["csv_path"])
    assert captured["bytes"] == b"Handle,Title\nabc,Kurta\n"


def test_apply_error_renders_panel_not_500(monkeypatch):
    """Any failure inside apply must render a 200 error panel (htmx only swaps on
    2xx), never a bare 500 that leaves the button looking dead."""
    client = _client(raise_server=False)
    monkeypatch.setattr(fixmod, "detect_format", lambda p: ("listings_report", ""))
    monkeypatch.setattr(fixmod, "read_error_file", lambda p, rules: _lr_correctable())

    def boom(skus, settings, fix_dir, csv_path=None):
        raise RuntimeError("pipeline blew up")

    monkeypatch.setattr(fixmod, "regenerate_surface_b", boom)

    up = client.post("/fix", files={"file": ("rej.csv", b"x", "text/csv")})
    fix_id = up.headers["x-fix-id"]
    r = client.post(f"/fix/apply/{fix_id}", files={
        "products_export": ("products_export.csv", b"Handle\nabc\n", "text/csv")})
    assert r.status_code == 200
    assert "could not" in r.text.lower()


def test_apply_error_panel_escapes_exception_text(monkeypatch):
    """Exception text (which can carry user-influenced content) must be HTML-escaped
    so it cannot inject markup into the error panel."""
    client = _client(raise_server=False)
    monkeypatch.setattr(fixmod, "detect_format", lambda p: ("listings_report", ""))
    monkeypatch.setattr(fixmod, "read_error_file", lambda p, rules: _lr_correctable())

    def boom(skus, settings, fix_dir, csv_path=None):
        raise RuntimeError("<script>alert(1)</script>")

    monkeypatch.setattr(fixmod, "regenerate_surface_b", boom)

    up = client.post("/fix", files={"file": ("rej.csv", b"x", "text/csv")})
    fix_id = up.headers["x-fix-id"]
    r = client.post(f"/fix/apply/{fix_id}", files={
        "products_export": ("products_export.csv", b"Handle\nabc\n", "text/csv")})
    assert r.status_code == 200
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text


def test_apply_bogus_fix_id_returns_404():
    client = _client()
    r = client.post("/fix/apply/../etc", data={})
    assert r.status_code == 404


def test_dismiss_writes_nothing():
    client = _client()
    r = client.get("/fix/dismiss")
    assert r.status_code == 200
    assert "No changes" in r.text


def test_image_rejections_offer_the_replacement_screen(tmp_path, monkeypatch):
    """error_rules.yaml diagnoses 'pixelated' correctly and then dead-ends. The fix
    now sits next to the diagnosis."""
    summary = {"written": 1, "file": None, "fixed": [], "could_not_rebuild": [],
               "dropped": [], "rejected": {}, "changed": {},
               "manual_needed": [{"sku": "S1", "category": "image",
                                  "explanation": "The image resolution is too low."}]}
    from src.web.main import templates
    html = templates.get_template("_fix_result.html").render(
        summary=summary, fix_id="a" * 32, request=None)
    assert "/preview/adopt-fix/" in html


def _corrected_workbook(path, skus=("S1",)):
    """A REAL corrected sheet. The original test wrote b'corrected-bytes' here,
    which is why nothing ever noticed that the adopted file's *contents* were
    never checked."""
    from src.myntra.template_reader import read_template
    from src.myntra.fill import fill_template
    from src.core.models import MappedRow, ImageResult
    t = read_template(V13)
    rows = [(MappedRow(sku=s, cells={"vendorSkuCode": s}), ImageResult(sku=s))
            for s in skus]
    fill_template(V13, t, rows, path)


def _adopted_skus(job_id):
    from src.web.jobs import store
    from src.myntra.preview import read_filled_rows
    from src.myntra.template_reader import read_template
    rows = read_filled_rows(store.get(job_id).result["filled"], read_template(V13))
    return [r["vendorSkuCode"] for r in rows]


def _image_fix_session(client, monkeypatch, items=None, source_type="sku_xlsx"):
    """Push a rejection file through /fix so its issues.json exists on disk, and
    return (fix_id, fix_dir). issues.json — NOT the corrected workbook — is what
    the replacement screen has to read: the corrected sheet excludes every
    explain_only SKU, i.e. exactly the ones the button names."""
    monkeypatch.setattr(fixmod, "detect_format", lambda p: (source_type, ""))
    monkeypatch.setattr(fixmod, "read_error_file", lambda p, rules: items or _items())
    up = client.post("/fix", files={"file": ("rej.csv", b"x", "text/csv")})
    fix_id = up.headers["x-fix-id"]
    return fix_id, fixmod._fix_dir(fix_id)


def _fake_regen(captured, out_path, missing=()):
    def regen(skus, settings, out_dir, csv_path=None):
        captured["skus"] = list(skus)
        captured["csv"] = csv_path
        built = [s for s in skus if s not in missing]
        _corrected_workbook(out_path, skus=tuple(built))
        return {"written": len(built), "file": out_path, "fixed": built,
                "could_not_rebuild": sorted(missing), "dropped": [],
                "rejected": {}, "changed": {}, "manual_needed": []}
    return regen


def test_adopt_fix_rebuilds_exactly_the_image_rejected_skus(tmp_path, monkeypatch):
    """The corrected workbook excludes every explain_only SKU by construction
    (corrector.py skips them), so adopting it opened a sheet without the products
    the button had just named. The replacement sheet must be rebuilt from the
    image rejections themselves — and carry nothing else."""
    client = _client()
    items = _items() + [
        ErrorItem(sku="HSN9", style_id=None, source_type="sku_xlsx", scope="sku",
                  raw_reason="HSN code does not match", cells={})]
    fix_id, fix_dir = _image_fix_session(client, monkeypatch, items)
    export = os.path.join(fix_dir, "products_export.csv")
    with open(export, "wb") as fh:
        fh.write(b"Handle\nabc\n")

    captured = {}
    monkeypatch.setattr(previewmod, "regenerate_surface_b",
                        _fake_regen(captured, str(tmp_path / "rebuilt.xlsx")))

    r = client.post(f"/preview/adopt-fix/{fix_id}")
    assert r.status_code == 200
    # 78SAZ is correctable and HSN9 is explain_only-but-not-an-image: neither belongs
    assert captured["skus"] == ["IMG1"]
    assert captured["csv"] == export
    redirect = r.headers["hx-redirect"]
    assert redirect.startswith("/generate/attributes/")
    job_id = redirect.rsplit("/", 1)[-1]

    from src.web.jobs import store
    job = store.get(job_id)
    assert job.status == "done"
    assert job.result["origin"] == "upload"
    assert _adopted_skus(job_id) == ["IMG1"]


def test_adopt_fix_without_the_shopify_export_asks_for_it(monkeypatch):
    """Rebuilding re-runs the pipeline, which needs the products export. On the
    per-SKU xlsx path nobody has necessarily uploaded one — say so instead of
    rebuilding from nothing."""
    client = _client()
    fix_id, _ = _image_fix_session(client, monkeypatch)

    called = {"regen": False}

    def boom(*a, **k):
        called["regen"] = True
        raise AssertionError("must not rebuild without an export")

    monkeypatch.setattr(previewmod, "regenerate_surface_b", boom)

    r = client.post(f"/preview/adopt-fix/{fix_id}")
    assert r.status_code == 200
    assert called["regen"] is False
    assert "hx-redirect" not in r.headers
    assert "products export" in r.text.lower()


def test_adopt_fix_names_the_skus_the_export_could_not_rebuild(tmp_path, monkeypatch):
    """A SKU missing from the uploaded export silently vanishes from the rebuilt
    sheet — the same 'the file excludes what the button promised' failure. Say
    which ones, and only then offer the ones that did rebuild."""
    client = _client()
    items = _items() + [
        ErrorItem(sku="IMG2", style_id=None, source_type="sku_xlsx", scope="sku",
                  raw_reason="The image is pixelated", cells={})]
    fix_id, fix_dir = _image_fix_session(client, monkeypatch, items)
    with open(os.path.join(fix_dir, "products_export.csv"), "wb") as fh:
        fh.write(b"Handle\nabc\n")

    captured = {}
    monkeypatch.setattr(previewmod, "regenerate_surface_b",
                        _fake_regen(captured, str(tmp_path / "rebuilt.xlsx"),
                                    missing=("IMG2",)))

    r = client.post(f"/preview/adopt-fix/{fix_id}")
    assert r.status_code == 200
    assert "hx-redirect" not in r.headers
    assert "IMG2" in r.text
    # the one that did rebuild is still reachable, by an explicit link
    assert "/generate/attributes/" in r.text


def test_adopt_fix_refuses_a_rebuilt_sheet_with_no_rows(tmp_path, monkeypatch):
    """A rebuild can legitimately come out empty (no rejected SKU was in the
    export). Adopting it drops the owner on a blank accordion with no
    explanation — the exact case /preview already refuses."""
    client = _client()
    fix_id, fix_dir = _image_fix_session(client, monkeypatch)
    with open(os.path.join(fix_dir, "products_export.csv"), "wb") as fh:
        fh.write(b"Handle\nabc\n")

    captured = {}
    monkeypatch.setattr(previewmod, "regenerate_surface_b",
                        _fake_regen(captured, str(tmp_path / "rebuilt.xlsx"),
                                    missing=("IMG1",)))

    r = client.post(f"/preview/adopt-fix/{fix_id}")
    assert r.status_code == 200
    assert "hx-redirect" not in r.headers
    assert "IMG1" in r.text
    assert "/generate/attributes/" not in r.text


def test_adopt_fix_404_when_the_fix_session_is_unknown():
    """A fix id with nothing behind it (expired, or never uploaded) must 404
    rather than rebuild an empty set."""
    client = _client()
    r = client.post("/preview/adopt-fix/" + "c" * 32)
    assert r.status_code == 404


def test_adopt_fix_survives_a_failing_rebuild(tmp_path, monkeypatch):
    """The pipeline can raise on a malformed export. htmx does not swap on 5xx,
    so an unhandled raise leaves the button looking dead — the prod bug this
    codebase has already shipped a fix for once."""
    client = _client(raise_server=False)
    fix_id, fix_dir = _image_fix_session(client, monkeypatch)
    with open(os.path.join(fix_dir, "products_export.csv"), "wb") as fh:
        fh.write(b"not really a products export\n")

    def boom(*a, **k):
        raise RuntimeError("<script>alert(1)</script>")

    monkeypatch.setattr(previewmod, "regenerate_surface_b", boom)

    r = client.post(f"/preview/adopt-fix/{fix_id}")
    assert r.status_code == 200
    assert "hx-redirect" not in r.headers
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text


def test_apply_saves_the_export_when_nothing_is_correctable(monkeypatch):
    """The 'nothing correctable' branch returns before _save_export, so the export
    the owner just attached was thrown away — and the Replace-images button then
    asked for it again, forever."""
    client = _client()
    monkeypatch.setattr(fixmod, "detect_format", lambda p: ("listings_report", ""))
    monkeypatch.setattr(fixmod, "read_error_file", lambda p, rules: [
        ErrorItem(sku="IMGX", style_id=None, source_type="listings_report", scope="sku",
                  raw_reason="The image is pixelated", cells={})])

    up = client.post("/fix", files={"file": ("rej.csv", b"x", "text/csv")})
    fix_id = up.headers["x-fix-id"]
    r = client.post(f"/fix/apply/{fix_id}", files={
        "products_export": ("products_export.csv", b"Handle\nabc\n", "text/csv")})
    assert r.status_code == 200
    saved = os.path.join(fixmod._fix_dir(fix_id), "products_export.csv")
    assert os.path.exists(saved)
    with open(saved, "rb") as fh:
        assert fh.read() == b"Handle\nabc\n"


def test_adopt_fix_rejects_malformed_fix_id():
    client = _client()
    r = client.post("/preview/adopt-fix/../etc")
    assert r.status_code == 404
