LOG_KEY = "state/correction_log.json"


def read_log(store, key=LOG_KEY):
    """The append-only list of correction records. Absent/malformed -> []."""
    try:
        data = store.get_json(key)
    except Exception:
        return []
    return data if isinstance(data, list) else []


def append(store, record, key=LOG_KEY):
    log = read_log(store, key)
    log.append(record)
    store.put_json(key, log)
    return log
