import json

from src.core.models import Product
from src.myntra.hsn_kb import signature, read_kb, suggest, learn, SEED


class FakeStore:
    """In-memory stand-in for S3JsonStore (mirrors tests/test_groupid_ledger.py)."""
    def __init__(self):
        self.data = {}

    def get_json(self, key):
        return self.data.get(key)

    def put_json(self, key, data):
        self.data[key] = json.loads(json.dumps(data))  # deep copy


def _p(title="", fabric=None):
    return Product(handle="h", sku="S", title=title, vendor="", tags="", body_html="",
                   price=1.0, compare_at_price=None, color=None, fabric=fabric,
                   size=None, status="active", images=[])


def test_signature_from_fabric_metafield():
    assert signature(_p(title="Blue Saree", fabric="Pure Silk"), "Sarees") == "saree|pure silk"


def test_signature_falls_back_to_title_keyword():
    sig = signature(_p(title="Lavender Pure Cotton Saree", fabric=None), "Sarees",
                    fabric_keywords=["cotton", "silk"])
    assert sig == "saree|cotton"


def test_signature_unknown_when_no_fabric_and_no_keyword():
    assert signature(_p(title="Plain Saree", fabric=None), "Sarees",
                     fabric_keywords=["cotton", "silk"]) == "saree|unknown"


def test_signature_normalizes_whitespace_and_case():
    assert signature(_p(title="x", fabric="  Pure   SILK "), "  Sarees ") == "saree|pure silk"


def test_read_kb_seeds_when_empty():
    kb = read_kb(FakeStore())
    assert kb["classifications"]["saree|pure cotton"][0]["hsn"] == "52081120"
    assert kb["classifications"]["saree|pure silk"][0]["hsn"] == "50072010"


def test_suggest_returns_seed_entry_then_empty_for_unknown():
    kb = read_kb(FakeStore())
    assert suggest(kb, "saree|pure silk")[0]["hsn"] == "50072010"
    assert suggest(kb, "saree|unknown") == []


def test_learn_upserts_count_examples_and_new_code():
    s = FakeStore()
    learn(s, "saree|unknown", "63079090", example_name="Plain Saree")
    kb = read_kb(s)
    e = kb["classifications"]["saree|unknown"][0]
    assert e["hsn"] == "63079090"
    assert e["count"] == 1
    assert e["examples"] == ["Plain Saree"]
    # learning the SAME code again bumps count, dedups example
    learn(s, "saree|unknown", "63079090", example_name="Plain Saree")
    assert read_kb(s)["classifications"]["saree|unknown"][0]["count"] == 2
    # a DIFFERENT code for the same signature is added as a second suggestion
    learn(s, "saree|unknown", "52081120", example_name="Other")
    assert {e["hsn"] for e in suggest(read_kb(s), "saree|unknown")} == {"63079090", "52081120"}


def test_suggest_orders_most_used_first():
    s = FakeStore()
    learn(s, "saree|unknown", "111", example_name="a")
    learn(s, "saree|unknown", "222", example_name="b")
    learn(s, "saree|unknown", "222", example_name="c")   # 222 now count 2
    assert suggest(read_kb(s), "saree|unknown")[0]["hsn"] == "222"
