from dataclasses import dataclass
from src.myntra.explainer import explain_item, match_rule, ExplainedIssue
from src.web.settings import LocalJsonStore
from src.myntra.explanation_store import get as store_get


@dataclass
class _Item:
    sku: str | None = "S1"
    style_id: str | None = None
    source_type: str = "sku_xlsx"
    scope: str = "sku"
    raw_reason: str = ""
    cells: dict | None = None


RULES = {
    "rules": [
        {"match": "already registered", "category": "duplicate",
         "action": "drop_sku", "explanation": "Already live on Myntra."},
    ],
    "unknown": {"category": "unknown", "action": "explain_only",
                "explanation": "Unrecognised error."},
}


def test_yaml_hit_wins_and_carries_action():
    it = _Item(raw_reason="Seller Sku Code X is already registered")
    out = explain_item(it, RULES)
    assert out.source == "yaml"
    assert out.action == "drop_sku"
    assert out.explanation == "Already live on Myntra."


def test_listings_report_reason_passes_through():
    it = _Item(source_type="listings_report",
               raw_reason="Product image is a flat shot; please reshoot on a model")
    out = explain_item(it, RULES)
    assert out.source == "plain"
    assert out.action == "explain_only"
    assert out.explanation == it.raw_reason


def test_learned_store_used_before_gemini(tmp_path):
    from src.myntra.explanation_store import learn
    st = LocalJsonStore(str(tmp_path / "e.json"))
    it = _Item(raw_reason="HSN given 111 does not match present 222")
    from src.myntra.signature import normalize
    learn(st, normalize(it.raw_reason)[0], "Learned explanation.")
    called = {"gemini": False}
    gem = {"enabled": True, "api_key": "k", "model": "m",
           "client": lambda p: called.__setitem__("gemini", True) or "SHOULD NOT RUN"}
    out = explain_item(it, RULES, store=st, gemini=gem)
    assert out.source == "learned"
    assert out.explanation == "Learned explanation."
    assert called["gemini"] is False


def test_gemini_explains_then_writes_store(tmp_path):
    st = LocalJsonStore(str(tmp_path / "e.json"))
    it = _Item(raw_reason="Some brand new cryptic wording 999")
    gem = {"enabled": True, "api_key": "k", "model": "m",
           "client": lambda p: "Gemini plain text."}
    out = explain_item(it, RULES, store=st, gemini=gem)
    assert out.source == "gemini"
    assert out.action == "explain_only"
    from src.myntra.signature import normalize
    assert store_get(st, normalize(it.raw_reason)[0])["explanation"] == "Gemini plain text."


def test_raw_fallback_when_gemini_off(tmp_path):
    st = LocalJsonStore(str(tmp_path / "e.json"))
    it = _Item(raw_reason="Totally unseen error text 42")
    out = explain_item(it, RULES, store=st, gemini={"enabled": False})
    assert out.source == "raw"
    assert out.explanation == it.raw_reason
