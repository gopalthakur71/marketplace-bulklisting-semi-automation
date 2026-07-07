# Fix-Error Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `/fix` flow read all three real Myntra error/report formats, explain every error in plain English (YAML → self-learning dictionary → explain-only Gemini → raw fallback), correct only instant-text errors behind a human gate, and drop a durable correction-log breadcrumb.

**Architecture:** New reader (`error_sources.py`) fingerprints the uploaded file and emits a normalized `ErrorItem` list. An `explainer.py` turns each item into an `ExplainedIssue` using a strict lookup order; a `signature.py` normalizer keys a JSON `explanation_store.py` so Gemini is called at most once per error type ever. Corrections **reuse** the existing deterministic `corrector.correct()` (Surface A: correct-in-place) or re-run `pipeline.main()` (Surface B: rebuild rejected SKUs from the SKU registry + Shopify export). Every corrected SKU appends a record to an append-only `correction_log.py`.

**Tech Stack:** Python 3.12, FastAPI + htmx + Jinja2, openpyxl, PyYAML, pytest. New dep: `google-generativeai` (lazy-imported, mocked in tests). All JSON state uses the existing `LocalJsonStore`/`S3JsonStore` (`get_json`/`put_json`) abstraction from `src/web/settings.py`.

## Global Constraints

- **The LLM explains; it never fixes or guesses.** Gemini output is display text only — it can never change the corrected file or supply a value.
- **Auto-fixes come only from human-authored `config/myntra/error_rules.yaml` rules.** Learned-store and Gemini entries are always `action: explain_only`.
- **Code flags, human decides.** Every correction sits behind the Proceed / Do-not-change gate.
- **The app fixes only what a user can provide instantly as text** (brand, pincode, address, colour, price). Image/quality/cropping problems are explained, never auto-corrected.
- **Privacy:** only the error text (`STATUS` + `SYSTEM ERROR MESSAGE`, normalized to a signature) ever leaves the machine. Never send the product row — manufacturer/packer name, address, pincode live in other columns and must not travel.
- **Reuse the existing store abstraction** (`LocalJsonStore.get_json/put_json`, non-atomic). Do NOT build custom atomic writes — the ledger/hsn/registry stores are all non-atomic; consistency with them wins. (Deliberate deviation from spec §7 "atomic writes.")
- **Store the NORMALIZED SIGNATURE, not the raw message**, and store a plain explanation (NOT a `{NUM}`-interpolated template). Show the raw Myntra message on the review card for the specifics. (Deliberate deviation from spec §5's template interpolation — dropped.)
- **Dropdown-controlled values must match the Myntra template's exact vocabulary spelling** — the existing `correct()` already canonicalizes via `validate_value`; do not bypass it.
- **Keep existing public interfaces intact:** `corrector.correct()`, `corrector.plan_corrections()`, `error_reader.read_errors()`, `error_reader.classify()`, `error_reader.load_rules()`, `error_reader.RowError`. Their tests (`tests/test_corrector.py`, `tests/test_error_reader.py`) must stay green.
- **Tests never call the live Gemini API** — the client is always injected/mocked.
- **Pytest runs from the repo root** (`python -m pytest -q`); `tests/conftest.py` puts the repo root on `sys.path`, so imports are `from src.myntra... import ...`.

---

## File Structure

**New source files**
- `src/myntra/signature.py` — error clause → normalized signature (+ captured values).
- `src/myntra/explanation_store.py` — JSON learned dictionary (read / get / learn), keyed on signature.
- `src/myntra/gemini_client.py` — explain-only LLM call, injectable client, retry+fallback.
- `src/myntra/explainer.py` — `ExplainedIssue` dataclass + `explain_item()` orchestrating YAML → learned → Gemini → raw.
- `src/myntra/error_sources.py` — `ErrorItem` dataclass + `detect_format()` + `read_error_file()` (3 readers).
- `src/myntra/correction_log.py` — append-only Phase-D breadcrumb.

**Extended source files**
- `src/web/settings.py` — new `Settings` fields, `GEMINI_API_KEY` in `_FIELDS`, env wiring, `explanation_store()` + `correction_log_store()` factories.
- `src/myntra/corrector.py` — brand/address auto-fix categories; `correct_from_issues()` (Surface A wrapper + correction log); `regenerate_surface_b()` (Surface B resolver).
- `src/web/routers/fix.py` — accept `.csv`, detect format, explain, route surfaces, render.
- `config/myntra/error_rules.yaml` — new curated rules.
- `src/web/templates/fix.html`, `_fix_review.html`, `_fix_result.html` — two groups + gate + `.csv`.
- `requirements.txt` — add `google-generativeai`.

**New test files** (one per module, mirroring `tests/test_error_reader.py` style)
- `tests/test_signature.py`, `tests/test_explanation_store.py`, `tests/test_gemini_client.py`, `tests/test_explainer.py`, `tests/test_error_sources.py`, `tests/test_correction_log.py`, plus additions to `tests/test_corrector.py` and a rewrite of `tests/web/test_fix.py`.

---

## Task 1: Config, dependency & store factories

**Files:**
- Modify: `src/web/settings.py`
- Modify: `requirements.txt`
- Test: `tests/web/test_settings.py` (add cases)

**Interfaces:**
- Consumes: existing `Settings`, `load_settings`, `LocalJsonStore`, `_FIELDS`.
- Produces: `Settings.gemini_api_key: str`, `Settings.gemini_model: str`, `Settings.explain_with_gemini: bool`, `Settings.explanation_store_path: str | None`, `Settings.correction_log_path: str | None`; factories `explanation_store(settings) -> store`, `correction_log_store(settings) -> store`.

- [ ] **Step 1: Write the failing test**

Add to `tests/web/test_settings.py` (create the file if it does not exist, with `from src.web.settings import load_settings, explanation_store, correction_log_store, LocalJsonStore`):

```python
def test_gemini_and_store_paths_from_env():
    env = {
        "GEMINI_API_KEY": "k-123",
        "GEMINI_MODEL": "gemini-2.5-flash",
        "EXPLAIN_WITH_GEMINI": "1",
        "EXPLANATION_STORE_PATH": "/tmp/expl.json",
        "CORRECTION_LOG_PATH": "/tmp/corr.json",
    }
    s = load_settings(env=env, ssm=lambda name: None)
    assert s.gemini_api_key == "k-123"
    assert s.gemini_model == "gemini-2.5-flash"
    assert s.explain_with_gemini is True
    assert isinstance(explanation_store(s), LocalJsonStore)
    assert explanation_store(s).path == "/tmp/expl.json"
    assert isinstance(correction_log_store(s), LocalJsonStore)


def test_gemini_defaults_off():
    s = load_settings(env={}, ssm=lambda name: None)
    assert s.explain_with_gemini is False
    assert s.gemini_model == "gemini-2.5-flash"
    assert s.gemini_api_key == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/web/test_settings.py::test_gemini_and_store_paths_from_env -q`
Expected: FAIL (`AttributeError: 'Settings' object has no attribute 'gemini_api_key'` or `ImportError`).

- [ ] **Step 3: Add the dependency**

In `requirements.txt`, under `# ── Direct dependencies ──` (keep alphabetical-ish with the others), add:

```
google-generativeai==0.8.5
```

Then install locally so the app (not the tests) can use it: `pip install google-generativeai==0.8.5`. Tests mock the client, so a missing install never fails the suite. (If the resolver pins a different compatible version, use that exact version instead and record it here.)

- [ ] **Step 4: Extend `Settings` and `_FIELDS`**

In `src/web/settings.py`, add `GEMINI_API_KEY` to `_FIELDS` (so it resolves from env or SSM like the Cognito secret):

```python
_FIELDS = [
    ("S3_BUCKET", "s3_bucket"),
    ("S3_REGION", "s3_region"),
    ("S3_PREFIX", "s3_prefix"),
    ("COGNITO_POOL_ID", "cognito_pool_id"),
    ("COGNITO_CLIENT_ID", "cognito_client_id"),
    ("COGNITO_DOMAIN", "cognito_domain"),
    ("COGNITO_REDIRECT_URI", "cognito_redirect_uri"),
    ("COGNITO_CLIENT_SECRET", "cognito_client_secret"),
    ("GEMINI_API_KEY", "gemini_api_key"),
]
```

Add fields to the `Settings` dataclass (after `sku_registry_local_path`):

```python
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    explain_with_gemini: bool = False
    explanation_store_path: str | None = None
    correction_log_path: str | None = None
```

- [ ] **Step 5: Wire env-only fields in `load_settings`**

In `load_settings`, alongside the other `env.get(...)` lines (after `s.sku_registry_local_path = ...`):

```python
    s.gemini_model = env.get("GEMINI_MODEL") or "gemini-2.5-flash"
    s.explain_with_gemini = env.get("EXPLAIN_WITH_GEMINI", "") in ("1", "true", "True")
    s.explanation_store_path = env.get("EXPLANATION_STORE_PATH") or None
    s.correction_log_path = env.get("CORRECTION_LOG_PATH") or None
```

(`gemini_api_key` is resolved by the `_FIELDS` loop below — env first, then SSM.)

- [ ] **Step 6: Add the two store factories**

Append to `src/web/settings.py` (mirroring `sku_registry_store`):

```python
def explanation_store(settings: Settings):
    """Store for the self-learning error-explanation dictionary. Own local path
    (LocalJsonStore is one-file-per-path); S3 fallback in prod."""
    if settings.explanation_store_path:
        return LocalJsonStore(settings.explanation_store_path)
    import boto3
    from src.myntra.groupid_ledger import S3JsonStore
    return S3JsonStore(settings.s3_bucket, boto3.client("s3", region_name=settings.s3_region))


def correction_log_store(settings: Settings):
    """Store for the append-only correction log (Phase-D breadcrumb). Own local path."""
    if settings.correction_log_path:
        return LocalJsonStore(settings.correction_log_path)
    import boto3
    from src.myntra.groupid_ledger import S3JsonStore
    return S3JsonStore(settings.s3_bucket, boto3.client("s3", region_name=settings.s3_region))
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `python -m pytest tests/web/test_settings.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/web/settings.py requirements.txt tests/web/test_settings.py
git commit -m "feat(config): Gemini + explanation/correction-log store settings"
```

---

## Task 2: Signature normalizer (`signature.py`)

**Files:**
- Create: `src/myntra/signature.py`
- Test: `tests/test_signature.py`

**Interfaces:**
- Produces: `normalize(clause: str) -> tuple[str, dict]` — returns `(signature, captured)` where `signature` is lowercase with digit runs → `<num>`, SKU/article codes → `<sku>`, URLs → `<url>`, whitespace collapsed; `captured` maps `"URL"|"SKU"|"NUM"` to the list of stripped literals (kept for future template use; not used by the store).

- [ ] **Step 1: Write the failing test**

Create `tests/test_signature.py`:

```python
from src.myntra.signature import normalize


def test_same_error_different_skus_one_signature():
    a, _ = normalize("Seller Sku Code 169SDE326SFSF is already registered for seller 87065")
    b, _ = normalize("Seller Sku Code 165SDE226RSG is already registered for seller 87065")
    assert a == b
    assert a == "seller sku code <sku> is already registered for seller <num>"


def test_different_errors_stay_distinct():
    a, _ = normalize("Seller Sku Code X9A8B7 is already registered")
    b, _ = normalize("HSN given 52081120 does not match present 50072010")
    assert a != b


def test_captures_stripped_values():
    _, cap = normalize("style id 43427259 image https://x.com/a.jpg sku 127SDE826NSB")
    assert "43427259" in cap["NUM"]
    assert "https://x.com/a.jpg" in cap["URL"]
    assert "127SDE826NSB" in cap["SKU"]


def test_letters_only_words_are_kept():
    sig, _ = normalize("getBrandCodeFromBrandName returned null key")
    assert "getbrandcodefrombrandname" in sig
    assert "<sku>" not in sig  # no digits -> not treated as a code
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_signature.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'src.myntra.signature'`).

- [ ] **Step 3: Write the implementation**

Create `src/myntra/signature.py`:

```python
import re

_URL = re.compile(r"https?://\S+")
# An alphanumeric token >= 6 chars containing BOTH a letter and a digit = a Myntra
# SKU/article code (e.g. 169SDE326SFSF). Pure-digit ids fall through to <NUM>;
# pure-letter words (getBrandCodeFromBrandName) are kept as-is.
_SKU = re.compile(r"\b(?=[A-Za-z0-9]*[A-Za-z])(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{6,}\b")
_NUM = re.compile(r"\d+")
_WS = re.compile(r"\s+")


def normalize(clause):
    """Return (signature, captured). Strip only obvious variable data (URLs, SKU
    codes, digit runs); keep every English word; lowercase; collapse whitespace."""
    text = str(clause or "")
    captured = {"URL": [], "SKU": [], "NUM": []}

    def _cap(tag):
        def _sub(m):
            captured[tag].append(m.group(0))
            return "<%s>" % tag
        return _sub

    text = _URL.sub(_cap("URL"), text)
    text = _SKU.sub(_cap("SKU"), text)
    text = _NUM.sub(_cap("NUM"), text)
    text = _WS.sub(" ", text).strip().lower()
    return text, captured
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_signature.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/myntra/signature.py tests/test_signature.py
git commit -m "feat(myntra): error-clause signature normalizer"
```

---

## Task 3: Learned explanation store (`explanation_store.py`)

**Files:**
- Create: `src/myntra/explanation_store.py`
- Test: `tests/test_explanation_store.py`

**Interfaces:**
- Consumes: a store object with `get_json(key)`/`put_json(key, data)` (Task 1's `LocalJsonStore`).
- Produces:
  - `read_store(store, key=STORE_KEY) -> dict` — `{}` on absent/corrupt.
  - `get(store, signature, key=STORE_KEY) -> dict | None` — the entry `{explanation, category, count, first_seen}` or `None`.
  - `learn(store, signature, explanation, category=None, key=STORE_KEY) -> dict` — upsert; bump `count` if present, else create; returns the full store dict.
  - constant `STORE_KEY = "state/error_explanations.json"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_explanation_store.py`:

```python
import json
from src.myntra.explanation_store import read_store, get, learn
from src.web.settings import LocalJsonStore


def _store(tmp_path):
    return LocalJsonStore(str(tmp_path / "expl.json"))


def test_learn_then_get(tmp_path):
    st = _store(tmp_path)
    assert get(st, "sig one") is None
    learn(st, "sig one", "This means the SKU is already live.", category="duplicate")
    entry = get(st, "sig one")
    assert entry["explanation"] == "This means the SKU is already live."
    assert entry["category"] == "duplicate"
    assert entry["count"] == 1
    assert entry["first_seen"]


def test_learn_twice_bumps_count(tmp_path):
    st = _store(tmp_path)
    learn(st, "s", "e")
    learn(st, "s", "e (ignored second time)")
    entry = get(st, "s")
    assert entry["count"] == 2
    assert entry["explanation"] == "e"  # first good explanation is frozen


def test_corrupt_file_treated_as_empty(tmp_path):
    p = tmp_path / "expl.json"
    p.write_text("{ this is not json", encoding="utf-8")
    st = LocalJsonStore(str(p))
    assert read_store(st) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_explanation_store.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write the implementation**

Create `src/myntra/explanation_store.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_explanation_store.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/myntra/explanation_store.py tests/test_explanation_store.py
git commit -m "feat(myntra): self-learning explanation store keyed on signature"
```

---

## Task 4: Gemini client (`gemini_client.py`)

**Files:**
- Create: `src/myntra/gemini_client.py`
- Test: `tests/test_gemini_client.py`

**Interfaces:**
- Produces:
  - `DEFAULT_MODEL = "gemini-2.5-flash"`
  - `explain(error_text, *, api_key=None, model=DEFAULT_MODEL, client=None, retries=1) -> str | None`
  - `client` is an injectable callable `(prompt: str) -> str`. When `None` and `api_key` is set, a lazy real client is built. Returns stripped text, or `None` on any failure / empty result.

- [ ] **Step 1: Write the failing test**

Create `tests/test_gemini_client.py`:

```python
from src.myntra.gemini_client import explain


def test_payload_contains_only_error_text_no_product_data():
    seen = {}

    def fake_client(prompt):
        seen["prompt"] = prompt
        return "This is a plain explanation."

    text = explain("HSN 52081120 does not match present 50072010",
                   client=fake_client)
    assert text == "This is a plain explanation."
    # The prompt must carry the error text but never a manufacturer/packer/address.
    assert "52081120" in seen["prompt"]
    assert "address" not in seen["prompt"].lower()
    assert "pincode" not in seen["prompt"].lower()


def test_returns_none_without_key_or_client():
    assert explain("anything", api_key=None, client=None) is None


def test_retries_then_falls_back_to_none():
    calls = {"n": 0}

    def flaky(prompt):
        calls["n"] += 1
        raise RuntimeError("boom")

    assert explain("x", client=flaky, retries=1) is None
    assert calls["n"] == 2  # initial try + 1 retry
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_gemini_client.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write the implementation**

Create `src/myntra/gemini_client.py`:

```python
import logging

_log = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-2.5-flash"

# Explain-only. The model must never propose or invent a fix or a value.
PROMPT = (
    "You are helping an online seller understand a Myntra catalog upload rejection. "
    "Explain the following rejection message in one or two plain-English sentences. "
    "Do NOT suggest, invent, or guess any fix, code, or value — only explain what it means.\n\n"
    "Rejection message: {text}"
)


def _default_client(api_key, model):
    """Build a thin callable around google-generativeai. Imported lazily so the
    dependency is optional and tests (which always inject a client) never load it."""
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    gm = genai.GenerativeModel(model)

    def _call(prompt):
        return gm.generate_content(prompt).text

    return _call


def explain(error_text, *, api_key=None, model=DEFAULT_MODEL, client=None, retries=1):
    """Return a plain-English explanation, or None on any failure. Sends ONLY the
    error text — never the product row (privacy, spec §5)."""
    if client is None:
        if not api_key:
            return None
        try:
            client = _default_client(api_key, model)
        except Exception as exc:
            _log.warning("Gemini client init failed: %s", exc)
            return None
    prompt = PROMPT.format(text=error_text)
    for attempt in range(retries + 1):
        try:
            out = client(prompt)
            out = (out or "").strip()
            return out or None
        except Exception as exc:
            _log.warning("Gemini explain failed (attempt %d): %s", attempt + 1, exc)
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_gemini_client.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/myntra/gemini_client.py tests/test_gemini_client.py
git commit -m "feat(myntra): explain-only Gemini client (injectable, mockable)"
```

---

## Task 5: Explainer (`explainer.py`)

**Files:**
- Create: `src/myntra/explainer.py`
- Test: `tests/test_explainer.py`

**Interfaces:**
- Consumes: `error_reader.classify` semantics (re-implemented as `match_rule`); `signature.normalize`; `explanation_store.get`/`learn`; `gemini_client.explain`.
- Produces:
  - `ExplainedIssue` dataclass: `sku: str|None, style_id: str|None, scope: str, source_type: str, raw_reason: str, explanation: str, action: str, field: str|None, category: str|None, source: str, cells: dict|None`.
  - `match_rule(message, rules) -> dict | None` — `{category, action, explanation, field}` on a YAML substring hit, else `None`.
  - `explain_item(item, rules, store=None, gemini=None) -> ExplainedIssue` — `item` is Task 6's `ErrorItem`; `gemini` is `{"enabled": bool, "api_key": str, "model": str, "client": callable|None}` or `None`.

Lookup order (first hit wins): YAML rule → (listings_report reasons pass through as already-plain) → learned store → Gemini (then learn) → raw fallback. `action` is `explain_only` for everything except YAML hits, which carry their authored `action`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_explainer.py`:

```python
from dataclasses import dataclass
from src.myntra.explainer import explain_item, match_rule, ExplainedIssue
from src.web.settings import LocalJsonStore
from src.myntra.explanation_store import get as store_get


@dataclass
class _Item:
    sku: str | None = "S1"
    style_id: str | None = None
    source_type: str = "sku_xlsx"
    scope: str = "sku"
    raw_reason: str = ""
    cells: dict | None = None


RULES = {
    "rules": [
        {"match": "already registered", "category": "duplicate",
         "action": "drop_sku", "explanation": "Already live on Myntra."},
    ],
    "unknown": {"category": "unknown", "action": "explain_only",
                "explanation": "Unrecognised error."},
}


def test_yaml_hit_wins_and_carries_action():
    it = _Item(raw_reason="Seller Sku Code X is already registered")
    out = explain_item(it, RULES)
    assert out.source == "yaml"
    assert out.action == "drop_sku"
    assert out.explanation == "Already live on Myntra."


def test_listings_report_reason_passes_through():
    it = _Item(source_type="listings_report",
               raw_reason="Product image is a flat shot; please reshoot on a model")
    out = explain_item(it, RULES)
    assert out.source == "plain"
    assert out.action == "explain_only"
    assert out.explanation == it.raw_reason


def test_learned_store_used_before_gemini(tmp_path):
    from src.myntra.explanation_store import learn
    st = LocalJsonStore(str(tmp_path / "e.json"))
    it = _Item(raw_reason="HSN given 111 does not match present 222")
    from src.myntra.signature import normalize
    learn(st, normalize(it.raw_reason)[0], "Learned explanation.")
    called = {"gemini": False}
    gem = {"enabled": True, "api_key": "k", "model": "m",
           "client": lambda p: called.__setitem__("gemini", True) or "SHOULD NOT RUN"}
    out = explain_item(it, RULES, store=st, gemini=gem)
    assert out.source == "learned"
    assert out.explanation == "Learned explanation."
    assert called["gemini"] is False


def test_gemini_explains_then_writes_store(tmp_path):
    st = LocalJsonStore(str(tmp_path / "e.json"))
    it = _Item(raw_reason="Some brand new cryptic wording 999")
    gem = {"enabled": True, "api_key": "k", "model": "m",
           "client": lambda p: "Gemini plain text."}
    out = explain_item(it, RULES, store=st, gemini=gem)
    assert out.source == "gemini"
    assert out.action == "explain_only"
    from src.myntra.signature import normalize
    assert store_get(st, normalize(it.raw_reason)[0])["explanation"] == "Gemini plain text."


def test_raw_fallback_when_gemini_off(tmp_path):
    st = LocalJsonStore(str(tmp_path / "e.json"))
    it = _Item(raw_reason="Totally unseen error text 42")
    out = explain_item(it, RULES, store=st, gemini={"enabled": False})
    assert out.source == "raw"
    assert out.explanation == it.raw_reason
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_explainer.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write the implementation**

Create `src/myntra/explainer.py`:

```python
from dataclasses import dataclass

from src.myntra.signature import normalize
from src.myntra.explanation_store import get as store_get, learn as store_learn
from src.myntra.gemini_client import explain as gemini_explain


@dataclass
class ExplainedIssue:
    sku: str | None
    style_id: str | None
    scope: str
    source_type: str
    raw_reason: str
    explanation: str
    action: str
    field: str | None
    category: str | None
    source: str          # yaml | plain | learned | gemini | raw
    cells: dict | None


def match_rule(message, rules):
    """First YAML rule whose `match` substring is in the message, else None. Mirrors
    error_reader.classify() but reports a miss so the caller can fall through."""
    low = str(message or "").strip().lower()
    for rule in rules.get("rules", []):
        if str(rule["match"]).lower() in low:
            return {"category": rule["category"], "action": rule["action"],
                    "explanation": rule["explanation"], "field": rule.get("field")}
    return None


def _issue(item, explanation, action, source, field=None, category=None):
    return ExplainedIssue(
        sku=item.sku, style_id=item.style_id, scope=item.scope,
        source_type=item.source_type, raw_reason=item.raw_reason,
        explanation=explanation, action=action, field=field,
        category=category, source=source, cells=item.cells)


def explain_item(item, rules, store=None, gemini=None):
    """Turn one ErrorItem into an ExplainedIssue. Lookup order (spec §5):
    YAML rule (only source of auto-fix) -> plain pass-through for Listings Report ->
    learned store -> Gemini (explain-only, then learn) -> raw fallback."""
    raw = item.raw_reason
    m = match_rule(raw, rules)
    if m:
        return _issue(item, m["explanation"], m["action"], "yaml",
                      field=m["field"], category=m["category"])

    # Listings-Report reasons are already plain English -> never send to Gemini.
    if item.source_type == "listings_report":
        return _issue(item, raw, "explain_only", "plain")

    sig, _ = normalize(raw)
    if store is not None:
        entry = store_get(store, sig)
        if entry:
            return _issue(item, entry["explanation"], "explain_only", "learned",
                          category=entry.get("category"))

    if gemini and gemini.get("enabled"):
        text = gemini_explain(raw, api_key=gemini.get("api_key"),
                              model=gemini.get("model", "gemini-2.5-flash"),
                              client=gemini.get("client"))
        if text:
            if store is not None:
                store_learn(store, sig, text)
            return _issue(item, text, "explain_only", "gemini")

    return _issue(item, raw, "explain_only", "raw")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_explainer.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/myntra/explainer.py tests/test_explainer.py
git commit -m "feat(myntra): explainer with YAML>learned>Gemini>raw lookup order"
```

---

## Task 6: Multi-format reader (`error_sources.py`)

**Files:**
- Create: `src/myntra/error_sources.py`
- Test: `tests/test_error_sources.py`

**Interfaces:**
- Consumes: `error_reader.read_errors`, `error_reader.load_rules`.
- Produces:
  - `ErrorItem` dataclass: `sku: str|None, style_id: str|None, source_type: str, scope: str, raw_reason: str, cells: dict|None`.
  - `detect_format(path) -> tuple[str|None, str]` — `source_type` is `"sku_xlsx"|"sheet_csv"|"listings_report"` or `None` (unknown), plus a user-facing `reason` string (empty when detected).
  - `read_error_file(path, rules=None) -> list[ErrorItem]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_error_sources.py`:

```python
import csv
import openpyxl
from src.myntra.error_sources import detect_format, read_error_file, ErrorItem
from src.myntra.error_reader import load_rules


def _sku_xlsx(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sarees"
    headers = ["STATUS", "SYSTEM ERROR MESSAGE", "styleGroupId", "vendorSkuCode"]
    for c, h in enumerate(headers, start=1):
        ws.cell(row=3, column=c, value=h)
    ws.cell(row=4, column=1, value="SKU_VALIDATION_FAILED")
    ws.cell(row=4, column=2, value="ISP cannot be empty; 6 digit Pincode is missing")
    ws.cell(row=4, column=3, value=11)
    ws.cell(row=4, column=4, value="78SAZ125BSI")
    wb.save(path)


def _sheet_csv(path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ROW NO", "BRAND", "STATUS", "SYSTEM ERROR MESSAGE"])
        w.writerow(["0", "", "SHEET_VALIDATION_FAILED",
                    "Style SKU Count Validation failed! : Minimum unique StyleGroupIds required is 1. Given sheet has only 7."])


def _listings_csv(path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["article type", "brand", "style status", "style id",
                    "seller sku code", "onhold reason"])
        w.writerow(["Sarees", "Ijor", "P", "43214808", "127SDE826NSB", ""])           # live -> skipped
        w.writerow(["Sarees", "Ijor", "PMR", "43214809", "128SDE826NSB",
                    "Image is a flat shot; reshoot on model"])                          # rejected


def test_detect_sku_xlsx(tmp_path):
    p = tmp_path / "r.xlsx"
    _sku_xlsx(p)
    src, reason = detect_format(str(p))
    assert src == "sku_xlsx"


def test_detect_sheet_csv(tmp_path):
    p = tmp_path / "e.csv"
    _sheet_csv(p)
    assert detect_format(str(p))[0] == "sheet_csv"


def test_detect_listings_report(tmp_path):
    p = tmp_path / "l.csv"
    _listings_csv(p)
    assert detect_format(str(p))[0] == "listings_report"


def test_detect_unknown_extension(tmp_path):
    p = tmp_path / "x.txt"
    p.write_text("hi", encoding="utf-8")
    src, reason = detect_format(str(p))
    assert src is None
    assert reason


def test_read_sku_xlsx_splits_clauses(tmp_path):
    p = tmp_path / "r.xlsx"
    _sku_xlsx(p)
    items = read_error_file(str(p), load_rules())
    assert all(isinstance(i, ErrorItem) for i in items)
    assert {i.raw_reason for i in items} == {
        "ISP cannot be empty", "6 digit Pincode is missing"}
    assert all(i.source_type == "sku_xlsx" and i.scope == "sku" for i in items)
    assert items[0].cells["vendorSkuCode"] == "78SAZ125BSI"


def test_read_sheet_csv_one_item_no_split(tmp_path):
    p = tmp_path / "e.csv"
    _sheet_csv(p)
    items = read_error_file(str(p))
    assert len(items) == 1
    assert items[0].scope == "sheet"
    assert items[0].sku is None
    assert "Style SKU Count" in items[0].raw_reason


def test_read_listings_skips_live_rows(tmp_path):
    p = tmp_path / "l.csv"
    _listings_csv(p)
    items = read_error_file(str(p))
    assert len(items) == 1
    assert items[0].sku == "128SDE826NSB"
    assert items[0].style_id == "43214809"
    assert items[0].source_type == "listings_report"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_error_sources.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write the implementation**

Create `src/myntra/error_sources.py`:

```python
import csv
import os
from dataclasses import dataclass

import openpyxl

from src.myntra.error_reader import read_errors, load_rules

_XLSX_HEADERS = {"STATUS", "SYSTEM ERROR MESSAGE"}
_SHEET_CSV_HEADERS = {"row no", "status", "system error message"}
_LISTINGS_HEADERS = {"style status", "seller sku code", "onhold reason"}


@dataclass
class ErrorItem:
    sku: str | None
    style_id: str | None
    source_type: str          # sku_xlsx | sheet_csv | listings_report
    scope: str                # sku | sheet
    raw_reason: str
    cells: dict | None


def _xlsx_error_sheet(path):
    """First worksheet whose header row (scanning rows 1..6) holds both error
    columns -> (sheet_name, header_row). Fixes the old hardcoded sheet='Sarees'."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        for ws in wb.worksheets:
            for i, row in enumerate(ws.iter_rows(min_row=1, max_row=6, values_only=True),
                                    start=1):
                vals = {str(v).strip() for v in row if v is not None}
                if _XLSX_HEADERS <= vals:
                    return ws.title, i
        return None, None
    finally:
        wb.close()


def _csv_header(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.reader(fh):
            return [(c or "").strip() for c in row]
    return []


def detect_format(path):
    """(source_type | None, user_facing_reason). Extension gate, then content
    fingerprint by column presence (spec §4)."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".xlsx", ".csv"):
        return None, "Please upload a Myntra rejection .xlsx or .csv file."
    try:
        if ext == ".xlsx":
            sheet, _ = _xlsx_error_sheet(path)
            if sheet:
                return "sku_xlsx", ""
            return None, ("This doesn't look like a Myntra rejection — please upload "
                          "the rejection file or the downloaded Listings Report.")
        header = {h.lower() for h in _csv_header(path)}
        if _SHEET_CSV_HEADERS <= header:
            return "sheet_csv", ""
        if _LISTINGS_HEADERS <= header:
            return "listings_report", ""
        return None, ("This doesn't look like a Myntra rejection or Listings Report — "
                      "please upload the rejection file or the downloaded Listings Report.")
    except Exception:
        return None, "Couldn't read this file."


def _read_sku_xlsx(path, rules):
    sheet, _ = _xlsx_error_sheet(path)
    items = []
    for re_ in read_errors(path, rules, sheet=sheet):
        for issue in re_.issues:
            items.append(ErrorItem(
                sku=re_.sku or None,
                style_id=re_.cells.get("styleId") or re_.cells.get("styleGroupId"),
                source_type="sku_xlsx", scope="sku",
                raw_reason=issue["raw"], cells=re_.cells))
    return items


def _rows_lower(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            yield {(k or "").strip().lower(): (v if v is not None else "") for k, v in row.items()}


def _read_sheet_csv(path):
    # Whole-sheet rejection: one message per row, NOT split on ';' (the trailing
    # ';failed while validation:null' is noise, not a separate error).
    items = []
    for rec in _rows_lower(path):
        msg = str(rec.get("system error message") or "").strip()
        if msg:
            items.append(ErrorItem(sku=None, style_id=None, source_type="sheet_csv",
                                   scope="sheet", raw_reason=msg, cells=None))
    return items


def _read_listings_report(path):
    items = []
    for rec in _rows_lower(path):
        reason = str(rec.get("onhold reason") or "").strip()
        if not reason:
            continue  # live/OK rows carry no onhold reason
        items.append(ErrorItem(
            sku=(rec.get("seller sku code") or None),
            style_id=(rec.get("style id") or None),
            source_type="listings_report", scope="sku",
            raw_reason=reason, cells=None))
    return items


def read_error_file(path, rules=None):
    """Detect the format and return a normalized ErrorItem list. Unknown formats
    return [] — the caller uses detect_format() for the user-facing reason."""
    rules = rules or load_rules()
    src, _ = detect_format(path)
    if src == "sku_xlsx":
        return _read_sku_xlsx(path, rules)
    if src == "sheet_csv":
        return _read_sheet_csv(path)
    if src == "listings_report":
        return _read_listings_report(path)
    return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_error_sources.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Verify against the real fixtures**

Run: `python -c "from src.myntra.error_sources import detect_format; import glob; [print(detect_format(f)[0], f) for f in glob.glob('errors/myntra/*.csv') + glob.glob('errors/myntra/*.xlsx')]"`
Expected: each real file prints a non-`None` source_type (`.xlsx` → `sku_xlsx`; `Output file error.csv`/`error-*.csv` → `sheet_csv`; `*MDirect_Listings_Report*.csv` → `listings_report`). If any prints `None`, inspect that file's header row and adjust the fingerprint sets, then re-run Step 4.

- [ ] **Step 6: Commit**

```bash
git add src/myntra/error_sources.py tests/test_error_sources.py
git commit -m "feat(myntra): 3-format error reader with fingerprint detection"
```

---

## Task 7: Correction log (`correction_log.py`)

**Files:**
- Create: `src/myntra/correction_log.py`
- Test: `tests/test_correction_log.py`

**Interfaces:**
- Consumes: a `get_json`/`put_json` store.
- Produces:
  - `LOG_KEY = "state/correction_log.json"`
  - `read_log(store, key=LOG_KEY) -> list`
  - `append(store, record, key=LOG_KEY) -> list` — append `record` dict to the list, persist, return the list.

- [ ] **Step 1: Write the failing test**

Create `tests/test_correction_log.py`:

```python
from src.myntra.correction_log import read_log, append
from src.web.settings import LocalJsonStore


def test_append_accumulates(tmp_path):
    st = LocalJsonStore(str(tmp_path / "log.json"))
    assert read_log(st) == []
    append(st, {"sku": "A", "changes": {"brand": ["", "Ijor Ethnic Partners"]}})
    append(st, {"sku": "B", "changes": {"ISP": ["", "2690"]}})
    log = read_log(st)
    assert [r["sku"] for r in log] == ["A", "B"]


def test_corrupt_log_treated_as_empty(tmp_path):
    p = tmp_path / "log.json"
    p.write_text("not json", encoding="utf-8")
    st = LocalJsonStore(str(p))
    assert read_log(st) == []
    append(st, {"sku": "C"})
    assert read_log(st)[-1]["sku"] == "C"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_correction_log.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write the implementation**

Create `src/myntra/correction_log.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_correction_log.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/myntra/correction_log.py tests/test_correction_log.py
git commit -m "feat(myntra): append-only correction log (Phase-D breadcrumb)"
```

---

## Task 8: Curate `error_rules.yaml`

**Files:**
- Modify: `config/myntra/error_rules.yaml`
- Test: `tests/test_error_rules_curated.py`

**Interfaces:**
- Consumes: `error_reader.load_rules`, `error_reader.classify`.
- Produces: new rules matching the real cryptic wordings — brand-code null → `auto_fix`(brand); incomplete manufacturer/packer info → `auto_fix`(address); HSN mismatch → `explain_only`; image flat-shot/pixelated/cropped → `explain_only`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_error_rules_curated.py`:

```python
from src.myntra.error_reader import load_rules, classify


def test_brand_code_null_is_brand_auto_fix():
    rules = load_rules()
    r = classify("Null key returned for cache operation [...getBrandCodeFromBrandName...]", rules)
    assert r["action"] == "auto_fix"
    assert r["category"] == "brand"


def test_incomplete_address_is_address_auto_fix():
    rules = load_rules()
    r = classify("Manufacturer and packer information is incomplete", rules)
    assert r["action"] == "auto_fix"
    assert r["category"] == "address"


def test_hsn_mismatch_is_explain_only():
    rules = load_rules()
    r = classify("HSN given 52081120 does not match the one present 50072010", rules)
    assert r["action"] == "explain_only"
    assert r["category"] == "hsn"


def test_flat_shot_image_is_explain_only():
    rules = load_rules()
    r = classify("Primary image appears to be a flat shot", rules)
    assert r["action"] == "explain_only"
    assert r["category"] == "image"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_error_rules_curated.py -q`
Expected: FAIL (matches fall through to `unknown`).

- [ ] **Step 3: Add the rules**

In `config/myntra/error_rules.yaml`, insert these entries into the `rules:` list (order matters — put the more specific `getbrandcodefrombrandname` before generic wordings; keep the existing rules):

```yaml
  - match: "getbrandcodefrombrandname"
    category: brand
    action: auto_fix
    explanation: "Myntra didn't recognise the brand name in your sheet, so the whole file was rejected. Re-generated with your registered brand from settings. If it still fails, your brand may not be enabled on Myntra yet."
  - match: "manufacturer and packer information is incomplete"
    category: address
    action: auto_fix
    explanation: "The manufacturer/packer name and address block was incomplete. Filled automatically from your saved settings."
  - match: "hsn"
    category: hsn
    action: explain_only
    explanation: "The HSN code you sent does not match the one Myntra already has for this style. Confirm the correct HSN for this fabric and re-generate; the app will not change tax codes automatically."
  - match: "flat shot"
    category: image
    action: explain_only
    explanation: "Myntra wants the product shown on a model, not laid flat. Re-shoot or replace this image, then re-upload — the app cannot fix photos."
  - match: "pixelated"
    category: image
    action: explain_only
    explanation: "The image resolution is too low. Replace it with a sharper photo, then re-upload — the app cannot fix photos."
  - match: "incorrectly cropped"
    category: image
    action: explain_only
    explanation: "The image is cropped in a way Myntra doesn't allow. Re-crop or replace it, then re-upload — the app cannot fix photos."
```

Note: the existing `"pincode is missing"` rule stays. Since `classify()` is first-match-wins and `read_errors`/`error_sources` split on `;`, a message containing both `getBrandCodeFromBrandName` and other tokens will match on the first rule whose substring appears — keep the brand rule above any broad rule.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_error_rules_curated.py tests/test_error_reader.py -q`
Expected: PASS (both files — the existing error-reader tests must still pass).

- [ ] **Step 5: Commit**

```bash
git add config/myntra/error_rules.yaml tests/test_error_rules_curated.py
git commit -m "feat(config): curate error_rules for brand/address/hsn/image wordings"
```

---

## Task 9: Corrector — brand & address auto-fix categories

**Files:**
- Modify: `src/myntra/corrector.py:35-83` (the `correct()` auto-fix loop)
- Test: `tests/test_corrector.py` (add cases)

**Interfaces:**
- Consumes: `constants["brand"]`, `constants["Manufacturer Name and Address with Pincode"]`, `constants["Packer Name and Address with Pincode"]`.
- Produces: `correct()` now also fills `brand` for category `brand`, and the two address headers for category `address` (in addition to the existing `pincode`).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_corrector.py`:

```python
def test_correct_fills_brand_and_address(tmp_path):
    from src.myntra.error_reader import RowError
    from src.myntra.corrector import correct

    template = read_template(TEMPLATE)
    constants = {
        "brand": "Ijor Ethnic Partners",
        "Manufacturer Name and Address with Pincode": "Ijor, Faridabad, 121006",
        "Packer Name and Address with Pincode": "Ijor, Faridabad, 121006",
    }
    rows = [
        RowError(row=4, sku="AAA", status="", cells={"vendorSkuCode": "AAA", "brand": ""},
                 issues=[{"category": "brand", "action": "auto_fix", "field": None,
                          "explanation": "brand", "raw": "getBrandCodeFromBrandName"}]),
        RowError(row=5, sku="BBB", status="",
                 cells={"vendorSkuCode": "BBB",
                        "Manufacturer Name and Address with Pincode": ""},
                 issues=[{"category": "address", "action": "auto_fix", "field": None,
                          "explanation": "addr", "raw": "information is incomplete"}]),
    ]
    out = tmp_path / "out.xlsx"
    summary = correct(rows, template, TEMPLATE, constants, {}, set(), str(out))
    assert "brand" in summary["changed"]["AAA"]
    assert "Manufacturer Name and Address with Pincode" in summary["changed"]["BBB"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_corrector.py::test_correct_fills_brand_and_address -q`
Expected: FAIL (`KeyError`/assertion — brand/address not yet auto-filled).

- [ ] **Step 3: Extend the auto-fix loop**

In `src/myntra/corrector.py`, replace the deterministic auto-fix block inside `correct()` (currently the `for issue in re_.issues:` loop handling `pincode`/`numeric`) with:

```python
        # deterministic auto-fixes derived from issue categories
        for issue in re_.issues:
            if issue["category"] in ("pincode", "address"):
                for h in ("Manufacturer Name and Address with Pincode",
                          "Packer Name and Address with Pincode"):
                    if constants.get(h):
                        cells[h] = constants[h]
                        changed.append(h)
            elif issue["category"] == "brand":
                if constants.get("brand"):
                    cells["brand"] = constants["brand"]
                    changed.append("brand")
            elif issue["category"] == "numeric":
                # Backfill an empty selling price (ISP) from MRP. fill_template
                # coerces MRP/ISP to real numbers, which covers the "non numeric"
                # half of this category for values that are already present.
                if not cells.get("ISP") and cells.get("MRP"):
                    cells["ISP"] = cells["MRP"]
                    changed.append("ISP")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_corrector.py -q`
Expected: PASS (existing + new case).

- [ ] **Step 5: Commit**

```bash
git add src/myntra/corrector.py tests/test_corrector.py
git commit -m "feat(corrector): brand + address deterministic auto-fixes"
```

---

## Task 10: Corrector — `correct_from_issues()` (Surface A wrapper + log)

**Files:**
- Modify: `src/myntra/corrector.py` (add function + imports)
- Test: `tests/test_corrector.py` (add cases)

**Interfaces:**
- Consumes: `explainer.ExplainedIssue`; `correct()`; `correction_log.append`; `error_reader.RowError`; `signature.normalize`.
- Produces: `correct_from_issues(issues, template, template_path, constants, answers, out_path, log_store=None, fix_id=None) -> summary`. Groups `ExplainedIssue` (Surface A, `cells` present) by SKU; a SKU with ANY `explain_only` issue is pulled into `summary["manual_needed"]` and EXCLUDED from the file; `drop_sku` SKUs are dropped; the rest are corrected via `correct()`. Writes one correction-log record per changed SKU. Returns `correct()`'s summary plus `summary["manual_needed"]`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_corrector.py`:

```python
def test_correct_from_issues_excludes_explain_only_and_logs(tmp_path):
    from src.myntra.explainer import ExplainedIssue
    from src.myntra.corrector import correct_from_issues
    from src.myntra.correction_log import read_log
    from src.web.settings import LocalJsonStore

    template = read_template(TEMPLATE)
    constants = {"brand": "Ijor Ethnic Partners"}

    def _iss(sku, action, category, cells, explanation="x", field=None):
        return ExplainedIssue(sku=sku, style_id=None, scope="sku",
                              source_type="sku_xlsx", raw_reason="getBrandCodeFromBrandName",
                              explanation=explanation, action=action, field=field,
                              category=category, source="yaml", cells=cells)

    issues = [
        _iss("AAA", "auto_fix", "brand", {"vendorSkuCode": "AAA", "brand": ""}),
        _iss("IMG", "explain_only", "image", {"vendorSkuCode": "IMG"},
             explanation="Reshoot the photo"),
    ]
    log = LocalJsonStore(str(tmp_path / "log.json"))
    out = tmp_path / "out.xlsx"
    summary = correct_from_issues(issues, template, TEMPLATE, constants, {},
                                  str(out), log_store=log, fix_id="fix123")

    assert summary["written"] == 1                       # only AAA written
    assert [m["sku"] for m in summary["manual_needed"]] == ["IMG"]
    assert "brand" in summary["changed"]["AAA"]
    recs = read_log(log)
    assert recs[0]["sku"] == "AAA"
    assert recs[0]["fix_id"] == "fix123"
    assert "brand" in recs[0]["changes"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_corrector.py::test_correct_from_issues_excludes_explain_only_and_logs -q`
Expected: FAIL (`ImportError: cannot import name 'correct_from_issues'`).

- [ ] **Step 3: Write the implementation**

At the top of `src/myntra/corrector.py`, add imports:

```python
import datetime
from collections import OrderedDict

from src.myntra.error_reader import RowError
from src.myntra.correction_log import append as log_append
from src.myntra.signature import normalize
```

Append this function to `src/myntra/corrector.py`:

```python
def _now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _derive_changes(sku, cells_before, answers, constants, changed_fields):
    """Best-effort {field: [old, new]} for the correction log. The log is a
    Phase-D breadcrumb (not read in this build), so approximating `old` from the
    pre-fix cells and `new` from the deterministic source is acceptable."""
    changes = {}
    for field in changed_fields:
        old = cells_before.get(field, "") or ""
        if field in (answers.get(sku) or {}):
            new = answers[sku][field]
        elif field == "ISP":
            new = cells_before.get("MRP", "")
        else:
            new = constants.get(field, "")
        changes[field] = [old, new]
    return changes


def correct_from_issues(issues, template, template_path, constants, answers, out_path,
                        log_store=None, fix_id=None):
    """Surface A: correct SKUs in place from ExplainedIssue records. A SKU with any
    explain_only issue is excluded from the file (reported under 'manual_needed');
    drop_sku SKUs are dropped; the rest go through the deterministic correct()."""
    by_sku = OrderedDict()
    for it in issues:
        by_sku.setdefault(it.sku, []).append(it)

    rows, drops, manual_needed = [], set(), []
    cells_before = {}
    for sku, its in by_sku.items():
        if any(i.action == "explain_only" for i in its):
            manual_needed.append({
                "sku": sku,
                "explanation": "; ".join(i.explanation for i in its
                                         if i.action == "explain_only")})
            continue
        cells = {}
        for it in its:
            if it.cells:
                cells.update(it.cells)
        cells_before[sku] = dict(cells)
        rows.append(RowError(
            row=0, sku=sku, status="", cells=cells,
            issues=[{"category": i.category, "action": i.action, "field": i.field,
                     "explanation": i.explanation, "raw": i.raw_reason} for i in its]))
        if any(i.action == "drop_sku" for i in its):
            drops.add(sku)

    summary = correct(rows, template, template_path, constants, answers, drops, out_path)
    summary["manual_needed"] = manual_needed

    if log_store is not None:
        for sku, fields in summary.get("changed", {}).items():
            first_raw = next((i.raw_reason for i in by_sku.get(sku, [])), "")
            log_append(log_store, {
                "timestamp": _now_iso(),
                "fix_id": fix_id,
                "sku": sku,
                "signature": normalize(first_raw)[0] if first_raw else "",
                "changes": _derive_changes(sku, cells_before.get(sku, {}),
                                           answers, constants, fields)})
    return summary
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_corrector.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/myntra/corrector.py tests/test_corrector.py
git commit -m "feat(corrector): correct_from_issues — Surface A gate + correction log"
```

---

## Task 11: Corrector — `regenerate_surface_b()` (rebuild from registry/export)

**Files:**
- Modify: `src/myntra/corrector.py` (add function)
- Test: `tests/test_corrector.py` (add case)

**Interfaces:**
- Consumes: `pipeline.main`; `sku_registry.read_registry`; `web.settings.sku_registry_store`.
- Produces: `regenerate_surface_b(skus, settings, out_dir, csv_path=None) -> summary`. Resolves per-SKU `styleGroupId`/`HSN` pins from the SKU registry, re-runs `pipeline.main(only_skus=..., style_group_id_by_sku=..., hsn_by_sku=..., csv_path=..., out_dir=...)`, and reports which requested SKUs were rebuilt vs `could_not_rebuild`. `skus=None` means rebuild the whole sheet (all products — for sheet-scoped A′ fixes). Returns a summary with `file`, `fixed`, `could_not_rebuild`, `manual_needed`, `written`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_corrector.py`:

```python
def test_regenerate_surface_b_resolves_pins_and_reports_missing(monkeypatch, tmp_path):
    import src.myntra.corrector as corrector
    from src.web.settings import Settings

    # Fake registry: AAA is known (has pins), BBB is unknown.
    monkeypatch.setattr(corrector, "sku_registry_store", lambda s: object())
    monkeypatch.setattr(corrector, "read_registry",
                        lambda store: {"AAA": {"style_group_id": 42, "hsn": "52081120"}})

    captured = {}

    def fake_pipeline(**kwargs):
        captured.update(kwargs)
        return {"filled": str(tmp_path / "myntra_filled.xlsx"),
                "products": 1, "records": [{"sku": "AAA"}]}

    monkeypatch.setattr(corrector, "pipeline_main", fake_pipeline)

    summary = corrector.regenerate_surface_b(["AAA", "BBB"], Settings(), str(tmp_path))
    assert captured["only_skus"] == {"AAA", "BBB"}
    assert captured["style_group_id_by_sku"] == {"AAA": 42}
    assert captured["hsn_by_sku"] == {"AAA": "52081120"}
    assert summary["fixed"] == ["AAA"]
    assert summary["could_not_rebuild"] == ["BBB"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_corrector.py::test_regenerate_surface_b_resolves_pins_and_reports_missing -q`
Expected: FAIL (`AttributeError: module ... has no attribute 'regenerate_surface_b'`).

- [ ] **Step 3: Write the implementation**

Add imports at the top of `src/myntra/corrector.py`:

```python
from src.myntra.pipeline import main as pipeline_main
from src.myntra.sku_registry import read_registry
from src.web.settings import sku_registry_store
```

Append this function:

```python
def regenerate_surface_b(skus, settings, out_dir, csv_path=None):
    """Surface B / A′: rebuild rejected SKUs from the SKU registry pins + the
    Shopify export, applying the CURRENT constants.yaml (so brand/address/pincode
    fixes flow through). skus=None rebuilds the whole sheet. SKUs resolvable in
    neither the registry nor the export are reported as could_not_rebuild."""
    reg = read_registry(sku_registry_store(settings))
    only = set(skus) if skus else None
    sgid, hsn = {}, {}
    for sku in (skus or []):
        e = reg.get(sku)
        if not e:
            continue
        if e.get("style_group_id") is not None:
            sgid[sku] = e["style_group_id"]
        if e.get("hsn") is not None:
            hsn[sku] = e["hsn"]

    res = pipeline_main(csv_path=csv_path, out_dir=out_dir, only_skus=only,
                        style_group_id_by_sku=sgid, hsn_by_sku=hsn)

    built = {r["sku"] for r in res.get("records", [])}
    missing = sorted(set(skus) - built) if skus else []
    return {
        "written": res.get("products", 0),
        "file": res.get("filled"),
        "fixed": sorted(built),
        "could_not_rebuild": missing,
        "manual_needed": [],
        "dropped": [],
        "rejected": {},
        "changed": {},
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_corrector.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/myntra/corrector.py tests/test_corrector.py
git commit -m "feat(corrector): regenerate_surface_b rebuilds rejected SKUs via pipeline"
```

---

## Task 12: Router rewrite (`fix.py`) + `fix.html`

**Files:**
- Modify: `src/web/routers/fix.py` (rewrite the flow)
- Modify: `src/web/templates/fix.html:7` (accept `.csv`)
- Test: `tests/web/test_fix.py` (rewrite)

**Interfaces:**
- Consumes: `error_sources.detect_format`/`read_error_file`; `explainer.explain_item`; `corrector.correct_from_issues`/`regenerate_surface_b`; `explanation_store`/`correction_log_store`; `pages.get_settings`; `error_reader.load_rules`; `read_template`.
- Produces: `POST /fix` renders `_fix_review.html` with `correctable`, `explain_only`, `fix_id`, `source_type` (or an unknown-format panel); `POST /fix/apply/{fix_id}` routes by `source_type` and renders `_fix_result.html`; `GET /fix/dismiss` renders a "no changes made" panel; `GET /fix/download/{fix_id}` serves `myntra_corrected.xlsx`.

- [ ] **Step 1: Write the failing test**

Rewrite `tests/web/test_fix.py`:

```python
from fastapi.testclient import TestClient

from src.web.main import create_app
from src.web.settings import Settings
import src.web.routers.fix as fixmod
from src.myntra.error_sources import ErrorItem


def _client():
    return TestClient(create_app(Settings(auth_disabled=True, s3_bucket="b")))


def _items():
    return [
        ErrorItem(sku="78SAZ", style_id=None, source_type="sku_xlsx", scope="sku",
                  raw_reason="Brand Colour (Remarks) cannot be null",
                  cells={"vendorSkuCode": "78SAZ", "Prominent Colour": "Ivory"}),
        ErrorItem(sku="IMG1", style_id=None, source_type="sku_xlsx", scope="sku",
                  raw_reason="Primary image appears to be a flat shot",
                  cells={"vendorSkuCode": "IMG1"}),
    ]


def test_upload_groups_correctable_and_explain_only(monkeypatch):
    client = _client()
    monkeypatch.setattr(fixmod, "detect_format", lambda p: ("sku_xlsx", ""))
    monkeypatch.setattr(fixmod, "read_error_file", lambda p, rules: _items())
    r = client.post("/fix", files={"file": ("rej.xlsx", b"x",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 200
    assert "Proceed" in r.text
    assert "Do not make any changes" in r.text
    assert "78SAZ" in r.text and "IMG1" in r.text


def test_unknown_format_shows_guidance(monkeypatch):
    client = _client()
    monkeypatch.setattr(fixmod, "detect_format",
                        lambda p: (None, "This doesn't look like a Myntra rejection"))
    r = client.post("/fix", files={"file": ("weird.csv", b"a,b\n1,2\n", "text/csv")})
    assert r.status_code == 200
    assert "doesn't look like a Myntra rejection" in r.text


def test_apply_surface_a_calls_correct_from_issues(monkeypatch):
    client = _client()
    monkeypatch.setattr(fixmod, "detect_format", lambda p: ("sku_xlsx", ""))
    monkeypatch.setattr(fixmod, "read_error_file", lambda p, rules: _items())
    monkeypatch.setattr(fixmod, "read_template", lambda p: object())
    monkeypatch.setattr(fixmod, "_load_constants", lambda: {})

    captured = {}

    def fake_cfi(issues, template, template_path, constants, answers, out_path,
                 log_store=None, fix_id=None):
        captured["answers"] = answers
        with open(out_path, "wb") as fh:
            fh.write(b"corrected")
        return {"written": 1, "manual_needed": [{"sku": "IMG1", "explanation": "flat shot"}],
                "dropped": [], "changed": {"78SAZ": ["Prominent Colour"]},
                "could_not_rebuild": [], "rejected": {}}

    monkeypatch.setattr(fixmod, "correct_from_issues", fake_cfi)

    up = client.post("/fix", files={"file": ("rej.xlsx", b"x",
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    fix_id = up.headers["x-fix-id"]
    r = client.post(f"/fix/apply/{fix_id}",
                    data={"answer__78SAZ__Prominent Colour": "Off White"})
    assert r.status_code == 200
    assert captured["answers"] == {"78SAZ": {"Prominent Colour": "Off White"}}
    assert "IMG1" in r.text  # manual_needed surfaced on the result screen


def test_apply_bogus_fix_id_returns_404():
    client = _client()
    r = client.post("/fix/apply/../etc", data={})
    assert r.status_code == 404


def test_dismiss_writes_nothing():
    client = _client()
    r = client.get("/fix/dismiss")
    assert r.status_code == 200
    assert "No changes" in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/web/test_fix.py -q`
Expected: FAIL (old router still references `read_errors`; new symbols missing).

- [ ] **Step 3: Rewrite `src/web/routers/fix.py`**

Replace the whole file with:

```python
import dataclasses
import json
import os
import re
import shutil
import uuid

import yaml
from fastapi import APIRouter, Request, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from src.myntra.error_reader import load_rules
from src.myntra.error_sources import detect_format, read_error_file
from src.myntra.explainer import explain_item, ExplainedIssue
from src.myntra.corrector import correct_from_issues, regenerate_surface_b
from src.myntra.template_reader import read_template
from src.web.settings import explanation_store, correction_log_store
from src.web.routers.pages import get_user, get_settings

router = APIRouter()
RUNTIME = os.path.join(os.path.dirname(os.path.dirname(__file__)), "runtime")
CONSTANTS = os.path.join("config", "myntra", "constants.yaml")
TEMPLATE = os.path.join("templates", "myntra", "Myntra-Sku-Template-2026-06-16.xlsx")

_ACCEPTED_EXT = (".xlsx", ".csv")


def _safe_fix_id(fix_id: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", fix_id):
        raise HTTPException(status_code=404, detail="unknown fix session")
    return fix_id


def _fix_dir(fix_id: str) -> str:
    fix_dir = os.path.join(RUNTIME, "fix-" + fix_id)
    if not os.path.realpath(fix_dir).startswith(os.path.realpath(RUNTIME) + os.sep):
        raise HTTPException(status_code=404, detail="unknown fix session")
    return fix_dir


def _templates():
    from src.web.main import templates
    return templates


def _resolve_template_path():
    return TEMPLATE


def _load_constants():
    with open(CONSTANTS, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _gemini_cfg(settings):
    return {"enabled": bool(settings.explain_with_gemini and settings.gemini_api_key),
            "api_key": settings.gemini_api_key, "model": settings.gemini_model,
            "client": None}


@router.get("/fix", response_class=HTMLResponse)
def fix_form(request: Request):
    get_user(request)
    return _templates().TemplateResponse(request, "fix.html", {"user": get_user(request)})


@router.get("/fix/dismiss", response_class=HTMLResponse)
def fix_dismiss(request: Request):
    get_user(request)
    return HTMLResponse(
        '<div class="panel"><h3>No changes made</h3>'
        '<p>Nothing was written. Fix the items listed above, then re-upload when ready.</p></div>')


@router.post("/fix", response_class=HTMLResponse)
def fix_upload(request: Request, file: UploadFile = File(...)):
    get_user(request)
    settings = get_settings(request)
    if not file.filename.lower().endswith(_ACCEPTED_EXT):
        raise HTTPException(status_code=400, detail="Please upload a Myntra .xlsx or .csv file")

    fix_id = uuid.uuid4().hex
    fix_dir = os.path.join(RUNTIME, "fix-" + fix_id)
    os.makedirs(fix_dir, exist_ok=True)
    ext = os.path.splitext(file.filename)[1].lower()
    err_path = os.path.join(fix_dir, "rejection" + ext)
    with open(err_path, "wb") as out:
        shutil.copyfileobj(file.file, out)

    source_type, reason = detect_format(err_path)
    if source_type is None:
        return HTMLResponse('<div class="panel"><h3>Unrecognised file</h3><p>%s</p></div>' % reason)

    rules = load_rules()
    store = explanation_store(settings)
    gem = _gemini_cfg(settings)
    items = read_error_file(err_path, rules)
    issues = [explain_item(it, rules, store=store, gemini=gem) for it in items]

    with open(os.path.join(fix_dir, "issues.json"), "w", encoding="utf-8") as fh:
        json.dump({"source_type": source_type,
                   "issues": [dataclasses.asdict(i) for i in issues]}, fh)

    correctable = [i for i in issues if i.action != "explain_only"]
    explain_only = [i for i in issues if i.action == "explain_only"]
    resp = _templates().TemplateResponse(request, "_fix_review.html", {
        "correctable": correctable, "explain_only": explain_only,
        "fix_id": fix_id, "source_type": source_type})
    resp.headers["x-fix-id"] = fix_id
    return resp


def _load_issues(fix_dir):
    path = os.path.join(fix_dir, "issues.json")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="session expired, please re-upload")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return data["source_type"], [ExplainedIssue(**d) for d in data["issues"]]


@router.post("/fix/apply/{fix_id}", response_class=HTMLResponse)
async def fix_apply(request: Request, fix_id: str):
    get_user(request)
    settings = get_settings(request)
    fix_id = _safe_fix_id(fix_id)
    fix_dir = _fix_dir(fix_id)
    source_type, issues = _load_issues(fix_dir)

    form = await request.form()
    answers, drops = {}, set()
    for key, value in form.items():
        if key.startswith("answer__") and str(value).strip():
            _, sku, field = key.split("__", 2)
            answers.setdefault(sku, {})[field] = value
        elif key.startswith("drop__"):
            drops.add(key.split("__", 1)[1])
    for sku in drops:
        for i in issues:
            if i.sku == sku:
                i.action = "drop_sku"

    out_path = os.path.join(fix_dir, "myntra_corrected.xlsx")
    if source_type == "sku_xlsx":
        template = read_template(_resolve_template_path())
        summary = correct_from_issues(
            issues, template, _resolve_template_path(), _load_constants(),
            answers, out_path, log_store=correction_log_store(settings), fix_id=fix_id)
    else:
        skus = sorted({i.sku for i in issues
                       if i.sku and i.action != "explain_only"}) or None
        summary = regenerate_surface_b(skus, settings, fix_dir)
        if summary.get("file") and os.path.exists(summary["file"]):
            shutil.copyfile(summary["file"], out_path)
        summary.setdefault("manual_needed", [
            {"sku": i.sku, "explanation": i.explanation}
            for i in issues if i.action == "explain_only"])

    return _templates().TemplateResponse(request, "_fix_result.html",
                                         {"summary": summary, "fix_id": fix_id})


@router.get("/fix/download/{fix_id}")
def fix_download(request: Request, fix_id: str):
    get_user(request)
    fix_id = _safe_fix_id(fix_id)
    fix_dir = _fix_dir(fix_id)
    path = os.path.join(fix_dir, "myntra_corrected.xlsx")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="not ready")
    return FileResponse(path, filename="myntra_corrected.xlsx")
```

- [ ] **Step 4: Update `fix.html` to accept `.csv`**

In `src/web/templates/fix.html`, change the upload line (`:7`) and its label:

```html
  <div class="drop">⬆ Drop the rejection <span class="mono">.xlsx</span> or Listings Report <span class="mono">.csv</span> Myntra sent back
    <input type="file" name="file" accept=".xlsx,.csv" required></div>
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/web/test_fix.py -q`
Expected: PASS (5 passed). Note: this task will fail templates rendering for `_fix_review.html`/`_fix_result.html` until Task 13 rewrites them — if the two render tests (`test_upload_...`, `test_apply_...`) fail on a template `UndefinedError`, proceed to Task 13 and re-run both tasks' tests together at the end of Task 13.

- [ ] **Step 6: Commit**

```bash
git add src/web/routers/fix.py src/web/templates/fix.html tests/web/test_fix.py
git commit -m "feat(web): /fix reads 3 formats, explains, routes surfaces behind gate"
```

---

## Task 13: Review & result templates (two groups + gate)

**Files:**
- Modify: `src/web/templates/_fix_review.html` (rewrite)
- Modify: `src/web/templates/_fix_result.html` (rewrite)

**Interfaces:**
- Consumes (`_fix_review.html`): `correctable` (list of `ExplainedIssue`), `explain_only` (list), `fix_id`, `source_type`.
- Consumes (`_fix_result.html`): `summary` with keys `written`, `changed` (dict), `dropped` (list), `could_not_rebuild` (list), `manual_needed` (list of `{sku, explanation}`), `rejected` (dict); `fix_id`.

- [ ] **Step 1: Rewrite `_fix_review.html`**

```html
<div id="review">
<form hx-post="/fix/apply/{{ fix_id }}" hx-target="#review" hx-swap="innerHTML">
  {% if correctable %}
    <h3>We can fix these ({{ correctable|length }})</h3>
    {% for i in correctable %}
      {% if i.action == 'manual_choice' %}
        <div class="card need"><strong class="mono">{{ i.sku }}</strong>
          <span class="flag">needs you</span>
          <div>{{ i.explanation }}{% if i.field %} — field <strong>{{ i.field }}</strong>{% endif %}</div>
          <input type="text" name="answer__{{ i.sku }}__{{ i.field }}"
                 value="{{ (i.cells or {}).get(i.field, '') }}">
          <div class="hint">Type the correct Myntra value. It is checked before writing.</div>
          <div class="hint mono">raw: {{ i.raw_reason }}</div></div>
      {% elif i.action == 'drop_sku' %}
        <div class="card need"><strong class="mono">{{ i.sku }}</strong>
          <span class="flag">already listed</span><div>{{ i.explanation }}</div>
          <label><input type="checkbox" name="drop__{{ i.sku }}" checked> Drop this SKU from the file</label>
          <div class="hint mono">raw: {{ i.raw_reason }}</div></div>
      {% else %}
        <div class="card auto"><strong class="mono">{{ i.sku or 'whole sheet' }}</strong>
          <span class="ok">auto-fix</span><div>{{ i.explanation }}</div>
          <div class="hint mono">raw: {{ i.raw_reason }}</div></div>
      {% endif %}
    {% endfor %}
  {% endif %}

  {% if explain_only %}
    <h3>You must fix these yourself first ({{ explain_only|length }})</h3>
    <p class="hint">These need real work (images, quality, tax codes). The app explains them but writes nothing — fix them, then re-upload.</p>
    {% for i in explain_only %}
      <div class="card expl"><strong class="mono">{{ i.sku or 'whole sheet' }}</strong>
        <span>explain only</span><div>{{ i.explanation }}</div>
        <div class="hint mono">raw: {{ i.raw_reason }}</div></div>
    {% endfor %}
  {% endif %}

  <div class="actions">
    {% if correctable %}
      <button class="btn" type="submit">Proceed with fix &amp; download corrected file →</button>
    {% endif %}
    <a class="btn ghost" hx-get="/fix/dismiss" hx-target="#review" hx-swap="innerHTML">Do not make any changes</a>
  </div>
</form>
</div>
```

- [ ] **Step 2: Rewrite `_fix_result.html`**

```html
<div class="panel">
  <h3 class="ok">✅ Done</h3>
  <ul class="mono">
    <li>{{ summary.written }} row(s) written to the corrected file</li>
    {% if summary.changed %}<li>{{ summary.changed|length }} SKU(s) changed</li>{% endif %}
    {% if summary.dropped %}<li>dropped (already listed): {{ summary.dropped|join(', ') }}</li>{% endif %}
    {% if summary.get('could_not_rebuild') %}<li class="flag">couldn't rebuild (data not found): {{ summary.could_not_rebuild|join(', ') }}</li>{% endif %}
    {% if summary.get('rejected') %}<li class="flag">rejected (not valid Myntra values): {{ summary.rejected }}</li>{% endif %}
  </ul>
  {% if summary.get('manual_needed') %}
    <h4 class="flag">Fix these yourself, then re-upload:</h4>
    <ul class="mono">
      {% for m in summary.manual_needed %}<li>{{ m.sku }} — {{ m.explanation }}</li>{% endfor %}
    </ul>
  {% endif %}
  {% if summary.written %}
    <a class="btn" href="/fix/download/{{ fix_id }}">⬇ Download corrected xlsx</a>
  {% endif %}
</div>
```

- [ ] **Step 3: Run the router + template tests together**

Run: `python -m pytest tests/web/test_fix.py -q`
Expected: PASS (5 passed) — the render paths now resolve.

- [ ] **Step 4: Commit**

```bash
git add src/web/templates/_fix_review.html src/web/templates/_fix_result.html
git commit -m "feat(web): two-group fix review with explicit Proceed / no-change gate"
```

---

## Task 14: End-to-end integration test per surface

**Files:**
- Test: `tests/web/test_fix_e2e.py` (new)

**Interfaces:**
- Consumes: the full stack through `TestClient`. Surface A uses a synthesized sku_xlsx (real correction, no network). Surface B patches `regenerate_surface_b` (the pipeline itself is covered by existing pipeline tests).

- [ ] **Step 1: Write the test**

Create `tests/web/test_fix_e2e.py`:

```python
import io
import openpyxl
from fastapi.testclient import TestClient

from src.web.main import create_app
from src.web.settings import Settings
import src.web.routers.fix as fixmod


def _client(tmp_path):
    return TestClient(create_app(Settings(
        auth_disabled=True, s3_bucket="b",
        explanation_store_path=str(tmp_path / "expl.json"),
        correction_log_path=str(tmp_path / "log.json"))))


def _sku_xlsx_bytes():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sarees"
    headers = ["STATUS", "SYSTEM ERROR MESSAGE", "vendorSkuCode", "brand",
               "Manufacturer Name and Address with Pincode",
               "Packer Name and Address with Pincode", "Front Image"]
    for c, h in enumerate(headers, start=1):
        ws.cell(row=3, column=c, value=h)
    # AAA: address auto-fix (correctable); IMG: flat-shot image (explain-only)
    ws.append([]) if False else None
    ws.cell(row=4, column=1, value="SKU_VALIDATION_FAILED")
    ws.cell(row=4, column=2, value="Manufacturer and packer information is incomplete")
    ws.cell(row=4, column=3, value="AAA")
    ws.cell(row=5, column=1, value="SKU_VALIDATION_FAILED")
    ws.cell(row=5, column=2, value="Primary image appears to be a flat shot")
    ws.cell(row=5, column=3, value="IMG")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_surface_a_end_to_end_excludes_explain_only(tmp_path, monkeypatch):
    # Use the real template + constants; keep fill_template light by pointing at
    # the repo template. No network: images come from cells, none present here.
    client = _client(tmp_path)
    up = client.post("/fix", files={"file": ("wLf4susb_file.xlsx", _sku_xlsx_bytes(),
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert up.status_code == 200
    assert "AAA" in up.text            # correctable group
    assert "flat shot" in up.text      # explain-only group
    fix_id = up.headers["x-fix-id"]

    r = client.post(f"/fix/apply/{fix_id}", data={})
    assert r.status_code == 200
    assert "Download corrected xlsx" in r.text
    assert "IMG" in r.text             # surfaced as manual-needed, not in the file

    # correction log recorded the AAA address fix
    from src.myntra.correction_log import read_log
    from src.web.settings import LocalJsonStore
    log = read_log(LocalJsonStore(str(tmp_path / "log.json")))
    assert any(rec["sku"] == "AAA" for rec in log)


def test_surface_b_end_to_end(monkeypatch, tmp_path):
    client = _client(tmp_path)

    def fake_regen(skus, settings, out_dir, csv_path=None):
        path = f"{out_dir}/myntra_filled.xlsx"
        with open(path, "wb") as fh:
            fh.write(b"rebuilt")
        return {"written": 1, "file": path, "fixed": list(skus or []),
                "could_not_rebuild": [], "manual_needed": [], "dropped": [],
                "rejected": {}, "changed": {}}

    monkeypatch.setattr(fixmod, "regenerate_surface_b", fake_regen)

    listings = (b'"style status","seller sku code","onhold reason","style id"\r\n'
                b'"PMR","127SDE826NSB","address incomplete","43214808"\r\n')
    up = client.post("/fix", files={"file": ("MDirect_Listings_Report.csv", listings, "text/csv")})
    assert up.status_code == 200
    fix_id = up.headers["x-fix-id"]
    r = client.post(f"/fix/apply/{fix_id}", data={})
    assert r.status_code == 200
    assert "Download corrected xlsx" in r.text
```

Note on Surface A e2e: `correct()` calls `fill_template(template_path, template, rows, out_path)`. With no image URLs in the cells, `_image_result` yields an empty `passed_urls` list, which `fill_template` handles (writes blanks). If `fill_template` raises on a missing image column for this minimal sheet, narrow the test to assert on the review screen + `correct_from_issues` summary via a direct unit call instead of the full HTTP apply — but attempt the HTTP path first.

- [ ] **Step 2: Run the test**

Run: `python -m pytest tests/web/test_fix_e2e.py -q`
Expected: PASS (2 passed). If Surface A fails inside `fill_template` for the minimal sheet, apply the fallback in the note above, then re-run.

- [ ] **Step 3: Run the FULL suite**

Run: `python -m pytest -q`
Expected: PASS — all pre-existing tests plus the new ones. Confirm no regression in `tests/test_corrector.py`, `tests/test_error_reader.py`, `tests/web/test_fix.py`, `tests/web/test_settings.py`.

- [ ] **Step 4: Commit**

```bash
git add tests/web/test_fix_e2e.py
git commit -m "test(web): end-to-end fix flow per surface (A correct-in-place, B rebuild)"
```

---

## Self-Review (completed against the spec)

**Spec coverage:**
- §2 three formats → Task 6 (`detect_format` + 3 readers), verified against real fixtures (Task 6 Step 5).
- §4 detection (extension + fingerprint, two unknown messages) → Task 6 `detect_format`.
- §5 explanation engine + lookup order + signature + Gemini guardrails → Tasks 2, 3, 4, 5.
- §6 buckets (auto/ask-user/explain-only), data-source resolver, output → Tasks 9, 10, 11, 12.
- §7 persistent state (learned store, correction log) → Tasks 3, 7 (non-atomic per Global Constraints deviation).
- §8 graceful degradation (Gemini off/down → raw; unknown format; can't rebuild; corrupt store) → Tasks 3, 4, 5, 6, 11.
- §9 config → Task 1.
- §10 testing (per-module + e2e) → every task's tests + Task 14.
- §11 out of scope (Phase D success detection/join) → not implemented; correction log is written but never read (Task 7 + note in Task 10).

**Placeholder scan:** no TBD/TODO; every code step carries full code; every test step carries assertions. One deliberate, bounded fallback note in Task 14 (with an explicit alternative) for `fill_template` on a minimal sheet.

**Type consistency:** `ExplainedIssue` fields identical across Tasks 5, 10, 12, 14; `ErrorItem` fields identical across Tasks 6, 12; `correct_from_issues` / `regenerate_surface_b` / `correct()` signatures consistent between definition (Tasks 9–11) and callers (Task 12). `explain_item(item, rules, store=, gemini=)` signature identical in Tasks 5 and 12. Store key constants (`STORE_KEY`, `LOG_KEY`, `REGISTRY_KEY`) match their modules.
