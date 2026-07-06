import json

from src.myntra.sku_registry import content_hash, read_registry, partition, record


class FakeStore:
    def __init__(self):
        self.data = {}

    def get_json(self, key):
        return self.data.get(key)

    def put_json(self, key, data):
        self.data[key] = json.loads(json.dumps(data))


def test_content_hash_ignores_stylegroupid_and_hsn():
    a = {"productDisplayName": "Silk Saree", "styleGroupId": "13", "HSN": "50072010"}
    b = {"productDisplayName": "Silk Saree", "styleGroupId": "999", "HSN": "11111111"}
    assert content_hash(a) == content_hash(b)


def test_content_hash_changes_on_real_field():
    a = {"productDisplayName": "Silk Saree", "MRP": "2000"}
    b = {"productDisplayName": "Silk Saree", "MRP": "2500"}
    assert content_hash(a) != content_hash(b)


def test_content_hash_stable_across_key_order():
    assert content_hash({"a": "1", "b": "2"}) == content_hash({"b": "2", "a": "1"})


def test_partition_buckets_new_repeat_edited():
    reg = {"S1": {"content_hash": "h1"}, "S2": {"content_hash": "h2"}}
    parts = partition([("S1", "h1"), ("S2", "hX"), ("S3", "h3")], reg)
    assert parts == {"new": ["S3"], "repeat": ["S1"], "edited": ["S2"]}


def test_record_pins_hash_id_hsn_and_dates():
    s = FakeStore()
    record(s, "S1", "h1", 13, "50072010")
    e = read_registry(s)["S1"]
    assert e["content_hash"] == "h1"
    assert e["style_group_id"] == 13
    assert e["hsn"] == "50072010"
    assert e["first_generated"] == e["last_generated"]
    # re-record keeps first_generated, refreshes the rest
    first = e["first_generated"]
    record(s, "S1", "h2", 14, "52081120")
    e2 = read_registry(s)["S1"]
    assert e2["first_generated"] == first
    assert e2["content_hash"] == "h2" and e2["style_group_id"] == 14 and e2["hsn"] == "52081120"


def test_read_registry_empty_when_absent():
    assert read_registry(FakeStore()) == {}
