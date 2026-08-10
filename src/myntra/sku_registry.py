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


def update_hsn(store, sku, hsn, key=REGISTRY_KEY):
    """Correct the stored HSN of an already-built SKU. Returns whether it existed.

    The attribute screen can change an HSN after the build that recorded it. The
    fix flow's rebuild pins HSN *from here*, so without this the rebuild would
    quietly restore the stale build-time code.

    Deliberately does not create entries: only a completed build earns a registry
    row, and inventing one here would give the duplicate guard a SKU with no
    content hash to compare."""
    registry = read_registry(store, key)
    entry = registry.get(sku)
    if entry is None:
        return False
    entry["hsn"] = hsn
    store.put_json(key, registry)
    return True


def update_names(store, sku, names, key=REGISTRY_KEY):
    """Pin the seller's hand-written names for an already-built SKU.

    Same job as update_hsn, for the same reason: the attribute screen can rewrite
    productDisplayName / List View Name after the build, and a later rebuild maps
    those columns from the Shopify export — so without a pin the rebuild silently
    restores the machine-generated title over the name the seller chose.

    Merges per key rather than replacing the dict: a per-panel save posts only the
    fields that panel carries, so a wholesale replace would drop names pinned by an
    earlier save. A None value REMOVES the pin instead of storing None — a cleared
    box means "go back to what the pipeline writes", not "blank this column on
    every future rebuild".

    Stored beside the fingerprint, never inside it, so pinning cannot make an
    otherwise unchanged SKU look edited to the duplicate guard. Like update_hsn,
    it does not invent registry rows."""
    registry = read_registry(store, key)
    entry = registry.get(sku)
    if entry is None:
        return False
    pinned = dict(entry.get("names") or {})
    for header, value in names.items():
        if value is None or str(value).strip() == "":
            pinned.pop(header, None)
        else:
            pinned[header] = str(value)
    entry["names"] = pinned
    store.put_json(key, registry)
    return True
