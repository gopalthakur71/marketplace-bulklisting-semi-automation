import datetime
import json
import re

HSN_KEY = "state/hsn_kb.json"
EXAMPLES_CAP = 5

# The two codes that used to be hardcoded in config/myntra/rules.yaml's fabric
# blocks. They seed a fresh KB so nothing regresses; the KB is now the single
# source of truth for HSN.
SEED = {
    "saree|pure cotton": [{"hsn": "52081120", "examples": [], "count": 0, "last_used": None}],
    "saree|pure silk": [{"hsn": "50072010", "examples": [], "count": 0, "last_used": None}],
}


def _norm(s):
    """lowercase, strip, collapse internal whitespace."""
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def _category_token(category):
    """Normalize the articleType constant to a category token, e.g.
    "Sarees" -> "saree". Naive singular: drop one trailing 's' on longer words."""
    tok = _norm(category)
    if tok.endswith("s") and len(tok) > 3:
        tok = tok[:-1]
    return tok


def signature(product, category, fabric_keywords=()):
    """Normalized "category|fabric" key. fabric = the Shopify fabric metafield;
    if blank, the first fabric_keywords token found in the title; else "unknown".
    Pure + deterministic so the pre-scan and the mapper always agree."""
    fabric = _norm(getattr(product, "fabric", None))
    if not fabric:
        title = _norm(getattr(product, "title", None))
        for kw in fabric_keywords:
            k = _norm(kw)
            if k and k in title:
                fabric = k
                break
    if not fabric:
        fabric = "unknown"
    return f"{_category_token(category)}|{fabric}"


def _today():
    return datetime.date.today().isoformat()


def read_kb(store, key=HSN_KEY):
    """Return the KB dict, seeding an empty/absent file from SEED (not persisted
    until the first learn())."""
    data = store.get_json(key)
    if not data or not data.get("classifications"):
        return {"classifications": json.loads(json.dumps(SEED))}
    return data


def suggest(kb, sig):
    """Stored entries for a signature, most-used first (may be empty)."""
    entries = kb.get("classifications", {}).get(sig, [])
    return sorted(entries, key=lambda e: e.get("count", 0), reverse=True)


def learn(store, sig, hsn, example_name=None, key=HSN_KEY):
    """Upsert (sig, hsn): bump count, refresh last_used, append a capped example.
    A new code for an existing signature is added as an additional suggestion."""
    kb = read_kb(store, key)
    entries = kb["classifications"].setdefault(sig, [])
    for e in entries:
        if e["hsn"] == hsn:
            e["count"] = e.get("count", 0) + 1
            e["last_used"] = _today()
            if example_name and example_name not in e["examples"]:
                e["examples"] = (e["examples"] + [example_name])[-EXAMPLES_CAP:]
            store.put_json(key, kb)
            return kb
    entries.append({
        "hsn": hsn,
        "examples": [example_name] if example_name else [],
        "count": 1,
        "last_used": _today(),
    })
    store.put_json(key, kb)
    return kb
