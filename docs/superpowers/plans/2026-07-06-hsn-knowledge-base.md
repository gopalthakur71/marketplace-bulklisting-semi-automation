# HSN Knowledge Base (Part A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Teach the Generate flow to learn an 8-digit HSN code once per `category|fabric` signature and reuse it forever, replacing the two hardcoded HSN codes with a learnable knowledge base and a "gather-then-ask" review step inside Generate.

**Architecture:** A new JSON store `src/myntra/hsn_kb.py` (shaped exactly like `groupid_ledger.py`) holds `signature → [{hsn, examples, count, last_used}]`. A shared pure `signature(product, category, fabric_keywords)` is used by BOTH the Generate pre-scan and the mapper so they always agree. Generate gains a pause: on CSV upload it pre-scans the batch's signatures, shows a review screen (empty 8-digit input + suggestion chips per signature), and only builds after the user submits valid codes — which are `learn()`ed into the KB and injected into the mapper as a `signature → hsn` map. HSN drops out of the `rules.yaml` fabric block (those two codes become the KB seed).

**Tech Stack:** Python 3, pytest, FastAPI + Starlette `TestClient`, Jinja2 + htmx, pandas (Shopify CSV read), openpyxl-derived `TemplateInfo`.

## Global Constraints

- Branch: `feat/hsn-knowledge-base` (off `main`). CI/CD deploys **only** on `main`, so **do not merge to main in this batch** — local verify only. (Part B / B.1–B.4 already live on this branch.)
- Vocab-controlled cells go through `_set` / `_set_forced` (flag-don't-guess). HSN is **not** vocab-controlled (free numeric header, already in `NUMERIC_HEADERS` in `src/myntra/fill.py`), so it is written directly via `_set` and formatted numerically by `fill.py`.
- `signature()` MUST be the single shared function (in `hsn_kb.py`) imported by both the Generate router pre-scan and `mapper.py`. Never duplicate the derivation logic.
- **Always ask, never silently auto-fill:** known signatures are still shown on the review screen (with suggestion chips); the user always submits. Only skip the review when the batch yields **zero** signatures (e.g. empty CSV) → build directly.
- HSN validation: exactly 8 digits (`re.fullmatch(r"\d{8}", v)`). Invalid input re-renders the review screen with an inline error and the entered values preserved.
- Seed values (must not regress): `saree|pure cotton → 52081120`, `saree|pure silk → 50072010`. These move from `rules.yaml` into `hsn_kb.SEED`.
- Local preview: `AUTH_DISABLED=1 LEDGER_LOCAL_PATH=<tmp>/ledger.json HSN_LOCAL_PATH=<tmp>/hsn_kb.json python -m uvicorn src.web.main:app --reload` → http://localhost:8000/generate
- Run the full suite with `python -m pytest -q` from repo root.

## File Structure

- **Create** `src/myntra/hsn_kb.py` — signature derivation + KB store (seed, read, suggest, learn). Pure; no web/AWS deps.
- **Create** `src/web/templates/_hsn_review.html` — the pre-build review screen (one row per signature, 8-digit input, suggestion chips).
- **Modify** `src/web/settings.py` — add `Settings.hsn_local_path`, parse `HSN_LOCAL_PATH`, add `hsn_store(settings)` mirroring `ledger_store`.
- **Modify** `src/myntra/mapper.py` — import `signature`; add an HSN-from-injected-map step; `map_product` gains `hsn_by_signature=None`.
- **Modify** `config/myntra/rules.yaml` — drop the `HSN` key from the `cotton`/`silk` fabric blocks.
- **Modify** `src/myntra/pipeline.py` — `main` gains `hsn_by_signature=None`, threaded into `map_product`.
- **Modify** `src/web/routers/generate.py` — pre-scan on upload → `awaiting_hsn` + persisted `hsn.json` + review screen; new `POST /generate/hsn/{job_id}` (validate → learn → build); refactor build-start into `_start_build`.
- **Modify** tests: `tests/test_mapper.py`, `tests/test_config_loads.py`, `tests/web/test_generate.py`; **create** `tests/test_hsn_kb.py`.

---

### Task 1: HSN knowledge base core (`hsn_kb.py`)

**Files:**
- Create: `src/myntra/hsn_kb.py`
- Test: `tests/test_hsn_kb.py`

**Interfaces:**
- Consumes: a store object with `get_json(key)` / `put_json(key, data)` (the same duck-typed store as `groupid_ledger.py`; `LocalJsonStore` / `S3JsonStore`). A `Product`-like object exposing `.fabric` and `.title`.
- Produces:
  - `signature(product, category, fabric_keywords=()) -> str` — normalized `"cat|fabric"`.
  - `read_kb(store, key=HSN_KEY) -> dict` — `{"classifications": {sig: [entry, ...]}}`, seeding an empty/absent KB from `SEED`.
  - `suggest(kb, sig) -> list[entry]` — entries for a signature, most-used first (may be empty).
  - `learn(store, sig, hsn, example_name=None, key=HSN_KEY) -> dict` — upsert; returns the full KB.
  - Module constants `HSN_KEY = "state/hsn_kb.json"`, `SEED` (dict).
  - entry shape: `{"hsn": str, "examples": list[str], "count": int, "last_used": str|None}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_hsn_kb.py`:

```python
import json

from src.core.models import Product
from src.myntra.hsn_kb import signature, read_kb, suggest, learn, SEED


class FakeStore:
    """In-memory stand-in for S3JsonStore (mirrors tests/test_groupid_ledger.py)."""
    def __init__(self):
        self.data = {}

    def get_json(self, key):
        return self.data.get(key)

    def put_json(self, key, data):
        self.data[key] = json.loads(json.dumps(data))  # deep copy


def _p(title="", fabric=None):
    return Product(handle="h", sku="S", title=title, vendor="", tags="", body_html="",
                   price=1.0, compare_at_price=None, color=None, fabric=fabric,
                   size=None, status="active", images=[])


def test_signature_from_fabric_metafield():
    assert signature(_p(title="Blue Saree", fabric="Pure Silk"), "Sarees") == "saree|pure silk"


def test_signature_falls_back_to_title_keyword():
    sig = signature(_p(title="Lavender Pure Cotton Saree", fabric=None), "Sarees",
                    fabric_keywords=["cotton", "silk"])
    assert sig == "saree|cotton"


def test_signature_unknown_when_no_fabric_and_no_keyword():
    assert signature(_p(title="Plain Saree", fabric=None), "Sarees",
                     fabric_keywords=["cotton", "silk"]) == "saree|unknown"


def test_signature_normalizes_whitespace_and_case():
    assert signature(_p(title="x", fabric="  Pure   SILK "), "  Sarees ") == "saree|pure silk"


def test_read_kb_seeds_when_empty():
    kb = read_kb(FakeStore())
    assert kb["classifications"]["saree|pure cotton"][0]["hsn"] == "52081120"
    assert kb["classifications"]["saree|pure silk"][0]["hsn"] == "50072010"


def test_suggest_returns_seed_entry_then_empty_for_unknown():
    kb = read_kb(FakeStore())
    assert suggest(kb, "saree|pure silk")[0]["hsn"] == "50072010"
    assert suggest(kb, "saree|unknown") == []


def test_learn_upserts_count_examples_and_new_code():
    s = FakeStore()
    learn(s, "saree|unknown", "63079090", example_name="Plain Saree")
    kb = read_kb(s)
    e = kb["classifications"]["saree|unknown"][0]
    assert e["hsn"] == "63079090"
    assert e["count"] == 1
    assert e["examples"] == ["Plain Saree"]
    # learning the SAME code again bumps count, dedups example
    learn(s, "saree|unknown", "63079090", example_name="Plain Saree")
    assert read_kb(s)["classifications"]["saree|unknown"][0]["count"] == 2
    # a DIFFERENT code for the same signature is added as a second suggestion
    learn(s, "saree|unknown", "52081120", example_name="Other")
    assert {e["hsn"] for e in suggest(read_kb(s), "saree|unknown")} == {"63079090", "52081120"}


def test_suggest_orders_most_used_first():
    s = FakeStore()
    learn(s, "saree|unknown", "111", example_name="a")
    learn(s, "saree|unknown", "222", example_name="b")
    learn(s, "saree|unknown", "222", example_name="c")   # 222 now count 2
    assert suggest(read_kb(s), "saree|unknown")[0]["hsn"] == "222"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_hsn_kb.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.myntra.hsn_kb'`.

- [ ] **Step 3: Implement `hsn_kb.py`**

Create `src/myntra/hsn_kb.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_hsn_kb.py -q`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add src/myntra/hsn_kb.py tests/test_hsn_kb.py
git commit -m "feat(hsn): HSN knowledge base — signature, read/suggest/learn (A.1-A.3)"
```

---

### Task 2: Settings — `hsn_local_path` + `hsn_store`

**Files:**
- Modify: `src/web/settings.py:37` (Settings field), `:69` (parse env), end of file (helper)
- Test: `tests/web/test_settings.py` (create if absent, else append)

**Interfaces:**
- Consumes: `Settings`, `LocalJsonStore`, `S3JsonStore` (existing).
- Produces: `Settings.hsn_local_path: str | None`; `hsn_store(settings) -> store`. `HSN_LOCAL_PATH` env var. `hsn_store` returns `LocalJsonStore(hsn_local_path)` when set, else an `S3JsonStore` on the configured bucket — **exactly mirroring `ledger_store`** (they must use different local paths because `LocalJsonStore` is one-file-per-path).

- [ ] **Step 1: Write the failing test**

Create `tests/web/test_settings.py` (or append if it exists):

```python
from src.web.settings import Settings, load_settings, hsn_store, LocalJsonStore


def test_hsn_local_path_parsed_from_env():
    s = load_settings(env={"HSN_LOCAL_PATH": "/tmp/hsn.json"}, ssm=lambda name: None)
    assert s.hsn_local_path == "/tmp/hsn.json"


def test_hsn_store_uses_local_path_when_set(tmp_path):
    s = Settings(hsn_local_path=str(tmp_path / "hsn.json"))
    store = hsn_store(s)
    assert isinstance(store, LocalJsonStore)
    store.put_json("state/hsn_kb.json", {"classifications": {}})
    assert store.get_json("state/hsn_kb.json") == {"classifications": {}}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/web/test_settings.py -q`
Expected: FAIL — `ImportError: cannot import name 'hsn_store'`.

- [ ] **Step 3: Add the field, env parse, and helper**

In `src/web/settings.py`, add the dataclass field after `ledger_local_path`:

```python
    ledger_local_path: str | None = None
    hsn_local_path: str | None = None
```

In `load_settings`, after the `s.ledger_local_path = ...` line:

```python
    s.ledger_local_path = env.get("LEDGER_LOCAL_PATH") or None
    s.hsn_local_path = env.get("HSN_LOCAL_PATH") or None
```

At the end of the file, after `ledger_store`, add:

```python
def hsn_store(settings: Settings):
    """Store for the HSN knowledge base. Mirrors ledger_store, but MUST use its
    own local path — LocalJsonStore writes one file per path, so sharing the
    ledger's path would clobber it."""
    if settings.hsn_local_path:
        return LocalJsonStore(settings.hsn_local_path)
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
git commit -m "feat(web): hsn_store + HSN_LOCAL_PATH setting (A.2)"
```

---

### Task 3: Mapper integration — HSN from injected map; drop it from the fabric block

**Files:**
- Modify: `src/myntra/mapper.py:1` (import), `:112` (signature), after `:158` (new step)
- Modify: `config/myntra/rules.yaml:8-17` (drop `HSN` from cotton/silk)
- Modify: `tests/test_mapper.py:74-121` (fabric-block tests) — remove HSN from `FABRIC_RULES`, drop the HSN assertions, add HSN-map tests
- Modify: `tests/test_config_loads.py:28` (assert HSN gone from the fabric block)

**Interfaces:**
- Consumes: `signature` from `src.myntra.hsn_kb`; `constants` (for `articleType`), `rules` (for `fabric_detection.order`) already threaded into `map_product`.
- Produces: `map_product(product, template, column_map, constants, rules=None, hsn_by_signature=None) -> MappedRow`. When `hsn_by_signature` is a dict, `HSN` is set from it (flagged if the signature is unresolved); when `None` (CLI path), HSN is left unset. The fabric block no longer writes HSN.

- [ ] **Step 1: Update the fabric-block tests and add HSN-map tests (write the failing tests first)**

In `tests/test_mapper.py`, change `FABRIC_RULES` (remove the `HSN` keys):

```python
FABRIC_RULES = {
    "fabric_detection": {
        "order": ["cotton", "silk"],
        "cotton": {"Saree Fabric": "Pure Cotton", "Wash Care": "Hand Wash"},
        "silk": {"Saree Fabric": "Pure Silk", "Wash Care": "Dry Clean"},
    },
    "prominent_colour_from_name": True,
    "colour_scan_exclude": ["NA"],
}
```

In `test_cotton_fabric_block_and_colour_and_forced_brand`, delete the line
`assert row.cells["HSN"] == "52081120"               # name has 'cotton'`.

In `test_silk_fabric_block`, delete the line `assert row.cells["HSN"] == "50072010"`.

Add two new tests at the end of `tests/test_mapper.py`:

```python
def test_hsn_set_from_injected_map():
    p = Product(handle="h", sku="S1", title="Banarasi Saree", vendor="V", tags="",
                body_html="", price=1.0, compare_at_price=None, color=None,
                fabric="Pure Silk", size=None, status="active", images=[])
    consts = {"articleType": "Sarees"}
    hsn_map = {"saree|pure silk": "50072010"}
    row = map_product(p, _template_with_rules(), {}, consts, FABRIC_RULES,
                      hsn_by_signature=hsn_map)
    assert row.cells["HSN"] == "50072010"


def test_hsn_unresolved_signature_is_flagged_not_guessed():
    p = Product(handle="h", sku="S2", title="Plain Saree", vendor="V", tags="",
                body_html="", price=1.0, compare_at_price=None, color=None,
                fabric=None, size=None, status="active", images=[])
    consts = {"articleType": "Sarees"}
    row = map_product(p, _template_with_rules(), {}, consts, FABRIC_RULES,
                      hsn_by_signature={})   # nothing learned yet
    assert "HSN" not in row.cells
    assert any(f.field == "HSN" for f in row.flags)


def test_fabric_block_no_longer_sets_hsn():
    p = Product(handle="h", sku="S3", title="Lavender Pure Cotton Saree", vendor="V",
                tags="", body_html="", price=1.0, compare_at_price=None, color=None,
                fabric=None, size=None, status="active", images=[])
    row = map_product(p, _template_with_rules(), {}, {}, FABRIC_RULES)  # no map
    assert "HSN" not in row.cells
```

In `tests/test_config_loads.py`, change the HSN assertion in `test_rules_config`:

```python
    assert "HSN" not in r["fabric_detection"]["cotton"]
    assert "HSN" not in r["fabric_detection"]["silk"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_mapper.py tests/test_config_loads.py -q`
Expected: FAIL — new `test_hsn_set_from_injected_map` fails (`KeyError: 'HSN'` / `map_product` has no `hsn_by_signature` kwarg → `TypeError`), and `test_config_loads` fails because `rules.yaml` still has HSN in the fabric block.

- [ ] **Step 3: Drop HSN from `rules.yaml`**

In `config/myntra/rules.yaml`, edit the fabric blocks to remove the `HSN` lines and update the comment:

```yaml
# Fabric detection: first keyword found in the product name (then the Shopify
# fabric field) selects the fabric block. Each block fills Saree Fabric,
# Blouse Fabric, and Wash Care. (HSN is NO LONGER set here — it is learned per
# category|fabric signature by the HSN knowledge base; see src/myntra/hsn_kb.py.)
fabric_detection:
  order: [cotton, silk]   # check in this order; first match wins
  cotton:
    Saree Fabric: Pure Cotton
    Blouse Fabric: Pure Cotton
    Wash Care: Hand Wash
  silk:
    Saree Fabric: Pure Silk
    Blouse Fabric: Pure Silk
    Wash Care: Dry Clean
```

- [ ] **Step 4: Add the mapper import and the HSN step**

In `src/myntra/mapper.py`, add the import near the top (after `from src.core.models import ...`):

```python
from src.myntra.hsn_kb import signature
```

Change the `map_product` signature:

```python
def map_product(product, template, column_map, constants, rules=None, hsn_by_signature=None):
```

Immediately after the fabric-detection block (the `for keyword in (fabric_cfg.get("order") or []):` loop, which now ends without setting HSN), insert step 5b:

```python
    # 5b. HSN from the learned knowledge base, injected as a signature->hsn map.
    # HSN is mandatory in Myntra but absent from the Shopify export; it is learned
    # once per category|fabric signature via the Generate review screen. On the CLI
    # path (hsn_by_signature is None) HSN is left blank. When a map is injected but a
    # signature is unresolved, flag it rather than guess.
    if hsn_by_signature is not None:
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

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_mapper.py tests/test_config_loads.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/myntra/mapper.py config/myntra/rules.yaml tests/test_mapper.py tests/test_config_loads.py
git commit -m "feat(mapper): HSN from injected KB map; drop it from fabric block (A.5)"
```

---

### Task 4: Pipeline threading — pass `hsn_by_signature` into the mapper

**Files:**
- Modify: `src/myntra/pipeline.py:19` (signature), `:46` (map_product call)
- Test: `tests/test_pipeline_override.py` (add one test)

**Interfaces:**
- Consumes: `map_product(..., hsn_by_signature=...)` from Task 3.
- Produces: `main(..., hsn_by_signature=None)` — the injected map is forwarded to every `map_product` call. Default `None` preserves the CLI behavior (HSN blank).

- [ ] **Step 1: Read the existing test file for the harness pattern**

Run: `python -m pytest tests/test_pipeline_override.py -q` (confirm it currently passes, and open it to reuse its fixture/monkeypatch style for the new test).

- [ ] **Step 2: Write the failing test**

Add to `tests/test_pipeline_override.py` (reuse the module's existing helpers for building a temp CSV/template + patching image processing; the assertion is that an injected HSN lands in the filled cells). If the module has a helper that returns the mapped rows or reads the output, assert on it; otherwise assert via a direct `map_product` call threaded exactly as `main` does:

```python
def test_pipeline_threads_hsn_by_signature_into_mapper(monkeypatch):
    # main() forwards hsn_by_signature to map_product; verify the kwarg is passed
    # through unchanged for every product.
    import src.myntra.pipeline as pipeline
    seen = {}

    def fake_map(product, template, column_map, constants, rules=None,
                 hsn_by_signature=None):
        seen["hsn_by_signature"] = hsn_by_signature
        from src.core.models import MappedRow
        return MappedRow(sku=product.sku)

    monkeypatch.setattr(pipeline, "map_product", fake_map)
    monkeypatch.setattr(pipeline, "process_images",
                        lambda *a, **k: __import__("src.core.models", fromlist=["ImageResult"]).ImageResult(sku="s"))
    monkeypatch.setattr(pipeline, "read_products",
                        lambda path: [__import__("src.core.models", fromlist=["Product"]).Product(
                            handle="h", sku="S1", title="T", vendor="", tags="", body_html="",
                            price=1.0, compare_at_price=None, color=None, fabric="Pure Silk",
                            size=None, status="active", images=[])])
    monkeypatch.setattr(pipeline, "fill_template", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "write_report", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "read_template", lambda p: __import__(
        "src.core.models", fromlist=["TemplateInfo"]).TemplateInfo(
        headers=[], header_row=3, first_data_row=4, col_index_by_header={}, vocab_by_header={}))

    pipeline.main(csv_path="x.csv", out_dir=str(__import__("tempfile").mkdtemp()),
                  upload=False, hsn_by_signature={"saree|pure silk": "50072010"})
    assert seen["hsn_by_signature"] == {"saree|pure silk": "50072010"}
```

> If `tests/test_pipeline_override.py` already has cleaner fixtures for stubbing these collaborators, prefer them over the inline `monkeypatch.setattr` calls above — the assertion (`seen["hsn_by_signature"] == {...}`) is the point.

- [ ] **Step 3: Run the test to verify it fails**

Run: `python -m pytest tests/test_pipeline_override.py::test_pipeline_threads_hsn_by_signature_into_mapper -q`
Expected: FAIL — `TypeError: main() got an unexpected keyword argument 'hsn_by_signature'`.

- [ ] **Step 4: Thread the argument through `pipeline.main`**

In `src/myntra/pipeline.py`, change the signature:

```python
def main(template_path=None, csv_path=None, out_dir="output", config_dir="config/myntra",
         fetch=None, upload=None, style_group_id_start=None, hsn_by_signature=None):
```

And the `map_product` call inside the loop:

```python
        mapped = map_product(p, template, column_map, constants, rules,
                             hsn_by_signature=hsn_by_signature)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/test_pipeline_override.py -q`
Expected: PASS (new test green; existing override tests still green).

- [ ] **Step 6: Commit**

```bash
git add src/myntra/pipeline.py tests/test_pipeline_override.py
git commit -m "feat(pipeline): thread hsn_by_signature into the mapper (A.5)"
```

---

### Task 5: Generate flow — pre-scan → HSN review → build

**Files:**
- Create: `src/web/templates/_hsn_review.html`
- Modify: `src/web/routers/generate.py` (pre-scan, `_start_build` refactor, `_spawn`/`_run_generate` signatures, new `hsn_submit` route, imports)
- Modify: `tests/web/test_generate.py` (`_client` gains `hsn_local_path`; update the 3 flow tests to pass the HSN step; add new tests)

**Interfaces:**
- Consumes: `read_products`, `signature`, `read_kb`, `suggest`, `learn`, `hsn_store`, `reserve`, `ledger_store` (all existing/earlier tasks).
- Produces:
  - `POST /generate` now either returns `_hsn_review.html` (job `awaiting_hsn`, `hsn.json` persisted in the job dir) or, when there are zero signatures, builds directly (returns `_stepper.html`).
  - `POST /generate/hsn/{job_id}` (async) — validates 8-digit codes, `learn()`s each, then builds. Re-renders `_hsn_review.html` with `error` + `values` on invalid input.
  - `_start_build(request, job, csv_path, job_dir, count, settings, hsn_by_signature=None) -> Response` — reserves the ledger, spawns the build thread, returns the stepper with `x-job-id`.
  - `_hsn_review.html` context: `job_id`, `signatures` (list of `{"signature", "examples", "suggestions"}`), optional `values` (list[str]) and `error` (str).

- [ ] **Step 1: Create the review template**

Create `src/web/templates/_hsn_review.html`:

```html
<form hx-post="/generate/hsn/{{ job_id }}" hx-target="#progress" hx-swap="innerHTML">
  <h3>One-time HSN codes for this batch</h3>
  <p class="hint">HSN isn't in the Shopify export. Enter the 8-digit code for each
    fabric group once — the app remembers it for next time. Chips show codes used
    before; click one to fill the box.</p>
  {% if error %}<p class="flag mono">⚠ {{ error }}</p>{% endif %}
  {% for s in signatures %}
    <div class="card hsn-row">
      <strong class="mono">{{ s.signature }}</strong>
      <div class="hint">{{ s.examples | join(", ") }}</div>
      <input type="text" inputmode="numeric" pattern="[0-9]{8}" maxlength="8" required
             name="hsn__{{ loop.index0 }}"
             value="{{ values[loop.index0] if values else '' }}"
             placeholder="8-digit HSN">
      {% for sug in s.suggestions %}
        <button type="button" class="chip"
                onclick="this.closest('.hsn-row').querySelector('input').value='{{ sug.hsn }}'">
          {{ sug.hsn }}{% if sug.examples %} · {{ sug.examples | join(', ') }}{% endif %}
        </button>
      {% endfor %}
    </div>
  {% endfor %}
  <button class="btn" type="submit">Save HSN &amp; generate →</button>
</form>
```

- [ ] **Step 2: Rewrite the generate router (imports, pre-scan, `_start_build`, spawn, hsn route)**

In `src/web/routers/generate.py`, update the imports block:

```python
import csv as csvmod
import json
import os
import re
import shutil

import yaml
from fastapi import APIRouter, Request, UploadFile, File, HTTPException, Form
from fastapi.responses import FileResponse, HTMLResponse

from src.core.shopify_reader import read_products
from src.myntra.groupid_ledger import reserve, confirm, unconfirm, read_ledger
from src.myntra.hsn_kb import signature, read_kb, suggest, learn
from src.myntra.pipeline import main as pipeline_main  # noqa: F401 (patched in tests)
from src.web.jobs import store
from src.web.routers.pages import get_user, get_settings
from src.web.settings import ledger_store, hsn_store

router = APIRouter()
RUNTIME = os.path.join(os.path.dirname(os.path.dirname(__file__)), "runtime")
CONFIG_DIR = "config/myntra"


def _safe_job_id(job_id: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise HTTPException(status_code=404, detail="unknown job")
    return job_id


def _load_yaml(name):
    with open(os.path.join(CONFIG_DIR, name), encoding="utf-8") as fh:
        return yaml.safe_load(fh)
```

Replace `generate_submit` with the pre-scan version (keeps CSV save + count, then pre-scans):

```python
@router.post("/generate", response_class=HTMLResponse)
def generate_submit(request: Request, file: UploadFile = File(...)):
    get_user(request)
    settings = get_settings(request)
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a .csv file")

    job = store.create()
    job_dir = os.path.join(RUNTIME, job.id)
    os.makedirs(job_dir, exist_ok=True)
    csv_path = os.path.join(job_dir, "products_export.csv")
    with open(csv_path, "wb") as out:
        shutil.copyfileobj(file.file, out)

    count = count_products(csv_path)

    # Pre-scan: which category|fabric signatures does this batch contain, and what
    # does the KB already know? HSN is absent from the export, so we always ask.
    constants = _load_yaml("constants.yaml")
    rules = _load_yaml("rules.yaml")
    category = constants.get("articleType", "")
    fabric_keywords = (rules.get("fabric_detection") or {}).get("order") or []
    kb = read_kb(hsn_store(settings))
    grouped = {}
    for p in read_products(csv_path):
        grouped.setdefault(signature(p, category, fabric_keywords), []).append(p.title)

    if not grouped:                      # empty CSV / no products → nothing to ask
        return _start_build(request, job, csv_path, job_dir, count, settings)

    signatures = [{"signature": sig, "examples": names[:5], "suggestions": suggest(kb, sig)}
                  for sig, names in grouped.items()]
    with open(os.path.join(job_dir, "hsn.json"), "w", encoding="utf-8") as fh:
        json.dump({"csv_path": csv_path, "count": count, "signatures": signatures}, fh)
    job.status = "awaiting_hsn"

    resp = _templates().TemplateResponse(
        request, "_hsn_review.html", {"job_id": job.id, "signatures": signatures})
    resp.headers["x-job-id"] = job.id
    return resp
```

Add `_start_build` (shared reserve+spawn) just below `generate_submit`:

```python
def _start_build(request, job, csv_path, job_dir, count, settings, hsn_by_signature=None):
    start, batch_id = reserve(ledger_store(settings), count, "myntra_filled.xlsx")
    job.batch_id = batch_id
    job.range = [start, start + count - 1]
    job.status = "running"
    _spawn(job.id, csv_path, job_dir, start, settings, hsn_by_signature)
    resp = _templates().TemplateResponse(
        request, "_stepper.html", {"job": job, "count": count})
    resp.headers["x-job-id"] = job.id
    return resp
```

Update `_spawn` and `_run_generate` to carry the map:

```python
def _spawn(job_id, csv_path, job_dir, start, settings, hsn_by_signature=None):
    import threading
    threading.Thread(
        target=_run_generate,
        args=(job_id, csv_path, job_dir, start, settings, hsn_by_signature),
        daemon=True).start()


def _run_generate(job_id, csv_path, job_dir, start, settings, hsn_by_signature=None):
    try:
        store.set_step(job_id, "Ingest CSV", "active")
        res = pipeline_main(csv_path=csv_path, out_dir=job_dir,
                            style_group_id_start=start,
                            hsn_by_signature=hsn_by_signature)
        for name in ["Ingest CSV", "Map attributes", "Images → S3", "Fill & validate", "Ready"]:
            store.set_step(job_id, name, "done")
        store.set_step(job_id, "Images → S3", "done", count=res.get("uploaded"))
        store.finish(job_id, res)
    except Exception as exc:  # surface failure to the UI
        store.fail(job_id, f"{type(exc).__name__}: {exc}")
```

Add the HSN-submit route (place after `generate_submit`/`_start_build`, near the other POST routes):

```python
@router.post("/generate/hsn/{job_id}", response_class=HTMLResponse)
async def hsn_submit(request: Request, job_id: str):
    get_user(request)
    settings = get_settings(request)
    job_id = _safe_job_id(job_id)
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="unknown job")
    job_dir = os.path.join(RUNTIME, job_id)
    hsn_path = os.path.join(job_dir, "hsn.json")
    if not os.path.exists(hsn_path):
        raise HTTPException(status_code=404, detail="session expired, please re-upload")
    with open(hsn_path, encoding="utf-8") as fh:
        data = json.load(fh)
    signatures = data["signatures"]

    form = await request.form()
    values = [str(form.get(f"hsn__{i}", "")).strip() for i in range(len(signatures))]
    if any(not re.fullmatch(r"\d{8}", v) for v in values):
        return _templates().TemplateResponse(
            request, "_hsn_review.html",
            {"job_id": job_id, "signatures": signatures, "values": values,
             "error": "Each HSN must be exactly 8 digits."})

    hsn_by_signature = {}
    for i, s in enumerate(signatures):
        example = s["examples"][0] if s["examples"] else None
        learn(hsn_store(settings), s["signature"], values[i], example_name=example)
        hsn_by_signature[s["signature"]] = values[i]

    return _start_build(request, job, data["csv_path"], job_dir,
                        data["count"], settings, hsn_by_signature)
```

- [ ] **Step 3: Update the web test harness + the three existing flow tests**

In `tests/web/test_generate.py`, give `_client` an HSN path:

```python
def _client(tmp_path):
    s = Settings(auth_disabled=True, s3_bucket="b",
                 ledger_local_path=str(tmp_path / "led.json"),
                 hsn_local_path=str(tmp_path / "hsn.json"))
    return TestClient(create_app(s)), s
```

Add a small helper near the top of the file (after `_client`) that drives upload → HSN → ready, so the flow tests stay DRY:

```python
def _pass_hsn_and_wait(client, job_id, hsn="12345678"):
    """Submit the single-signature HSN review, then poll until the sheet is ready.
    The default test CSV (Handle,Title only) yields one signature: saree|unknown."""
    import time
    r = client.post(f"/generate/hsn/{job_id}", data={"hsn__0": hsn})
    poll = r
    for _ in range(20):
        if "Download" in poll.text:
            return poll
        time.sleep(0.05)
        poll = client.get(f"/jobs/{job_id}")
    return poll
```

Rewrite `test_generate_runs_job_and_confirm_advances_ledger` to go through the HSN step (the ledger assertions are unchanged — reservation now happens at build, still 3 IDs):

```python
def test_generate_runs_job_and_confirm_advances_ledger(tmp_path, monkeypatch):
    client, settings = _client(tmp_path)

    def fake_main(csv_path=None, out_dir=None, style_group_id_start=None, **kw):
        with open(f"{out_dir}/myntra_filled.xlsx", "wb") as fh:
            fh.write(b"xlsx-bytes")
        with open(f"{out_dir}/report.txt", "w") as fh:
            fh.write("3 rows\n1 vocab flag: Ivory\n")
        return {"filled": f"{out_dir}/myntra_filled.xlsx",
                "report": f"{out_dir}/report.txt", "products": 3, "uploaded": 9}

    monkeypatch.setattr(gen, "pipeline_main", fake_main)
    monkeypatch.setattr(gen, "count_products", lambda path: 3)

    csv = b"Handle,Title\na,A\nb,B\nc,C\n"
    r = client.post("/generate", files={"file": ("products_export.csv", csv, "text/csv")})
    assert "One-time HSN" in r.text                 # pre-scan paused for HSN
    job_id = r.headers["x-job-id"]

    ready = _pass_hsn_and_wait(client, job_id)
    assert "Download" in ready.text

    from src.myntra.groupid_ledger import read_ledger
    from src.web.settings import ledger_store
    rc = client.post(f"/generate/confirm/{job_id}")
    assert read_ledger(ledger_store(settings))["next_style_group_id"] == 4
```

Rewrite `test_result_screen_shows_verify_notice` to pass the HSN step:

```python
def test_result_screen_shows_verify_notice(tmp_path, monkeypatch):
    client, settings = _client(tmp_path)

    def fake_main(csv_path=None, out_dir=None, style_group_id_start=None, **kw):
        with open(f"{out_dir}/myntra_filled.xlsx", "wb") as fh:
            fh.write(b"xlsx-bytes")
        with open(f"{out_dir}/report.txt", "w") as fh:
            fh.write("3 rows\n")
        return {"filled": f"{out_dir}/myntra_filled.xlsx",
                "report": f"{out_dir}/report.txt", "products": 3, "uploaded": 9}

    monkeypatch.setattr(gen, "pipeline_main", fake_main)
    monkeypatch.setattr(gen, "count_products", lambda path: 3)

    csv = b"Handle,Title\na,A\nb,B\nc,C\n"
    r = client.post("/generate", files={"file": ("products_export.csv", csv, "text/csv")})
    ready = _pass_hsn_and_wait(client, r.headers["x-job-id"])
    assert "verify the downloaded file yourself" in ready.text.lower()
```

Rewrite `test_confirm_then_undo_rolls_ledger_back` to pass the HSN step:

```python
def test_confirm_then_undo_rolls_ledger_back(tmp_path, monkeypatch):
    client, settings = _client(tmp_path)

    def fake_main(csv_path=None, out_dir=None, style_group_id_start=None, **kw):
        with open(f"{out_dir}/myntra_filled.xlsx", "wb") as fh:
            fh.write(b"x")
        with open(f"{out_dir}/report.txt", "w") as fh:
            fh.write("r\n")
        return {"filled": f"{out_dir}/myntra_filled.xlsx",
                "report": f"{out_dir}/report.txt", "products": 3, "uploaded": 0}

    monkeypatch.setattr(gen, "pipeline_main", fake_main)
    monkeypatch.setattr(gen, "count_products", lambda path: 3)

    csv = b"Handle,Title\na,A\nb,B\nc,C\n"
    r = client.post("/generate", files={"file": ("products_export.csv", csv, "text/csv")})
    job_id = r.headers["x-job-id"]
    _pass_hsn_and_wait(client, job_id)

    from src.myntra.groupid_ledger import read_ledger
    from src.web.settings import ledger_store

    rc = client.post(f"/generate/confirm/{job_id}")
    assert "Undo" in rc.text
    assert read_ledger(ledger_store(settings))["next_style_group_id"] == 4

    ru = client.post(f"/generate/unconfirm/{job_id}")
    assert "Mark upload successful" in ru.text
    assert read_ledger(ledger_store(settings))["next_style_group_id"] == 1
```

- [ ] **Step 4: Add new HSN-flow tests**

Append to `tests/web/test_generate.py`:

```python
def test_hsn_review_lists_signature_and_learns_on_submit(tmp_path, monkeypatch):
    client, settings = _client(tmp_path)

    def fake_main(csv_path=None, out_dir=None, style_group_id_start=None,
                  hsn_by_signature=None, **kw):
        # the learned map reaches the pipeline
        assert hsn_by_signature == {"saree|unknown": "63079090"}
        with open(f"{out_dir}/myntra_filled.xlsx", "wb") as fh:
            fh.write(b"x")
        with open(f"{out_dir}/report.txt", "w") as fh:
            fh.write("r\n")
        return {"filled": f"{out_dir}/myntra_filled.xlsx",
                "report": f"{out_dir}/report.txt", "products": 1, "uploaded": 0}

    monkeypatch.setattr(gen, "pipeline_main", fake_main)
    monkeypatch.setattr(gen, "count_products", lambda path: 1)

    csv = b"Handle,Title\na,Plain Saree\n"
    r = client.post("/generate", files={"file": ("products_export.csv", csv, "text/csv")})
    assert "saree|unknown" in r.text
    job_id = r.headers["x-job-id"]

    ready = _pass_hsn_and_wait(client, job_id, hsn="63079090")
    assert "Download" in ready.text

    from src.myntra.hsn_kb import read_kb, suggest
    from src.web.settings import hsn_store
    kb = read_kb(hsn_store(settings))
    assert suggest(kb, "saree|unknown")[0]["hsn"] == "63079090"


def test_hsn_invalid_code_rerenders_with_error(tmp_path, monkeypatch):
    client, settings = _client(tmp_path)
    monkeypatch.setattr(gen, "count_products", lambda path: 1)

    csv = b"Handle,Title\na,Plain Saree\n"
    r = client.post("/generate", files={"file": ("products_export.csv", csv, "text/csv")})
    job_id = r.headers["x-job-id"]

    bad = client.post(f"/generate/hsn/{job_id}", data={"hsn__0": "123"})   # not 8 digits
    assert "exactly 8 digits" in bad.text
    assert 'value="123"' in bad.text                    # entered value preserved
    from src.myntra.groupid_ledger import read_ledger
    from src.web.settings import ledger_store
    assert read_ledger(ledger_store(settings))["next_style_group_id"] == 1  # not built


def test_generate_form_still_renders(tmp_path):
    client, _ = _client(tmp_path)
    assert client.get("/generate").status_code == 200
```

- [ ] **Step 5: Run the web suite to verify it passes**

Run: `python -m pytest tests/web/test_generate.py -q`
Expected: PASS (3 rewritten flow tests + 3 new + the non-csv rejection test).

- [ ] **Step 6: Commit**

```bash
git add src/web/routers/generate.py src/web/templates/_hsn_review.html tests/web/test_generate.py
git commit -m "feat(web): pre-scan HSN review inside Generate — gather then ask (A.4)"
```

---

### Task 6: Full-suite verification + local smoke

**Files:** none (verification only).

- [ ] **Step 1: Run the entire suite**

Run: `python -m pytest -q`
Expected: all green (the Part-B suite + every new/updated test above). Note the count.

- [ ] **Step 2: Local smoke of the HSN flow**

Run (from repo root):

```bash
AUTH_DISABLED=1 \
LEDGER_LOCAL_PATH="$TMPDIR/ledger.json" \
HSN_LOCAL_PATH="$TMPDIR/hsn_kb.json" \
  python -m uvicorn src.web.main:app --port 8000
```

Open http://localhost:8000/generate, upload a real `products_export.csv`, and confirm:
- the flow **pauses on a review screen** listing each `category|fabric` signature with an empty 8-digit box;
- a **silk/cotton** signature shows a **suggestion chip** (`50072010` / `52081120`) that fills the box when clicked;
- entering a non-8-digit code and submitting **re-renders the review with an error** and keeps typed values;
- submitting valid codes runs the build and reaches the **Download + verify-notice** result;
- re-uploading the same CSV now shows the just-entered codes as **chips** (KB persisted to `$TMPDIR/hsn_kb.json`).

- [ ] **Step 3: Stop the server.** No merge to `main` (branch stays `feat/hsn-knowledge-base`).

- [ ] **Step 4: Update the in-repo docs** if a module/layer map is affected: add `src/myntra/hsn_kb.py` to `docs/ARCHITECTURE.md` / `AGENTS.md` state-store list next to `groupid_ledger.py`, and note `state/hsn_kb.json` in `docs/infra-resources.md` alongside the ledger key. Commit:

```bash
git add docs/ARCHITECTURE.md AGENTS.md docs/infra-resources.md
git commit -m "docs: record HSN knowledge base module + state key"
```

---

## Self-Review

**Spec coverage (against `docs/superpowers/specs/2026-07-02-hsn-kb-and-app-fixes-design.md`, Part A):**
- A.1 core model (learn once per signature, KB is source of truth, multiple codes per signature) → Task 1 (`SEED`, `learn` appends new codes, `suggest`). ✔
- A.2 storage mirroring the ledger, separate local path, `hsn_store`, seed → Task 1 (`HSN_KEY`, `read_kb` seeds) + Task 2 (`hsn_store`, `HSN_LOCAL_PATH`, `Settings.hsn_local_path`). ✔
- A.3 shared `signature()` (metafield → title fallback → unknown; normalization) → Task 1, imported by mapper (Task 3) and the router (Task 5). ✔
- A.4 pause inside Generate (pre-scan → `awaiting_hsn` + persisted state → review screen with empty inputs + suggestion chips → validate/learn/build; skip when zero signatures) → Task 5. ✔
- A.5 mapper integration (drop HSN from fabric block; set from injected map; flag unresolved; `HSN` already in `NUMERIC_HEADERS`) → Task 3 + Task 4 (thread through pipeline). ✔
- A.6 edge cases (8-digit validation re-render keeping values; overriding a suggestion learns an additional code; unknown fabric asked+learned; single-user, no locking) → Task 5 (`hsn_submit` re-render) + Task 1 (`learn` multi-code). ✔
- Testing section → per-task TDD covers signature normalization, seeding, suggest ordering, learn upsert, HSN-from-map + unresolved flag, pre-scan→review→learn→build, invalid-code re-render. ✔

**Known, intentional behavior change (documented for the executor):** the **CLI** path (`python run.py` → `pipeline.main` with `hsn_by_signature=None`) no longer fills HSN from the fabric block — HSN is now learned/asked via the web review, per the "always ask" invariant. CLI-built sheets leave HSN blank for manual fill. If CLI KB auto-fill is wanted later, that is a follow-up (out of scope here, matching the SKU-dedup spec's "CLI is low priority").

**Seed vs. title-fallback signature nuance (documented):** a product with the fabric **metafield** "Pure Cotton"/"Pure Silk" yields `saree|pure cotton`/`saree|pure silk` and matches the seed chips. A product with a **blank** metafield whose title only contains the keyword "cotton"/"silk" yields `saree|cotton`/`saree|silk` (no seed chip the first time) and is learned on submit. This is the intended "learn once per signature" behavior, not a bug.

**Placeholder scan:** every code step shows the actual file content and an exact `pytest` command with expected output; the only soft note is Task 4 Step 2 ("prefer the module's existing fixtures") — the concrete assertion is still spelled out.

**Type consistency:** `signature(product, category, fabric_keywords=())`, `read_kb(store, key)`, `suggest(kb, sig)`, `learn(store, sig, hsn, example_name, key)` are defined in Task 1 and consumed with identical signatures in Tasks 3 and 5. `hsn_store(settings)` (Task 2) is called identically in Task 5. `map_product(..., hsn_by_signature=None)` (Task 3) matches the `pipeline.main` call (Task 4) and the `fake_main` kwarg in the Task 5 tests. The persisted `hsn.json` keys (`csv_path`, `count`, `signatures`) written in `generate_submit` match those read in `hsn_submit`. `_hsn_review.html` context keys (`job_id`, `signatures`, `values`, `error`) match both render sites.
