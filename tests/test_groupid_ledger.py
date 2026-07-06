import json
from src.myntra.groupid_ledger import read_ledger, reserve, confirm


class FakeStore:
    """In-memory stand-in for S3JsonStore."""
    def __init__(self):
        self.data = {}

    def get_json(self, key):
        return self.data.get(key)

    def put_json(self, key, data):
        self.data[key] = json.loads(json.dumps(data))  # deep copy, JSON-round-trip


def test_empty_ledger_starts_at_1():
    s = FakeStore()
    led = read_ledger(s)
    assert led["next_style_group_id"] == 1
    assert led["batches"] == []


def test_reserve_does_not_advance_counter():
    s = FakeStore()
    start, batch_id = reserve(s, count=3, filename="a.xlsx")
    assert start == 1
    # counter NOT advanced until confirm
    assert read_ledger(s)["next_style_group_id"] == 1
    # a second reserve before confirm reuses the same start (documented limitation)
    start2, _ = reserve(s, count=2, filename="b.xlsx")
    assert start2 == 1
    pend = [b for b in read_ledger(s)["batches"] if b["status"] == "pending"]
    assert len(pend) == 2


def test_confirm_advances_past_range():
    s = FakeStore()
    start, batch_id = reserve(s, count=3, filename="a.xlsx")   # range 1..3
    new_next = confirm(s, batch_id)
    assert new_next == 4
    assert read_ledger(s)["next_style_group_id"] == 4
    b = read_ledger(s)["batches"][0]
    assert b["status"] == "confirmed"
    # next reserve now starts at 4
    start2, _ = reserve(s, count=1, filename="c.xlsx")
    assert start2 == 4


def test_confirm_unknown_batch_raises():
    s = FakeStore()
    import pytest
    with pytest.raises(KeyError):
        confirm(s, "does-not-exist")


def test_unconfirm_reverts_most_recent_batch():
    from src.myntra.groupid_ledger import unconfirm
    s = FakeStore()
    start, batch_id = reserve(s, count=3, filename="a.xlsx")   # range 1..3
    confirm(s, batch_id)                                        # next -> 4
    new_next = unconfirm(s, batch_id)                           # roll back to 1
    assert new_next == 1
    led = read_ledger(s)
    assert led["next_style_group_id"] == 1
    assert led["batches"][0]["status"] == "pending"


def test_unconfirm_blocked_when_later_batch_confirmed():
    import pytest
    from src.myntra.groupid_ledger import unconfirm
    s = FakeStore()
    _, b1 = reserve(s, count=2, filename="a.xlsx")   # range 1..2
    confirm(s, b1)                                    # next -> 3
    _, b2 = reserve(s, count=2, filename="b.xlsx")    # range 3..4
    confirm(s, b2)                                    # next -> 5
    with pytest.raises(ValueError):
        unconfirm(s, b1)                              # b2 already consumed IDs past b1


def test_set_next_records_value_plus_one_and_undo_restores():
    from src.myntra.groupid_ledger import set_next, undo_set_next
    s = FakeStore()
    reserve(s, count=1, filename="a.xlsx")
    confirm(s, read_ledger(s)["batches"][0]["id"])   # next -> 2
    res = set_next(s, 40)                             # user says "last used = 40"
    assert res["next"] == 41
    assert res["prev"] == 2
    assert res["warn"] is False
    assert read_ledger(s)["next_style_group_id"] == 41
    assert undo_set_next(s) == 2
    assert read_ledger(s)["next_style_group_id"] == 2


def test_set_next_warns_when_lowering():
    from src.myntra.groupid_ledger import set_next
    s = FakeStore()
    reserve(s, count=10, filename="a.xlsx")
    confirm(s, read_ledger(s)["batches"][0]["id"])   # next -> 11
    res = set_next(s, 3)                              # lowering to 4 (< 11)
    assert res["next"] == 4
    assert res["warn"] is True
    assert read_ledger(s)["next_style_group_id"] == 4   # allowed despite warning


def test_undo_set_next_without_prior_raises():
    import pytest
    from src.myntra.groupid_ledger import undo_set_next
    s = FakeStore()
    with pytest.raises(ValueError):
        undo_set_next(s)
