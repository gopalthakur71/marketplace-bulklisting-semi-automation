import json

from src.myntra.sku_registry import (content_hash, read_registry, partition,
                                     record, update_hsn, update_names)


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


def test_update_hsn_changes_only_that_field():
    store = FakeStore()
    record(store, "S1", "hash-1", 42, "50072010")
    assert update_hsn(store, "S1", "54075240") is True
    entry = read_registry(store)["S1"]
    assert entry["hsn"] == "54075240"
    assert entry["content_hash"] == "hash-1"      # untouched
    assert entry["style_group_id"] == 42          # untouched


def test_update_hsn_is_a_no_op_for_an_unknown_sku():
    store = FakeStore()
    assert update_hsn(store, "NEVER-BUILT", "54075240") is False
    assert read_registry(store) == {}             # no row invented


def test_update_hsn_accepts_none_to_clear():
    store = FakeStore()
    record(store, "S1", "hash-1", 42, "50072010")
    assert update_hsn(store, "S1", None) is True
    assert read_registry(store)["S1"]["hsn"] is None


def test_update_names_pins_an_edited_name():
    store = FakeStore()
    record(store, "S1", "hash-1", 42, "50072010")
    assert update_names(store, "S1", {"productDisplayName": "Ijor Cotton Saree"}) is True
    entry = read_registry(store)["S1"]
    assert entry["names"] == {"productDisplayName": "Ijor Cotton Saree"}
    assert entry["content_hash"] == "hash-1"      # untouched
    assert entry["style_group_id"] == 42          # untouched
    assert entry["hsn"] == "50072010"             # untouched


def test_update_names_merges_rather_than_replacing():
    """Saving one panel posts only the fields that panel carries; a save that
    omits a name must not wipe a name pinned by an earlier save."""
    store = FakeStore()
    record(store, "S1", "hash-1", 42, "50072010")
    update_names(store, "S1", {"productDisplayName": "Full Name"})
    update_names(store, "S1", {"List View Name": "Short Name"})
    assert read_registry(store)["S1"]["names"] == {
        "productDisplayName": "Full Name", "List View Name": "Short Name"}


def test_update_names_drops_the_pin_when_a_name_is_cleared():
    """A cleared box means 'go back to what the pipeline writes', not 'pin blank
    forever' — otherwise clearing it would permanently blank the column on every
    later rebuild."""
    store = FakeStore()
    record(store, "S1", "hash-1", 42, "50072010")
    update_names(store, "S1", {"productDisplayName": "Full Name"})
    update_names(store, "S1", {"productDisplayName": None})
    assert read_registry(store)["S1"]["names"] == {}


def test_update_names_is_a_no_op_for_an_unknown_sku():
    store = FakeStore()
    assert update_names(store, "NEVER-BUILT", {"productDisplayName": "X"}) is False
    assert read_registry(store) == {}             # no row invented


def test_a_pinned_name_does_not_disturb_the_content_hash():
    """The pin lives beside the fingerprint, never inside it: pinning a name must
    not make an unchanged SKU look edited to the duplicate guard."""
    store = FakeStore()
    record(store, "S1", "hash-1", 42, "50072010")
    update_names(store, "S1", {"productDisplayName": "Renamed"})
    assert read_registry(store)["S1"]["content_hash"] == "hash-1"
