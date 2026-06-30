import os
from unittest import mock

from fastapi.testclient import TestClient

from src.web.main import create_app
from src.web.settings import Settings
import src.web.routers.fix as fixmod
from src.myntra.error_reader import RowError


def _client():
    return TestClient(create_app(Settings(auth_disabled=True, s3_bucket="b")))


def _fake_rows():
    return [
        RowError(row=4, sku="78SAZ", status="ERROR",
                 cells={"vendorSkuCode": "78SAZ", "Prominent Colour": "Ivory"},
                 issues=[{"category": "vocab", "action": "manual_choice",
                          "field": "Prominent Colour", "explanation": "Pick a Myntra colour",
                          "raw": "colour not in dropdown"}]),
        RowError(row=5, sku="81COT", status="ERROR",
                 cells={"vendorSkuCode": "81COT"},
                 issues=[{"category": "duplicate", "action": "drop_sku",
                          "field": None, "explanation": "Already listed",
                          "raw": "already registered"}]),
    ]


def test_fix_upload_shows_buckets(monkeypatch):
    client = _client()
    monkeypatch.setattr(fixmod, "read_errors", lambda path, rules: _fake_rows())
    monkeypatch.setattr(fixmod, "load_rules", lambda: {"rules": [], "unknown": {}})
    r = client.post("/fix", files={"file": ("rej.xlsx", b"x", "application/vnd.ms-excel")})
    assert r.status_code == 200
    assert "needs you" in r.text.lower()
    assert "Prominent Colour" in r.text
    assert "Drop this SKU" in r.text


def test_fix_apply_calls_correct_with_typed_answer(monkeypatch, tmp_path):
    client = _client()
    rows = _fake_rows()
    monkeypatch.setattr(fixmod, "read_errors", lambda path, rules: rows)
    monkeypatch.setattr(fixmod, "load_rules", lambda: {"rules": [], "unknown": {}})
    monkeypatch.setattr(fixmod, "read_template", lambda p: object())
    monkeypatch.setattr(fixmod, "_load_constants", lambda: {})
    monkeypatch.setattr(fixmod, "_resolve_template_path", lambda: "tpl.xlsx")

    captured = {}

    def fake_correct(row_errors, template, template_path, constants, answers, drops, out_path):
        captured["answers"] = answers
        captured["drops"] = drops
        with open(out_path, "wb") as fh:
            fh.write(b"corrected")
        return {"written": 1, "dropped": list(drops), "changed": {"78SAZ": ["Prominent Colour"]},
                "rejected": {}}

    monkeypatch.setattr(fixmod, "correct", fake_correct)

    # first upload to create the fix_id + cached rows
    up = client.post("/fix", files={"file": ("rej.xlsx", b"x", "application/vnd.ms-excel")})
    fix_id = up.headers["x-fix-id"]

    r = client.post(f"/fix/apply/{fix_id}", data={
        "answer__78SAZ__Prominent Colour": "Off White",
        "drop__81COT": "on",
    })
    assert r.status_code == 200
    assert captured["answers"] == {"78SAZ": {"Prominent Colour": "Off White"}}
    assert captured["drops"] == {"81COT"}
    assert "corrected" in r.text.lower() or "Download" in r.text


def test_fix_apply_bogus_fix_id_returns_404():
    """Path traversal attempt with a non-hex fix_id must return 404."""
    client = _client()
    r = client.post("/fix/apply/../etc", data={})
    assert r.status_code == 404
