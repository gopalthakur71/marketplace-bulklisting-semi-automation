import datetime

STORE_KEY = "state/error_explanations.json"


def _today():
    return datetime.date.today().isoformat()


def read_store(store, key=STORE_KEY):
    """Return the dict of {signature: entry}. Absent or malformed JSON -> {} so a
    half-written store never breaks the review screen (spec §8)."""
    try:
        data = store.get_json(key)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def get(store, signature, key=STORE_KEY):
    return read_store(store, key).get(signature)


def learn(store, signature, explanation, category=None, key=STORE_KEY):
    """Upsert a learned explanation. The FIRST good explanation per signature is
    frozen; later calls only bump the count (edits happen by hand in the JSON)."""
    data = read_store(store, key)
    entry = data.get(signature)
    if entry:
        entry["count"] = entry.get("count", 0) + 1
    else:
        data[signature] = {
            "explanation": explanation,
            "category": category,
            "count": 1,
            "first_seen": _today(),
        }
    store.put_json(key, data)
    return data
