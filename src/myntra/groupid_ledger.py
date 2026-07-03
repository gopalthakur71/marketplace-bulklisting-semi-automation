import datetime
import json
import uuid

LEDGER_KEY = "state/myntra_groupid.json"


def _new():
    return {"next_style_group_id": 1, "batches": []}


def read_ledger(store, key=LEDGER_KEY):
    data = store.get_json(key)
    return data if data is not None else _new()


def reserve(store, count, filename, key=LEDGER_KEY):
    """Reserve `count` styleGroupIds as a pending batch. Returns (start, batch_id).
    Does NOT advance next_style_group_id — only confirm() does, so a failed upload
    that is never confirmed frees its IDs for reuse."""
    led = read_ledger(store, key)
    start = led["next_style_group_id"]
    batch_id = uuid.uuid4().hex
    led["batches"].append({
        "id": batch_id,
        "file": filename,
        "range": [start, start + count - 1],
        "status": "pending",
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    })
    store.put_json(key, led)
    return start, batch_id


def confirm(store, batch_id, key=LEDGER_KEY):
    """Mark a pending batch confirmed and advance next_style_group_id past its range."""
    led = read_ledger(store, key)
    for b in led["batches"]:
        if b["id"] == batch_id and b["status"] == "pending":
            b["status"] = "confirmed"
            led["next_style_group_id"] = max(led["next_style_group_id"], b["range"][1] + 1)
            store.put_json(key, led)
            return led["next_style_group_id"]
    raise KeyError(f"no pending batch {batch_id!r}")


def unconfirm(store, batch_id, key=LEDGER_KEY):
    """Revert the MOST-RECENTLY-confirmed batch back to pending and roll
    next_style_group_id back to the start of its range. Guard: only safe when no
    later batch has consumed IDs past this range (i.e. next == range[1] + 1),
    otherwise undoing would reissue IDs a later confirm already used."""
    led = read_ledger(store, key)
    for b in led["batches"]:
        if b["id"] == batch_id and b["status"] == "confirmed":
            if led["next_style_group_id"] != b["range"][1] + 1:
                raise ValueError("can't undo — a later batch was already confirmed")
            b["status"] = "pending"
            led["next_style_group_id"] = b["range"][0]
            store.put_json(key, led)
            return led["next_style_group_id"]
    raise KeyError(f"no confirmed batch {batch_id!r}")


class S3JsonStore:
    """Production store: a JSON object per key in an S3 bucket. boto3 client injected."""
    def __init__(self, bucket, client):
        self.bucket = bucket
        self.client = client

    def get_json(self, key):
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=key)
        except self.client.exceptions.NoSuchKey:
            return None
        return json.loads(obj["Body"].read().decode("utf-8"))

    def put_json(self, key, data):
        self.client.put_object(
            Bucket=self.bucket, Key=key,
            Body=json.dumps(data, indent=2).encode("utf-8"),
            ContentType="application/json",
        )
