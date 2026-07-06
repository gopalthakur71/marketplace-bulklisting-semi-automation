# Pre-Upload SKU Duplicate-Generation Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the app from silently regenerating already-generated SKUs: keep a per-SKU registry recorded at generate time, and on a re-upload warn "already generated — use Fix errors if rejected" + offer a deterministic rebuild download instead of burning fresh styleGroupIds.

**Architecture:** A new `state/sku_registry.json` store (mirrors `groupid_ledger.py`/`hsn_kb.py`) maps each seller SKU → `{content_hash, style_group_id, hsn, dates}`. On upload the router maps products (no images/HSN), hashes each, and partitions NEW/REPEAT/EDITED against the registry. Any repeat → a warning screen with a rebuild-on-demand download (re-runs the pipeline forcing each SKU's pinned styleGroupId + HSN, so the file is byte-identical and the ledger is untouched). No repeats → today's HSN flow, and every build records the registry.

**Tech Stack:** Python 3, pytest, FastAPI + Starlette `TestClient`, Jinja2 + htmx, openpyxl-derived `TemplateInfo`, hashlib/sha1.

## Global Constraints

- Branch: `feat/hsn-knowledge-base`. CI/CD deploys **only** on `main` — **do not merge to main in this batch**, local verify only.
- Identity key is the **seller SKU** (`product.sku` = Shopify Variant SKU). Registry is app-owned; never read the Myntra report.
- Records are written at **generate time** (not upload-to-Myntra) — that is what catches the pre-upload repeat.
- `content_hash` **excludes `styleGroupId` and `HSN`** so it is computable at upload (before images and the HSN review), and so a rebuild with different pinned ids/HSN still hashes equal.
- HSN is **per-SKU authoritative** (pinned in the registry); the `category|fabric` signature KB stays **suggestion-only** and is not changed by this work.
- Duplicate response is **warn on any repeat** + rebuild-on-demand download. EDITED SKUs are treated like NEW here (fresh id, freshly asked HSN); reusing ids for edits is the separate post-Myntra spec.
- New JSON store MUST use its own local path (`SKU_REGISTRY_LOCAL_PATH`) because `LocalJsonStore` is one-file-per-path.
- Local preview: `AUTH_DISABLED=1 LEDGER_LOCAL_PATH=<t>/led.json HSN_LOCAL_PATH=<t>/hsn.json SKU_REGISTRY_LOCAL_PATH=<t>/reg.json uvicorn src.web.main:app --reload`.
- Run the full suite with `python -m pytest -q` from repo root.

## File Structure

- **Create** `src/myntra/sku_registry.py` — `content_hash`, `read_registry`, `partition`, `record`, `REGISTRY_KEY`. Pure; store-duck-typed.
- **Modify** `src/web/settings.py` — `Settings.sku_registry_local_path`, `SKU_REGISTRY_LOCAL_PATH` parse, `sku_registry_store()`.
- **Modify** `src/myntra/mapper.py` — `map_product` gains `hsn_override=None` (a pinned per-SKU HSN that wins over the signature lookup).
- **Modify** `src/myntra/pipeline.py` — `main` gains `only_skus`, `style_group_id_by_sku`, `hsn_by_sku`; returns `records`; new `scan_content_hashes()` helper.
- **Modify** `src/web/routers/generate.py` — partition on upload; thread `only_skus` through the HSN flow; record on build; new `rebuild` + "generate new only" routes.
- **Create** `src/web/templates/_dedup_warn.html`.
- **Modify** tests: `tests/test_pipeline_override.py`, `tests/web/test_generate.py`, `tests/web/test_settings.py`; **create** `tests/test_sku_registry.py`.

---

### Task 1: SKU registry core (`sku_registry.py`)

**Files:**
- Create: `src/myntra/sku_registry.py`
- Test: `tests/test_sku_registry.py`

**Interfaces:**
- Consumes: a store with `get_json(key)`/`put_json(key, data)` (same duck type as the ledger/KB).
- Produces:
  - `content_hash(cells: dict) -> str` — sha1 of canonical JSON of `cells` minus `styleGroupId` and `HSN`.
  - `read_registry(store, key=REGISTRY_KEY) -> dict` — `{}` when absent.
  - `partition(sku_hashes: list[tuple[str,str]], registry: dict) -> dict` — `{"new": [...], "repeat": [...], "edited": [...]}`.
  - `record(store, sku, content_hash, style_group_id, hsn, key=REGISTRY_KEY) -> None`.
  - `REGISTRY_KEY = "state/sku_registry.json"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sku_registry.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_sku_registry.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.myntra.sku_registry'`.

- [ ] **Step 3: Implement `sku_registry.py`**

Create `src/myntra/sku_registry.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_sku_registry.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/myntra/sku_registry.py tests/test_sku_registry.py
git commit -m "feat(registry): per-SKU generation registry — hash/partition/record"
```

---

### Task 2: Settings — `sku_registry_store`

**Files:**
- Modify: `src/web/settings.py`
- Test: `tests/web/test_settings.py`

**Interfaces:**
- Produces: `Settings.sku_registry_local_path: str | None`; `SKU_REGISTRY_LOCAL_PATH` env; `sku_registry_store(settings)` → `LocalJsonStore` when the path is set, else `S3JsonStore` (mirrors `ledger_store`/`hsn_store`).

- [ ] **Step 1: Write the failing test**

Append to `tests/web/test_settings.py`:

```python
def test_sku_registry_local_path_and_store(tmp_path):
    from src.web.settings import sku_registry_store
    s = load_settings(env={"SKU_REGISTRY_LOCAL_PATH": str(tmp_path / "reg.json")},
                      ssm=lambda n: None)
    assert s.sku_registry_local_path == str(tmp_path / "reg.json")
    store = sku_registry_store(s)
    assert isinstance(store, LocalJsonStore)
    store.put_json("state/sku_registry.json", {"S1": {"content_hash": "h"}})
    assert store.get_json("state/sku_registry.json")["S1"]["content_hash"] == "h"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/web/test_settings.py::test_sku_registry_local_path_and_store -q`
Expected: FAIL — `ImportError: cannot import name 'sku_registry_store'`.

- [ ] **Step 3: Add the field, env parse, and helper**

In `src/web/settings.py`, add the field after `hsn_local_path`:

```python
    hsn_local_path: str | None = None
    sku_registry_local_path: str | None = None
```

In `load_settings`, after the `s.hsn_local_path = ...` line:

```python
    s.hsn_local_path = env.get("HSN_LOCAL_PATH") or None
    s.sku_registry_local_path = env.get("SKU_REGISTRY_LOCAL_PATH") or None
```

At the end of the file, after `hsn_store`:

```python
def sku_registry_store(settings: Settings):
    """Store for the per-SKU generation registry. Mirrors ledger_store/hsn_store;
    own local path (LocalJsonStore is one-file-per-path)."""
    if settings.sku_registry_local_path:
        return LocalJsonStore(settings.sku_registry_local_path)
    import boto3
    from src.myntra.groupid_ledger import S3JsonStore
    return S3JsonStore(settings.s3_bucket, boto3.client("s3", region_name=settings.s3_region))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/web/test_settings.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/web/settings.py tests/web/test_settings.py
git commit -m "feat(web): sku_registry_store + SKU_REGISTRY_LOCAL_PATH setting"
```

---

### Task 3: Pipeline overrides, records, and `scan_content_hashes`

**Files:**
- Modify: `src/myntra/mapper.py` (add `hsn_override`)
- Modify: `src/myntra/pipeline.py` (`only_skus`, `style_group_id_by_sku`, `hsn_by_sku`, `records`, `scan_content_hashes`)
- Test: `tests/test_pipeline_override.py`

**Interfaces:**
- Consumes: `content_hash`, `read_products`, `read_template`, `map_product`.
- Produces:
  - `map_product(product, template, column_map, constants, rules=None, hsn_by_signature=None, hsn_override=None)` — when `hsn_override` is truthy, `HSN` is set to it (wins over the signature map).
  - `main(..., only_skus=None, style_group_id_by_sku=None, hsn_by_sku=None)` — filters products to `only_skus` (when given); forces each SKU's styleGroupId from `style_group_id_by_sku` and HSN from `hsn_by_sku` when present. Return dict gains `records`: `list[{"sku", "style_group_id", "hsn", "content_hash"}]`.
  - `scan_content_hashes(csv_path, template_path=None, config_dir="config/myntra") -> list[tuple[str,str]]` — `(sku, content_hash)` per product, HSN unset, no images.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_pipeline_override.py`:

```python
def test_hsn_override_wins_in_mapper():
    from src.myntra.mapper import map_product
    from src.core.models import Product, TemplateInfo
    headers = ["SKUCode", "HSN"]
    tmpl = TemplateInfo(headers=headers, header_row=3, first_data_row=4,
                        col_index_by_header={h: i + 1 for i, h in enumerate(headers)},
                        vocab_by_header={})
    p = Product(handle="h", sku="S1", title="T", vendor="", tags="", body_html="",
                price=1.0, compare_at_price=None, color=None, fabric="Pure Silk",
                size=None, status="active", images=[])
    row = map_product(p, tmpl, {}, {"articleType": "Sarees"}, {},
                      hsn_by_signature={"saree|pure silk": "50072010"},
                      hsn_override="99999999")
    assert row.cells["HSN"] == "99999999"


def test_scan_content_hashes_pairs_sku_and_hash():
    from src.myntra.pipeline import scan_content_hashes
    pairs = scan_content_hashes("tests/fixtures/products_export.csv",
                                template_path="templates/myntra/Myntra-Sku-Template-2026-06-16.xlsx")
    assert len(pairs) == 2
    skus = [s for s, _ in pairs]
    assert len(set(skus)) == 2                 # distinct SKUs
    assert all(len(h) == 40 for _, h in pairs)  # sha1 hex


def test_pipeline_pins_id_hsn_and_returns_records(tmp_path):
    warnings.filterwarnings("ignore")
    from src.myntra.pipeline import main, scan_content_hashes
    pairs = dict(scan_content_hashes("tests/fixtures/products_export.csv",
                 template_path="templates/myntra/Myntra-Sku-Template-2026-06-16.xlsx"))
    sku0 = list(pairs)[0]
    res = main(
        template_path="templates/myntra/Myntra-Sku-Template-2026-06-16.xlsx",
        csv_path="tests/fixtures/products_export.csv",
        out_dir=str(tmp_path / "out"), config_dir="config/myntra",
        fetch=_fake_fetch(), upload=False,
        only_skus={sku0},
        style_group_id_by_sku={sku0: 77},
        hsn_by_sku={sku0: "63079090"},
    )
    assert res["products"] == 1                         # filtered to one SKU
    rec = res["records"][0]
    assert rec["sku"] == sku0
    assert rec["style_group_id"] == 77
    assert rec["hsn"] == "63079090"
    assert rec["content_hash"] == pairs[sku0]           # excludes id+HSN → matches scan
    ws = openpyxl.load_workbook(tmp_path / "out" / "myntra_filled.xlsx")["Sarees"]
    hdr = {ws.cell(3, c).value: c for c in range(1, ws.max_column + 1)}
    assert ws.cell(4, hdr["styleGroupId"]).value == 77
    assert ws.cell(4, hdr["HSN"]).value == 63079090
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_pipeline_override.py -q`
Expected: FAIL — `map_product` has no `hsn_override` / `main` has no `only_skus` / no `scan_content_hashes`.

- [ ] **Step 3: Add `hsn_override` to the mapper**

In `src/myntra/mapper.py`, change the signature:

```python
def map_product(product, template, column_map, constants, rules=None, hsn_by_signature=None,
                hsn_override=None):
```

Replace the step-5b block with one that lets a pinned per-SKU HSN win:

```python
    # 5b. HSN. A pinned per-SKU code (hsn_override, e.g. a rebuild from the SKU
    # registry) wins; otherwise use the injected signature->hsn map from the KB
    # review. HSN is never guessed from the signature alone — unresolved => flag.
    if hsn_override:
        _set(row, template, "HSN", str(hsn_override))
    elif hsn_by_signature is not None:
        category = constants.get("articleType", "")
        fabric_keywords = (fabric_cfg.get("order") or [])
        sig = signature(product, category, fabric_keywords)
        hsn = hsn_by_signature.get(sig)
        if hsn:
            _set(row, template, "HSN", str(hsn))
        else:
            row.flags.append(Flag(sku=row.sku, field="HSN",
                                  reason="no HSN learned for signature", value=sig))
```

- [ ] **Step 4: Add pipeline overrides, records, and the scan helper**

In `src/myntra/pipeline.py`, add the import near the top:

```python
from src.myntra.sku_registry import content_hash
```

Change `main`'s signature:

```python
def main(template_path=None, csv_path=None, out_dir="output", config_dir="config/myntra",
         fetch=None, upload=None, style_group_id_start=None, hsn_by_signature=None,
         only_skus=None, style_group_id_by_sku=None, hsn_by_sku=None):
```

Replace the product loop (the `rows = []` / `for i, p in enumerate(...)` block) with one that filters, pins, and records:

```python
    products = read_products(csv_path)
    if only_skus is not None:
        products = [p for p in products if p.sku in only_skus]

    style_group_id_by_sku = style_group_id_by_sku or {}
    hsn_by_sku = hsn_by_sku or {}

    rows, records = [], []
    for i, p in enumerate(products, start=1):
        mapped = map_product(p, template, column_map, constants, rules,
                             hsn_by_signature=hsn_by_signature,
                             hsn_override=hsn_by_sku.get(p.sku))
        sid = None
        if rules.get("auto_style_group_id") and "styleGroupId" in template.col_index_by_header:
            if p.sku in style_group_id_by_sku:
                sid = style_group_id_by_sku[p.sku]
            else:
                base = (style_group_id_start if style_group_id_start is not None
                        else rules.get("style_group_id_start", 1))
                sid = base + i - 1
            mapped.cells["styleGroupId"] = str(sid)
        if fetch is None:
            img = process_images(p, specs, images_dir)
        else:
            img = process_images(p, specs, images_dir, fetch=fetch)
        rows.append((mapped, img))
        records.append({"sku": p.sku, "style_group_id": sid,
                        "hsn": mapped.cells.get("HSN"),
                        "content_hash": content_hash(mapped.cells)})
```

Change the return statement to include `records` and the filtered product count:

```python
    return {"filled": filled_path, "report": report_path,
            "products": len(products), "uploaded": uploaded, "records": records}
```

Add the scan helper at the end of the module (before `cli`):

```python
def scan_content_hashes(csv_path, template_path=None, config_dir="config/myntra"):
    """(sku, content_hash) per product with HSN unset and no image work — the
    upload-time input to the duplicate-generation guard."""
    template_path = template_path or _resolve(
        "Myntra-Sku-Template-2026-06-16.xlsx", "templates/myntra")
    column_map = yaml.safe_load(open(os.path.join(config_dir, "column_map.yaml")))
    constants = yaml.safe_load(open(os.path.join(config_dir, "constants.yaml")))
    rules = yaml.safe_load(open(os.path.join(config_dir, "rules.yaml")))
    template = read_template(template_path)
    out = []
    for p in read_products(csv_path):
        mapped = map_product(p, template, column_map, constants, rules, hsn_by_signature=None)
        out.append((p.sku, content_hash(mapped.cells)))
    return out
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_pipeline_override.py tests/test_mapper.py -q`
Expected: PASS (existing overrides/mapper tests still green; new tests green).

- [ ] **Step 6: Commit**

```bash
git add src/myntra/mapper.py src/myntra/pipeline.py tests/test_pipeline_override.py
git commit -m "feat(pipeline): per-SKU id/HSN pins, build records, scan_content_hashes"
```

---

### Task 4: Generate router — partition, warn screen, rebuild download, record on build

**Files:**
- Modify: `src/web/routers/generate.py`
- Create: `src/web/templates/_dedup_warn.html`
- Test: `tests/web/test_generate.py`

**Interfaces:**
- Consumes: `scan_content_hashes`, `read_registry`, `partition`, `record`, `sku_registry_store`, `read_products` (existing).
- Produces:
  - `POST /generate` — after saving the CSV, partitions SKUs; **any repeat** → persist `dedup.json` (`csv_path`, `count`, `new`, `edited`, `repeat`) and render `_dedup_warn.html`; **no repeat** → today's HSN flow.
  - `POST /generate/rebuild/{job_id}` — rebuild the repeat SKUs with pinned ids/HSN from the registry and stream the `.xlsx` (`FileResponse`); ledger untouched.
  - Build recording: `_run_generate` writes each `res["records"]` SKU into the registry.
  - `_start_build`/`_spawn`/`_run_generate` gain `only_skus=None` (passed to `pipeline_main`).
  - `_dedup_warn.html` context: `job_id`, `repeat` (list[str]), `has_new` (bool), `new_count` (int).

- [ ] **Step 1: Create the warning template**

Create `src/web/templates/_dedup_warn.html`:

```html
<div class="panel">
  <h3 class="flag">⚠ You are uploading SKUs that were already generated</h3>
  <p><strong>This file has already been generated.</strong> If you got an error from
    Myntra, upload the error file on the <a href="/fix">Fix errors</a> page.</p>
  <p class="hint mono">Already generated: {{ repeat | join(", ") }}</p>
  <button class="btn" hx-post="/generate/rebuild/{{ job_id }}" hx-target="#dl"
          hx-swap="innerHTML">⬇ Download the already-generated sheet</button>
  {% if has_new %}
    <button class="btn" hx-post="/generate/new-only/{{ job_id }}"
            hx-target="#progress" hx-swap="innerHTML">Generate the {{ new_count }} new SKUs only →</button>
  {% endif %}
  <div id="dl" style="margin-top:10px"></div>
</div>
```

- [ ] **Step 2: Wire partition + record + rebuild into the router**

In `src/web/routers/generate.py`, extend the imports:

```python
from src.myntra.pipeline import main as pipeline_main, scan_content_hashes  # noqa: F401 (patched in tests)
from src.myntra.sku_registry import read_registry, partition, record
from src.web.settings import ledger_store, hsn_store, sku_registry_store
```

In `generate_submit`, after `count = count_products(csv_path)` and before the HSN pre-scan, insert the dedup partition:

```python
    # Duplicate-generation guard: have we already generated any of these SKUs?
    pairs = scan_content_hashes(csv_path)
    parts = partition(pairs, read_registry(sku_registry_store(settings)))
    if parts["repeat"]:
        new_skus = parts["new"] + parts["edited"]
        with open(os.path.join(job_dir, "dedup.json"), "w", encoding="utf-8") as fh:
            json.dump({"csv_path": csv_path, "count": count,
                       "new": parts["new"], "edited": parts["edited"],
                       "repeat": parts["repeat"]}, fh)
        resp = _templates().TemplateResponse(
            request, "_dedup_warn.html",
            {"job_id": job.id, "repeat": parts["repeat"],
             "has_new": bool(new_skus), "new_count": len(new_skus)})
        resp.headers["x-job-id"] = job.id
        return resp
```

Thread `only_skus` through the build helpers. Change `_start_build`, `_spawn`, `_run_generate` signatures to carry it (defaulting to `None`), and forward it to `pipeline_main`; then record the build:

```python
def _start_build(request, job, csv_path, job_dir, count, settings,
                 hsn_by_signature=None, only_skus=None):
    start, batch_id = reserve(ledger_store(settings), count, "myntra_filled.xlsx")
    job.batch_id = batch_id
    job.range = [start, start + count - 1]
    job.status = "running"
    _spawn(job.id, csv_path, job_dir, start, settings, hsn_by_signature, only_skus)
    resp = _templates().TemplateResponse(
        request, "_stepper.html", {"job": job, "count": count})
    resp.headers["x-job-id"] = job.id
    return resp


def _spawn(job_id, csv_path, job_dir, start, settings, hsn_by_signature=None, only_skus=None):
    import threading
    threading.Thread(
        target=_run_generate,
        args=(job_id, csv_path, job_dir, start, settings, hsn_by_signature, only_skus),
        daemon=True).start()


def _run_generate(job_id, csv_path, job_dir, start, settings,
                  hsn_by_signature=None, only_skus=None):
    try:
        store.set_step(job_id, "Ingest CSV", "active")
        res = pipeline_main(csv_path=csv_path, out_dir=job_dir,
                            style_group_id_start=start,
                            hsn_by_signature=hsn_by_signature, only_skus=only_skus)
        reg = sku_registry_store(settings)
        for r in res.get("records", []):
            record(reg, r["sku"], r["content_hash"], r["style_group_id"], r["hsn"])
        for name in ["Ingest CSV", "Map attributes", "Images → S3", "Fill & validate", "Ready"]:
            store.set_step(job_id, name, "done")
        store.set_step(job_id, "Images → S3", "done", count=res.get("uploaded"))
        store.finish(job_id, res)
    except Exception as exc:  # surface failure to the UI
        store.fail(job_id, f"{type(exc).__name__}: {exc}")
```

Add the rebuild route (near the other POST routes):

```python
@router.post("/generate/rebuild/{job_id}")
def rebuild_download(request: Request, job_id: str):
    get_user(request)
    settings = get_settings(request)
    job_id = _safe_job_id(job_id)
    job_dir = os.path.join(RUNTIME, job_id)
    dedup_path = os.path.join(job_dir, "dedup.json")
    if not os.path.exists(dedup_path):
        raise HTTPException(status_code=404, detail="session expired, please re-upload")
    with open(dedup_path, encoding="utf-8") as fh:
        data = json.load(fh)
    repeat = data["repeat"]
    reg = read_registry(sku_registry_store(settings))
    sid_by_sku = {s: reg[s]["style_group_id"] for s in repeat if s in reg}
    hsn_by_sku = {s: reg[s]["hsn"] for s in repeat if s in reg}
    out_dir = os.path.join(job_dir, "rebuild")
    os.makedirs(out_dir, exist_ok=True)
    res = pipeline_main(csv_path=data["csv_path"], out_dir=out_dir,
                        only_skus=set(repeat),
                        style_group_id_by_sku=sid_by_sku, hsn_by_sku=hsn_by_sku)
    return FileResponse(res["filled"], filename="myntra_filled.xlsx")
```

- [ ] **Step 3: Update the web harness + write the failing tests**

In `tests/web/test_generate.py`, give `_client` a registry path:

```python
def _client(tmp_path):
    s = Settings(auth_disabled=True, s3_bucket="b",
                 ledger_local_path=str(tmp_path / "led.json"),
                 hsn_local_path=str(tmp_path / "hsn.json"),
                 sku_registry_local_path=str(tmp_path / "reg.json"))
    return TestClient(create_app(s)), s
```

Add these tests (they use the real fixture CSV, which has SKUs; `_pass_hsn_and_wait` from the HSN work still applies for the record test):

```python
def test_build_records_registry(tmp_path, monkeypatch):
    client, settings = _client(tmp_path)

    def fake_main(csv_path=None, out_dir=None, style_group_id_start=None,
                  hsn_by_signature=None, only_skus=None, **kw):
        with open(f"{out_dir}/myntra_filled.xlsx", "wb") as fh:
            fh.write(b"x")
        with open(f"{out_dir}/report.txt", "w") as fh:
            fh.write("r\n")
        return {"filled": f"{out_dir}/myntra_filled.xlsx", "report": f"{out_dir}/report.txt",
                "products": 1, "uploaded": 0,
                "records": [{"sku": "S1", "style_group_id": 13, "hsn": "50072010",
                             "content_hash": "h1"}]}

    monkeypatch.setattr(gen, "pipeline_main", fake_main)
    monkeypatch.setattr(gen, "count_products", lambda path: 1)

    csv = b"Handle,Title\na,Plain Saree\n"   # SKU empty -> partition NEW, proceeds
    r = client.post("/generate", files={"file": ("products_export.csv", csv, "text/csv")})
    _pass_hsn_and_wait(client, r.headers["x-job-id"])

    from src.myntra.sku_registry import read_registry
    from src.web.settings import sku_registry_store
    reg = read_registry(sku_registry_store(settings))
    assert reg["S1"]["style_group_id"] == 13 and reg["S1"]["hsn"] == "50072010"


def test_repeat_upload_warns_and_skips_hsn(tmp_path):
    client, settings = _client(tmp_path)
    # Pre-seed the registry with the fixture's real hashes so the re-upload is a repeat.
    from src.myntra.pipeline import scan_content_hashes
    from src.myntra.sku_registry import record
    from src.web.settings import sku_registry_store
    store = sku_registry_store(settings)
    for sku, h in scan_content_hashes("tests/fixtures/products_export.csv"):
        record(store, sku, h, 55, "50072010")

    with open("tests/fixtures/products_export.csv", "rb") as fh:
        csv = fh.read()
    r = client.post("/generate", files={"file": ("products_export.csv", csv, "text/csv")})
    assert "already generated" in r.text.lower()
    assert "One-time HSN" not in r.text          # HSN review skipped for a pure repeat


def test_rebuild_download_serves_xlsx_with_pinned_values(tmp_path, monkeypatch):
    client, settings = _client(tmp_path)
    from src.myntra.pipeline import scan_content_hashes
    from src.myntra.sku_registry import record
    from src.web.settings import sku_registry_store
    store = sku_registry_store(settings)
    pinned = {}
    for i, (sku, h) in enumerate(scan_content_hashes("tests/fixtures/products_export.csv")):
        record(store, sku, h, 55 + i, "50072010")
        pinned[sku] = 55 + i

    seen = {}

    def fake_main(csv_path=None, out_dir=None, only_skus=None,
                  style_group_id_by_sku=None, hsn_by_sku=None, **kw):
        seen["ids"] = style_group_id_by_sku
        seen["hsn"] = hsn_by_sku
        with open(f"{out_dir}/myntra_filled.xlsx", "wb") as fh:
            fh.write(b"xlsx")
        return {"filled": f"{out_dir}/myntra_filled.xlsx", "report": "", "products": 2,
                "uploaded": 0, "records": []}

    monkeypatch.setattr(gen, "pipeline_main", fake_main)

    with open("tests/fixtures/products_export.csv", "rb") as fh:
        csv = fh.read()
    r = client.post("/generate", files={"file": ("products_export.csv", csv, "text/csv")})
    job_id = r.headers["x-job-id"]
    dl = client.post(f"/generate/rebuild/{job_id}")
    assert dl.status_code == 200
    assert dl.content == b"xlsx"
    assert seen["ids"] == pinned                       # pinned styleGroupIds forced
    assert set(seen["hsn"].values()) == {"50072010"}   # pinned HSN forced
    # ledger untouched by a rebuild
    from src.myntra.groupid_ledger import read_ledger
    from src.web.settings import ledger_store
    assert read_ledger(ledger_store(settings))["next_style_group_id"] == 1
```

- [ ] **Step 4: Run the web suite to verify it passes**

Run: `python -m pytest tests/web/test_generate.py -q`
Expected: PASS (new dedup tests green; the earlier HSN/flow tests still green — an empty-SKU minimal CSV partitions as NEW and proceeds).

- [ ] **Step 5: Commit**

```bash
git add src/web/routers/generate.py src/web/templates/_dedup_warn.html tests/web/test_generate.py
git commit -m "feat(web): pre-upload duplicate guard — warn + rebuild download; record on build"
```

---

### Task 5: "Generate the new SKUs only" (mixed files)

**Files:**
- Modify: `src/web/routers/generate.py` (new-only route; thread `only_skus` through the HSN pre-scan/review/build)
- Test: `tests/web/test_generate.py`

**Interfaces:**
- Consumes: `dedup.json` (`new`, `edited`, `csv_path`), the existing HSN pre-scan/build helpers, `_start_build(..., only_skus=...)` (Task 4).
- Produces: `POST /generate/new-only/{job_id}` — runs the HSN pre-scan/review (or direct build) restricted to `new`+`edited` SKUs, then builds only those and records them. The HSN pre-scan and `hsn_submit` carry `only_skus` so the review lists only the kept SKUs' signatures and the build is filtered.

- [ ] **Step 1: Refactor the HSN pre-scan to accept a SKU filter**

In `src/web/routers/generate.py`, extract the HSN pre-scan/build decision from `generate_submit` into a helper (so both the no-repeat path and the new-only route share it):

```python
def _hsn_prescan_or_build(request, job, csv_path, job_dir, count, settings, only_skus=None):
    constants = _load_yaml("constants.yaml")
    rules = _load_yaml("rules.yaml")
    category = constants.get("articleType", "")
    fabric_keywords = (rules.get("fabric_detection") or {}).get("order") or []
    kb = read_kb(hsn_store(settings))
    grouped = {}
    for p in read_products(csv_path):
        if only_skus is not None and p.sku not in only_skus:
            continue
        grouped.setdefault(signature(p, category, fabric_keywords), []).append(p.title)

    if not grouped:
        return _start_build(request, job, csv_path, job_dir, count, settings,
                            only_skus=only_skus)

    signatures = [{"signature": sig, "examples": names[:5], "suggestions": suggest(kb, sig)}
                  for sig, names in grouped.items()]
    with open(os.path.join(job_dir, "hsn.json"), "w", encoding="utf-8") as fh:
        json.dump({"csv_path": csv_path, "count": count, "signatures": signatures,
                   "only_skus": (list(only_skus) if only_skus is not None else None)}, fh)
    job.status = "awaiting_hsn"
    resp = _templates().TemplateResponse(
        request, "_hsn_review.html", {"job_id": job.id, "signatures": signatures})
    resp.headers["x-job-id"] = job.id
    return resp
```

Replace the HSN block in `generate_submit` (everything from `constants = _load_yaml("constants.yaml")` through the `_hsn_review.html` return) with:

```python
    return _hsn_prescan_or_build(request, job, csv_path, job_dir, count, settings)
```

In `hsn_submit`, read the optional filter and pass it through the build (and reserve only that many ids). Change the build tail of `hsn_submit`:

```python
    only = data.get("only_skus")
    only_set = set(only) if only is not None else None
    build_count = len(only_set) if only_set is not None else data["count"]
    return _start_build(request, job, data["csv_path"], job_dir,
                        build_count, settings, hsn_by_signature, only_skus=only_set)
```

- [ ] **Step 2: Add the new-only route**

Add to `src/web/routers/generate.py`:

```python
@router.post("/generate/new-only/{job_id}", response_class=HTMLResponse)
def generate_new_only(request: Request, job_id: str):
    get_user(request)
    settings = get_settings(request)
    job_id = _safe_job_id(job_id)
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="unknown job")
    job_dir = os.path.join(RUNTIME, job_id)
    dedup_path = os.path.join(job_dir, "dedup.json")
    if not os.path.exists(dedup_path):
        raise HTTPException(status_code=404, detail="session expired, please re-upload")
    with open(dedup_path, encoding="utf-8") as fh:
        data = json.load(fh)
    only = set(data["new"]) | set(data["edited"])
    return _hsn_prescan_or_build(request, job, data["csv_path"], job_dir,
                                 len(only), settings, only_skus=only)
```

- [ ] **Step 3: Write the failing test**

Add to `tests/web/test_generate.py`:

```python
def test_generate_new_only_builds_and_records_only_new(tmp_path, monkeypatch):
    client, settings = _client(tmp_path)
    from src.myntra.pipeline import scan_content_hashes
    from src.myntra.sku_registry import record, read_registry
    from src.web.settings import sku_registry_store

    pairs = scan_content_hashes("tests/fixtures/products_export.csv")
    store = sku_registry_store(settings)
    # Seed ONLY the first SKU as already-generated -> the file is mixed.
    first_sku, first_hash = pairs[0]
    new_sku = pairs[1][0]
    record(store, first_sku, first_hash, 55, "50072010")

    built = {}

    def fake_main(csv_path=None, out_dir=None, style_group_id_start=None,
                  hsn_by_signature=None, only_skus=None, **kw):
        built["only_skus"] = only_skus
        with open(f"{out_dir}/myntra_filled.xlsx", "wb") as fh:
            fh.write(b"x")
        with open(f"{out_dir}/report.txt", "w") as fh:
            fh.write("r\n")
        return {"filled": f"{out_dir}/myntra_filled.xlsx", "report": f"{out_dir}/report.txt",
                "products": 1, "uploaded": 0,
                "records": [{"sku": new_sku, "style_group_id": 1, "hsn": "63079090",
                             "content_hash": pairs[1][1]}]}

    monkeypatch.setattr(gen, "pipeline_main", fake_main)

    with open("tests/fixtures/products_export.csv", "rb") as fh:
        csv = fh.read()
    r = client.post("/generate", files={"file": ("products_export.csv", csv, "text/csv")})
    job_id = r.headers["x-job-id"]
    assert "already generated" in r.text.lower()

    # Choose "generate new only" -> HSN review for just the new SKU, then build it.
    r2 = client.post(f"/generate/new-only/{job_id}")
    assert "One-time HSN" in r2.text
    _pass_hsn_and_wait(client, job_id, hsn="63079090")

    assert built["only_skus"] == {new_sku}
    reg = read_registry(sku_registry_store(settings))
    assert new_sku in reg                      # new SKU recorded
```

- [ ] **Step 4: Run the web suite to verify it passes**

Run: `python -m pytest tests/web/test_generate.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/web/routers/generate.py tests/web/test_generate.py
git commit -m "feat(web): 'generate new SKUs only' for mixed repeat/new files"
```

---

### Task 6: Full-suite verification, docs, local smoke

**Files:** `AGENTS.md`, `docs/ARCHITECTURE.md`, `docs/infra-resources.md` (docs only).

- [ ] **Step 1: Run the entire suite**

Run: `python -m pytest -q`
Expected: all green. Note the count.

- [ ] **Step 2: Record the module + state key in the in-repo docs**

- `docs/ARCHITECTURE.md`: add a row for `src/myntra/sku_registry.py` (per-SKU generation registry; key `state/sku_registry.json`; recorded at generate time) next to `hsn_kb.py`; add `SKU_REGISTRY_LOCAL_PATH` to the `settings.py` row.
- `docs/infra-resources.md`: add `state/sku_registry.json` to the bucket keys row.
- `AGENTS.md`: add `sku_registry` to the Layer-2 backend file list, and `SKU_REGISTRY_LOCAL_PATH` to the local-run env line.

Commit:

```bash
git add AGENTS.md docs/ARCHITECTURE.md docs/infra-resources.md
git commit -m "docs: record SKU registry module + state key + env var"
```

- [ ] **Step 3: Local smoke**

Run:

```bash
AUTH_DISABLED=1 LEDGER_LOCAL_PATH="$TMPDIR/led.json" HSN_LOCAL_PATH="$TMPDIR/hsn.json" \
SKU_REGISTRY_LOCAL_PATH="$TMPDIR/reg.json" python -m uvicorn src.web.main:app --port 8000
```

Confirm: upload a real export → HSN review → build (records the registry). **Re-upload the same file** → the **"already generated"** warning (no HSN step), **Download the already-generated sheet** streams an `.xlsx`, and the styleGroupId ledger counter is unchanged. Add one new product to the file and re-upload → warning with **"Generate the N new SKUs only"**, which asks HSN for just the new one and builds it.

- [ ] **Step 4: Stop the server.** No merge to `main`.

---

## Self-Review

**Spec coverage (against `2026-07-06-sku-dedup-preupload-guard-design.md`):**
- §1 registry (own store/local path/helper) → Task 1 + Task 2. ✔
- §2 `content_hash` excludes styleGroupId+HSN, computed pre-HSN → Task 1 (`_EXCLUDE`) + Task 3 (`scan_content_hashes` maps with `hsn_by_signature=None`). ✔
- §3 flow: partition on upload; no-repeat → HSN flow + record; any-repeat → warn screen with the two actions → Task 4 (partition/warn/record) + Task 5 (new-only). ✔
- §4 rebuild-on-demand pins id+HSN, no ledger change → Task 3 (`only_skus`/`style_group_id_by_sku`/`hsn_by_sku`) + Task 4 (`/generate/rebuild`). ✔
- §5 new-only treats EDITED like NEW → Task 5. ✔
- Decisions (a)–(e): app-owned registry (T1), record at generate time (T4 `_run_generate`), warn+download not silent (T4), rebuild determinism (T3/T4), hash excludes id+HSN (T1). ✔
- Testing bullets → covered across T1/T3/T4/T5 tests.

**Placeholder scan:** every code step shows the actual content and an exact `pytest` command with expected output. Task 6 Step 2 lists exact doc rows to add (mirrors the HSN-KB doc task already merged).

**Type consistency:** `content_hash(cells)`, `read_registry(store)`, `partition(sku_hashes, registry)`, `record(store, sku, content_hash, style_group_id, hsn)` are defined in Task 1 and consumed with identical signatures in Tasks 3–5. `main(..., only_skus, style_group_id_by_sku, hsn_by_sku)` and its `records` return (T3) match the router calls (T4 `_run_generate`, `/rebuild`). `res["records"]` item keys (`sku`, `style_group_id`, `hsn`, `content_hash`) match `record()`'s parameters. `dedup.json` keys (`csv_path`, `count`, `new`, `edited`, `repeat`) written in T4 match the reads in `/rebuild` (T4) and `/new-only` (T5). `hsn.json` gains `only_skus`, written in T5's `_hsn_prescan_or_build` and read in `hsn_submit`. `_dedup_warn.html` context (`job_id`, `repeat`, `has_new`, `new_count`) matches its render site.

**Known scope calls (documented):** EDITED SKUs regenerate as NEW (fresh id + asked HSN) — reusing ids for edits is the post-Myntra spec. Rebuild rebuilds only the REPEAT SKUs into the sheet. CLI guard is out of scope.
```
