import os

from fastapi.testclient import TestClient

from src.web.main import create_app
from src.web.settings import Settings
import src.web.routers.fix as fixmod
from src.myntra.error_sources import ErrorItem


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


def test_upload_groups_correctable_and_explain_only(monkeypatch):
    client = _client()
    monkeypatch.setattr(fixmod, "detect_format", lambda p: ("sku_xlsx", ""))
    monkeypatch.setattr(fixmod, "read_error_file", lambda p, rules: _items())
    r = client.post("/fix", files={"file": ("rej.xlsx", b"x",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 200
    assert "Proceed" in r.text
    assert "Do not make any changes" in r.text
    assert "78SAZ" in r.text and "IMG1" in r.text


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
