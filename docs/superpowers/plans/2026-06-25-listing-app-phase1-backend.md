# Listing App — Phase 1: Backend Semi-Automation Core — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the pure-Python backend the web app will call: an S3-backed styleGroupId ledger, a per-run `style_group_id_start` override on the pipeline, and the Myntra error-helper (parse resubmission file → classify → auto-fix + apply user answers → regenerate corrected sheet).

**Architecture:** New modules under `src/myntra/` plus one config file, reusing the existing `read_template`/`fill_template`/`MappedRow`/`ImageResult`. No web, AWS, or Docker here — storage and classification are injected so everything is unit-testable. The web app (Phase 2) will wire these together.

**Tech Stack:** Python 3.12, openpyxl, PyYAML, pytest. boto3 only for the production S3 store class (its logic is exercised through an injected fake in tests).

## Global Constraints

- Python 3.12; deps limited to those already in `requirements.txt` (no new runtime deps in this phase).
- Reuse existing units — do NOT reimplement: `src.myntra.template_reader.read_template`, `src.myntra.fill.fill_template`, `src.core.models.MappedRow`, `src.core.models.ImageResult`.
- Config-first: error classification lives in `config/myntra/error_rules.yaml`, never hardcoded in Python.
- Storage/classification injected for testability (mirror `src/core/s3_upload.py`'s injectable `client`).
- The styleGroupId ledger is JSON-in-S3; **`reserve()` must NOT advance the counter — only `confirm()` does** (failed uploads must free their IDs).
- TDD: failing test first, minimal code, commit per task.
- Commit messages: `feat:`/`test:`/`refactor:` prefix; end with the repo's Co-Authored-By trailer.

---

### Task 1: styleGroupId ledger (`groupid_ledger.py`)

**Files:**
- Create: `src/myntra/groupid_ledger.py`
- Test: `tests/test_groupid_ledger.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `read_ledger(store, key="state/myntra_groupid.json") -> dict`
  - `reserve(store, count, filename, key=...) -> tuple[int, str]` returns `(start_id, batch_id)`; appends a `pending` batch; does NOT advance `next_style_group_id`.
  - `confirm(store, batch_id, key=...) -> int` flips the batch to `confirmed`, advances `next_style_group_id` to `max(current, range_end+1)`, returns the new next id; raises `KeyError` if no matching pending batch.
  - `class S3JsonStore` with `get_json(key) -> dict | None` and `put_json(key, data) -> None`.
  - A test fake `FakeStore` (defined in the test file) implementing the same two methods over an in-memory dict.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_groupid_ledger.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_groupid_ledger.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.myntra.groupid_ledger'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/myntra/groupid_ledger.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_groupid_ledger.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/myntra/groupid_ledger.py tests/test_groupid_ledger.py
git commit -m "feat: S3-backed styleGroupId ledger (reserve/confirm, no DB)"
```

---

### Task 2: per-run `style_group_id_start` override on `main()`

**Files:**
- Modify: `src/myntra/pipeline.py` (the `main()` signature + the styleGroupId assignment block)
- Test: `tests/test_pipeline_override.py`

**Interfaces:**
- Consumes: existing `src.myntra.pipeline.main(...)`.
- Produces: `main(..., style_group_id_start=None)`. When not `None`, it overrides the `config/myntra/rules.yaml` value; the CLI path (no argument) keeps using the config value.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline_override.py
import io
import warnings
import openpyxl
from PIL import Image
from src.myntra.pipeline import main


def _fake_fetch():
    buf = io.BytesIO()
    Image.new("RGB", (1000, 1000), (10, 20, 30)).save(buf, "PNG")
    data = buf.getvalue()
    return lambda url: data


def test_style_group_id_start_override(tmp_path):
    warnings.filterwarnings("ignore")
    out = tmp_path / "out"
    main(
        template_path="templates/myntra/Myntra-Sku-Template-2026-06-16.xlsx",
        csv_path="tests/fixtures/products_export.csv",
        out_dir=str(out),
        config_dir="config/myntra",
        fetch=_fake_fetch(),
        upload=False,
        style_group_id_start=100,
    )
    ws = openpyxl.load_workbook(out / "myntra_filled.xlsx")["Sarees"]
    hdr = {ws.cell(3, c).value: c for c in range(1, ws.max_column + 1)}
    # fixture has 2 products -> styleGroupIds 100, 101
    assert ws.cell(4, hdr["styleGroupId"]).value == 100
    assert ws.cell(5, hdr["styleGroupId"]).value == 101
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pipeline_override.py -v`
Expected: FAIL — `TypeError: main() got an unexpected keyword argument 'style_group_id_start'`

- [ ] **Step 3: Modify `main()`**

In `src/myntra/pipeline.py`, change the signature:

```python
def main(template_path=None, csv_path=None, out_dir="output", config_dir="config/myntra",
         fetch=None, upload=None, style_group_id_start=None):
```

And change the styleGroupId assignment block inside the product loop from:

```python
        if rules.get("auto_style_group_id") and "styleGroupId" in template.col_index_by_header:
            start = rules.get("style_group_id_start", 1)
            mapped.cells["styleGroupId"] = str(start + i - 1)
```

to:

```python
        if rules.get("auto_style_group_id") and "styleGroupId" in template.col_index_by_header:
            start = (style_group_id_start if style_group_id_start is not None
                     else rules.get("style_group_id_start", 1))
            mapped.cells["styleGroupId"] = str(start + i - 1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_pipeline_override.py tests/test_end_to_end.py -v`
Expected: PASS (override test + the unchanged end-to-end test still green)

- [ ] **Step 5: Commit**

```bash
git add src/myntra/pipeline.py tests/test_pipeline_override.py
git commit -m "feat: per-run style_group_id_start override on pipeline.main()"
```

---

### Task 3: error knowledge base + reader (`error_rules.yaml`, `error_reader.py`)

**Files:**
- Create: `config/myntra/error_rules.yaml`
- Create: `src/myntra/error_reader.py`
- Test: `tests/test_error_reader.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `load_rules(path="config/myntra/error_rules.yaml") -> dict`
  - `classify(message, rules) -> dict` — returns `{"category","action","explanation","raw"}`; `action` is one of `auto_fix|manual_choice|drop_sku|explain_only`; unmatched → the `unknown` fallback.
  - `read_errors(path, rules, sheet="Sarees") -> list[RowError]` where
    `RowError` is a dataclass `{row:int, sku:str, status:str, cells:dict[str,str], issues:list[dict]}`,
    `cells` maps standard Sarees header → value (the `STATUS`/`SYSTEM ERROR MESSAGE` columns stripped),
    and each `issues` entry is the dict returned by `classify` for one `;`-separated message.

- [ ] **Step 1: Write `config/myntra/error_rules.yaml`**

```yaml
# Maps a Myntra SYSTEM ERROR MESSAGE substring (case-insensitive) to a plain-English
# explanation and an action. First matching rule wins. Add new wordings here, no code.
# action: auto_fix | manual_choice | drop_sku | explain_only
rules:
  - match: "already registered"
    category: duplicate
    action: drop_sku
    explanation: "This SKU is already listed on Myntra, so it cannot be created again. It will be removed from this upload."
  - match: "pincode is missing"
    category: pincode
    action: auto_fix
    explanation: "The manufacturer/packer address was missing a 6-digit pincode. Filled automatically from your settings."
  - match: "mrp cannot be empty or non numeric"
    category: numeric
    action: auto_fix
    explanation: "MRP must be a number. Corrected automatically."
  - match: "isp cannot be empty"
    category: numeric
    action: auto_fix
    explanation: "Selling price (ISP) was empty. Filled automatically from your price."
  - match: "extension is not jpg"
    category: image
    action: auto_fix
    explanation: "Image links must end in .jpg. The app now hosts images as .jpg, so this is corrected."
  - match: "brand colour (remarks) cannot be null"
    category: colour
    action: manual_choice
    field: "Prominent Colour"
    explanation: "Myntra needs a colour it recognises. Please pick the closest match."
  - match: "style sku count"
    category: stylegroupid
    action: auto_fix
    explanation: "The style group numbering was off. Renumbered automatically from your catalog counter."

unknown:
  category: unknown
  action: explain_only
  explanation: "Myntra reported an error we don't recognise yet. Please read the original message and fix it in the sheet."
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_error_reader.py
import openpyxl
from src.myntra.error_reader import load_rules, classify, read_errors


def _make_resub(path, rows):
    """Build a minimal Myntra resubmission xlsx. `rows` = list of
    (status, message, stylegroupid, vendorSkuCode)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sarees"
    headers = ["STATUS", "SYSTEM ERROR MESSAGE", "styleGroupId", "vendorSkuCode"]
    for c, h in enumerate(headers, start=1):
        ws.cell(row=3, column=c, value=h)
    r = 4
    for status, msg, sgid, sku in rows:
        ws.cell(row=r, column=1, value=status)
        ws.cell(row=r, column=2, value=msg)
        ws.cell(row=r, column=3, value=sgid)
        ws.cell(row=r, column=4, value=sku)
        r += 1
    wb.save(path)


def test_classify_known_and_unknown():
    rules = load_rules()
    dup = classify("Seller Sku Code X is already registered for seller 87065", rules)
    assert dup["category"] == "duplicate"
    assert dup["action"] == "drop_sku"
    unk = classify("some brand new error wording", rules)
    assert unk["category"] == "unknown"
    assert unk["action"] == "explain_only"


def test_read_errors_parses_rows_and_issues(tmp_path):
    p = tmp_path / "resub.xlsx"
    _make_resub(p, [
        ("SKU_VALIDATION_FAILED",
         "ISP cannot be empty for DIY source.; 6 digit Pincode is missing in manufacturer name and address",
         11, "78SAZ125BSI"),
        ("SKU_VALIDATION_FAILED",
         "Seller Sku Code 165SDE226RSG is already registered for seller 87065",
         12, "165SDE226RSG"),
    ])
    rules = load_rules()
    errs = read_errors(str(p), rules)
    assert len(errs) == 2
    first = errs[0]
    assert first.sku == "78SAZ125BSI"
    assert first.cells["styleGroupId"] == "11"          # values returned as strings
    assert "STATUS" not in first.cells                  # error columns stripped
    cats = {i["category"] for i in first.issues}
    assert cats == {"numeric", "pincode"}               # two ;-separated messages classified
    assert errs[1].issues[0]["action"] == "drop_sku"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_error_reader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.myntra.error_reader'`

- [ ] **Step 4: Write minimal implementation**

```python
# src/myntra/error_reader.py
from dataclasses import dataclass, field

import openpyxl
import yaml

ERROR_COLUMNS = {"STATUS", "SYSTEM ERROR MESSAGE"}
HEADER_ROW = 3
FIRST_DATA_ROW = 4


@dataclass
class RowError:
    row: int
    sku: str
    status: str
    cells: dict          # standard header -> value (error columns stripped)
    issues: list = field(default_factory=list)


def load_rules(path="config/myntra/error_rules.yaml"):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def classify(message, rules):
    msg = (message or "").strip()
    low = msg.lower()
    for rule in rules.get("rules", []):
        if str(rule["match"]).lower() in low:
            return {"category": rule["category"], "action": rule["action"],
                    "explanation": rule["explanation"], "field": rule.get("field"),
                    "raw": msg}
    unk = rules["unknown"]
    return {"category": unk["category"], "action": unk["action"],
            "explanation": unk["explanation"], "field": None, "raw": msg}


def read_errors(path, rules, sheet="Sarees"):
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[sheet]
    headers = [ws.cell(HEADER_ROW, c).value for c in range(1, ws.max_column + 1)]
    out = []
    for r in range(FIRST_DATA_ROW, ws.max_row + 1):
        status = ws.cell(r, 1).value
        message = ws.cell(r, 2).value
        if status is None and message is None:
            continue
        cells = {}
        for c, h in enumerate(headers, start=1):
            if h in ERROR_COLUMNS or h is None:
                continue
            v = ws.cell(r, c).value
            cells[h] = None if v is None else str(v)
        issues = [classify(m, rules) for m in str(message or "").split(";") if m.strip()]
        out.append(RowError(row=r, sku=cells.get("vendorSkuCode") or "",
                             status=str(status or ""), cells=cells, issues=issues))
    return out
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_error_reader.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add config/myntra/error_rules.yaml src/myntra/error_reader.py tests/test_error_reader.py
git commit -m "feat: Myntra error-file reader + config-driven classification"
```

---

### Task 4: corrector (`corrector.py`) — apply fixes + regenerate

**Files:**
- Create: `src/myntra/corrector.py`
- Test: `tests/test_corrector.py`

**Interfaces:**
- Consumes: `read_errors`/`RowError` (Task 3); `read_template` and `fill_template` (existing); `MappedRow`, `ImageResult` (existing).
- Produces:
  - `plan_corrections(row_errors) -> dict` — returns
    `{"auto": [sku,...], "drop": [sku,...], "manual": [{"sku","field","explanation","choices":[]}], "unknown": [{"sku","raw"}]}`.
    `choices` for a manual colour issue is filled by the caller from template vocab (passed in).
  - `correct(row_errors, template, constants, answers, drops, out_path) -> dict`
    where `answers = {sku: {field: value}}` and `drops = set(sku)`. Drops the listed/duplicate SKUs,
    applies `answers` to cells, coerces numeric/pincode auto-fixes, rebuilds image URLs from the row's
    image columns, and writes a corrected sheet via `fill_template`. Returns a summary
    `{"written": int, "dropped": [sku,...], "changed": {sku: [field,...]}}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_corrector.py
import openpyxl
from src.myntra.error_reader import load_rules, read_errors
from src.myntra.template_reader import read_template
from src.myntra.corrector import plan_corrections, correct

TEMPLATE = "templates/myntra/Myntra-Sku-Template-2026-06-16.xlsx"
IMG = "https://ijorethnicpartners.s3.ap-south-1.amazonaws.com/myntra"
# correct(row_errors, template, template_path, constants, answers, drops, out_path)


def _make_resub(path, rows):
    """rows = list of dict(status, message, cells={header: value})."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sarees"
    headers = ["STATUS", "SYSTEM ERROR MESSAGE", "styleGroupId", "vendorSkuCode",
               "brand", "Prominent Colour", "Brand Colour (Remarks)", "Front Image"]
    for c, h in enumerate(headers, start=1):
        ws.cell(row=3, column=c, value=h)
    r = 4
    for row in rows:
        ws.cell(row=r, column=1, value=row["status"])
        ws.cell(row=r, column=2, value=row["message"])
        for c, h in enumerate(headers, start=1):
            if h in ("STATUS", "SYSTEM ERROR MESSAGE"):
                continue
            ws.cell(row=r, column=c, value=row["cells"].get(h))
        r += 1
    wb.save(path)


def test_plan_marks_drop_and_manual(tmp_path):
    p = tmp_path / "resub.xlsx"
    _make_resub(p, [
        {"status": "SKU_VALIDATION_FAILED",
         "message": "Brand Colour (Remarks) cannot be null",
         "cells": {"styleGroupId": "11", "vendorSkuCode": "78SAZ125BSI",
                   "brand": "Ijor Ethnic Partners", "Front Image": f"{IMG}/78SAZ125BSI/1.jpg"}},
        {"status": "SKU_VALIDATION_FAILED",
         "message": "Seller Sku Code 165SDE226RSG is already registered",
         "cells": {"styleGroupId": "12", "vendorSkuCode": "165SDE226RSG",
                   "brand": "Ijor Ethnic Partners", "Front Image": f"{IMG}/165SDE226RSG/1.jpg"}},
    ])
    rules = load_rules()
    errs = read_errors(str(p), rules)
    plan = plan_corrections(errs)
    assert plan["drop"] == ["165SDE226RSG"]
    assert plan["manual"][0]["sku"] == "78SAZ125BSI"
    assert plan["manual"][0]["field"] == "Prominent Colour"


def test_correct_drops_and_applies_answer(tmp_path):
    p = tmp_path / "resub.xlsx"
    _make_resub(p, [
        {"status": "SKU_VALIDATION_FAILED",
         "message": "Brand Colour (Remarks) cannot be null",
         "cells": {"styleGroupId": "11", "vendorSkuCode": "78SAZ125BSI",
                   "brand": "Ijor Ethnic Partners", "Front Image": f"{IMG}/78SAZ125BSI/1.jpg"}},
        {"status": "SKU_VALIDATION_FAILED",
         "message": "Seller Sku Code 165SDE226RSG is already registered",
         "cells": {"styleGroupId": "12", "vendorSkuCode": "165SDE226RSG",
                   "brand": "Ijor Ethnic Partners", "Front Image": f"{IMG}/165SDE226RSG/1.jpg"}},
    ])
    rules = load_rules()
    errs = read_errors(str(p), rules)
    template = read_template(TEMPLATE)
    out = tmp_path / "corrected.xlsx"
    summary = correct(
        errs, template, TEMPLATE, constants={},
        answers={"78SAZ125BSI": {"Prominent Colour": "White"}},
        drops={"165SDE226RSG"}, out_path=str(out),
    )
    assert summary["written"] == 1
    assert summary["dropped"] == ["165SDE226RSG"]
    ws = openpyxl.load_workbook(out)["Sarees"]
    hdr = {ws.cell(3, c).value: c for c in range(1, ws.max_column + 1)}
    # only the kept SKU is written, with the chosen colour and its image URL
    assert ws.cell(4, hdr["vendorSkuCode"]).value == "78SAZ125BSI"
    assert ws.cell(4, hdr["Prominent Colour"]).value == "White"
    assert ws.cell(4, hdr["Front Image"]).value == f"{IMG}/78SAZ125BSI/1.jpg"
    assert ws.cell(5, hdr["vendorSkuCode"]).value in (None, "")  # dropped SKU not written
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_corrector.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.myntra.corrector'`

- [ ] **Step 3: Write minimal implementation**

`fill_template`'s first argument is the template **path** (it re-opens the file), so
`correct` takes `template_path` explicitly.

```python
# src/myntra/corrector.py
from src.core.models import ImageResult, MappedRow
from src.myntra.fill import IMAGE_COLUMNS, fill_template

# image column order is the same list the sheet-writer uses
_IMAGE_HEADERS = IMAGE_COLUMNS


def plan_corrections(row_errors):
    """Summarise what will happen per SKU before the user confirms."""
    plan = {"auto": [], "drop": [], "manual": [], "unknown": []}
    for re_ in row_errors:
        for issue in re_.issues:
            act = issue["action"]
            if act == "drop_sku":
                if re_.sku not in plan["drop"]:
                    plan["drop"].append(re_.sku)
            elif act == "manual_choice":
                plan["manual"].append({"sku": re_.sku, "field": issue.get("field"),
                                       "explanation": issue["explanation"], "choices": []})
            elif act == "auto_fix":
                if re_.sku not in plan["auto"]:
                    plan["auto"].append(re_.sku)
            else:
                plan["unknown"].append({"sku": re_.sku, "raw": issue["raw"]})
    return plan


def _image_result(sku, cells):
    urls = [cells[h] for h in _IMAGE_HEADERS if cells.get(h)]
    return ImageResult(sku=sku, passed_urls=urls)


def correct(row_errors, template, template_path, constants, answers, drops, out_path):
    """Apply drops + user answers + deterministic auto-fixes, regenerate a sheet.
    answers = {sku: {field: value}}; drops = set(sku). Returns a summary dict."""
    rows = []
    summary = {"written": 0, "dropped": [], "changed": {}}
    for re_ in row_errors:
        if re_.sku in drops:
            summary["dropped"].append(re_.sku)
            continue
        cells = dict(re_.cells)
        changed = []
        # deterministic auto-fixes derived from issue categories
        for issue in re_.issues:
            if issue["category"] == "pincode":
                for h in ("Manufacturer Name and Address with Pincode",
                          "Packer Name and Address with Pincode"):
                    if constants.get(h):
                        cells[h] = constants[h]
                        changed.append(h)
        # user answers (manual choices), e.g. Prominent Colour
        for field, value in (answers.get(re_.sku) or {}).items():
            cells[field] = value
            changed.append(field)
            # mirror the colour into the free-text Brand Colour (Remarks)
            if field == "Prominent Colour" and "Brand Colour (Remarks)" in template.col_index_by_header:
                cells["Brand Colour (Remarks)"] = str(value).lower()
        rows.append((MappedRow(sku=re_.sku, cells=cells), _image_result(re_.sku, cells)))
        if changed:
            summary["changed"][re_.sku] = changed
    fill_template(template_path, template, rows, out_path)
    summary["written"] = len(rows)
    return summary
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_corrector.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the whole suite (no regressions)**

Run: `python -m pytest -q`
Expected: PASS (all prior tests + the new ledger/override/error/corrector tests)

- [ ] **Step 6: Commit**

```bash
git add src/myntra/corrector.py tests/test_corrector.py
git commit -m "feat: Myntra error corrector — drop/auto-fix/answers + regenerate"
```

---

## Phase 1 done — what's next

This phase delivers the backend the web app will call: the styleGroupId ledger, the
`main()` override, and the full error-helper (read → classify → plan → correct). All
unit-tested with injected fakes and programmatic fixtures (no reliance on the
git-ignored `errors/myntra/` files).

**Subsequent plans (written after this is green):**
- **Phase 2** — FastAPI + Jinja/Tailwind/htmx web app (Generate + Fix flows, in-process
  job store), local-only, no AWS/auth yet.
- **Phase 3** — Dockerfile + Cognito auth + SSM/Secrets Manager config loading.
- **Phase 4** — GitHub Actions (test → build → ECR via OIDC) + EC2 user-data/IAM/infra.
