from src.myntra.correction_log import read_log, append
from src.web.settings import LocalJsonStore


def test_append_accumulates(tmp_path):
    st = LocalJsonStore(str(tmp_path / "log.json"))
    assert read_log(st) == []
    append(st, {"sku": "A", "changes": {"brand": ["", "Ijor Ethnic Partners"]}})
    append(st, {"sku": "B", "changes": {"ISP": ["", "2690"]}})
    log = read_log(st)
    assert [r["sku"] for r in log] == ["A", "B"]


def test_corrupt_log_treated_as_empty(tmp_path):
    p = tmp_path / "log.json"
    p.write_text("not json", encoding="utf-8")
    st = LocalJsonStore(str(p))
    assert read_log(st) == []
    append(st, {"sku": "C"})
    assert read_log(st)[-1]["sku"] == "C"
