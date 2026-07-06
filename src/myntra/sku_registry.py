import datetime
import hashlib
import json

REGISTRY_KEY = "state/sku_registry.json"

# Excluded from the fingerprint: styleGroupId is run-assigned, HSN is a per-SKU
# user choice pinned separately. Excluding both lets the dup check run at upload
# time (before images/HSN) and keeps a rebuild with pinned values hashing equal.
_EXCLUDE = ("styleGroupId", "HSN")


def content_hash(cells):
    payload = {k: v for k, v in cells.items() if k not in _EXCLUDE}
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def read_registry(store, key=REGISTRY_KEY):
    return store.get_json(key) or {}


def partition(sku_hashes, registry):
    new, repeat, edited = [], [], []
    for sku, h in sku_hashes:
        entry = registry.get(sku)
        if entry is None:
            new.append(sku)
        elif entry.get("content_hash") == h:
            repeat.append(sku)
        else:
            edited.append(sku)
    return {"new": new, "repeat": repeat, "edited": edited}


def _today():
    return datetime.date.today().isoformat()


def record(store, sku, content_hash, style_group_id, hsn, key=REGISTRY_KEY):
    reg = read_registry(store, key)
    entry = reg.get(sku, {})
    entry.setdefault("first_generated", _today())
    entry["content_hash"] = content_hash
    entry["style_group_id"] = style_group_id
    entry["hsn"] = hsn
    entry["last_generated"] = _today()
    reg[sku] = entry
    store.put_json(key, reg)
