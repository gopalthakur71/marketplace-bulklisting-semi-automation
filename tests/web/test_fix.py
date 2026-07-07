from fastapi.testclient import TestClient

from src.web.main import create_app
from src.web.settings import Settings
import src.web.routers.fix as fixmod
from src.myntra.error_sources import ErrorItem


def _client():
    return TestClient(create_app(Settings(auth_disabled=True, s3_bucket="b")))


def _items():
    return [
        ErrorItem(sku="78SAZ", style_id=None, source_type="sku_xlsx", scope="sku",
                  raw_reason="Brand Colour (Remarks) cannot be null",
                  cells={"vendorSkuCode": "78SAZ", "Prominent Colour": "Ivory"}),
        ErrorItem(sku="IMG1", style_id=None, source_type="sku_xlsx", scope="sku",
                  raw_reason="Primary image appears to be a flat shot",
                  cells={"vendorSkuCode": "IMG1"}),
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


def test_apply_bogus_fix_id_returns_404():
    client = _client()
    r = client.post("/fix/apply/../etc", data={})
    assert r.status_code == 404


def test_dismiss_writes_nothing():
    client = _client()
    r = client.get("/fix/dismiss")
    assert r.status_code == 200
    assert "No changes" in r.text
