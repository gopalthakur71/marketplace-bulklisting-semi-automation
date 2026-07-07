import json
from src.myntra.explanation_store import read_store, get, learn
from src.web.settings import LocalJsonStore


def _store(tmp_path):
    return LocalJsonStore(str(tmp_path / "expl.json"))


def test_learn_then_get(tmp_path):
    st = _store(tmp_path)
    assert get(st, "sig one") is None
    learn(st, "sig one", "This means the SKU is already live.", category="duplicate")
    entry = get(st, "sig one")
    assert entry["explanation"] == "This means the SKU is already live."
    assert entry["category"] == "duplicate"
    assert entry["count"] == 1
    assert entry["first_seen"]


def test_learn_twice_bumps_count(tmp_path):
    st = _store(tmp_path)
    learn(st, "s", "e")
    learn(st, "s", "e (ignored second time)")
    entry = get(st, "s")
    assert entry["count"] == 2
    assert entry["explanation"] == "e"  # first good explanation is frozen


def test_corrupt_file_treated_as_empty(tmp_path):
    p = tmp_path / "expl.json"
    p.write_text("{ this is not json", encoding="utf-8")
    st = LocalJsonStore(str(p))
    assert read_store(st) == {}
