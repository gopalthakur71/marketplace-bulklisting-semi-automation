# App-Fix Small Batch (B.1, B.2, B.3, B.4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the four small, independent backlog fixes from the HSN-KB spec — COO auto-fill, undo mark-upload, verify-file notice, and manual styleGroupId seed — without touching the HSN knowledge base or the SKU-dedup review screen.

**Architecture:** Two of the fixes are pure ledger + route + htmx-fragment work in the web app (`groupid_ledger.py` + `generate.py` + Jinja partials); one is a declarative rule consumed by the mapper (`rules.yaml` + `mapper.py`); one is bold template copy. All ship on branch `feat/hsn-knowledge-base`, verified locally, never merged in this batch.

**Tech Stack:** Python 3, pytest, FastAPI + Starlette `TestClient`, Jinja2 templates, htmx (hx-post / hx-swap), openpyxl-derived `TemplateInfo`.

## Global Constraints

- Branch: `feat/hsn-knowledge-base` (off `main`). CI/CD deploys **only** on `main`, so **do not merge to main in this batch** — local verify only.
- Vocab-controlled cells go through `_set` / `_set_forced` (flag-don't-guess); never write a raw value into a vocab header without validation. See `memory/vocab-must-match-template.md`.
- `reserve()` never advances the counter; only `confirm()` does. Any counter rollback must preserve this invariant (guard against reissuing IDs a later batch already consumed).
- HTMX fragments returned by routes must be swappable in place (`hx-swap="outerHTML"` targets); keep `job.id` available to any button that posts back.
- Local preview loop (no deploy): `AUTH_DISABLED=1 LEDGER_LOCAL_PATH=<tmp>/ledger.json uvicorn src.web.main:app --reload` → http://localhost:8000/generate
- Run the full suite with `python -m pytest -q` from repo root.

---

### Task 1: B.3 — "Verify the file yourself" notice

**Files:**
- Modify: `src/web/templates/_result.html:9-14`
- Test: `tests/web/test_generate.py` (add one test)

**Interfaces:**
- Consumes: existing `job` context in `_result.html` (rendered by `job_status` in `generate.py`).
- Produces: nothing other tasks depend on.

- [ ] **Step 1: Write the failing test**

Add to `tests/web/test_generate.py` (reuse the existing `fake_main`/poll pattern from `test_generate_runs_job_and_confirm_advances_ledger`; assert the notice text is present on the ready result):

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
    job_id = r.headers["x-job-id"]

    import time
    poll = None
    for _ in range(20):
        poll = client.get(f"/jobs/{job_id}")
        if "Download" in poll.text:
            break
        time.sleep(0.05)
    assert "verify the downloaded file yourself" in poll.text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/web/test_generate.py::test_result_screen_shows_verify_notice -q`
Expected: FAIL — assertion error, the notice text is not present.

- [ ] **Step 3: Add the notice to the template**

In `src/web/templates/_result.html`, inside the `{% else %}` branch, immediately after the Download link (`<a class="btn" href="/generate/download/{{ job.id }}">⬇ Download xlsx</a>`) and before the confirm `<div>`:

```html
    <p class="flag mono" style="margin-top:12px"><strong>⚠ Please verify the downloaded file
      yourself and make any changes you need before uploading to Myntra.</strong></p>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/web/test_generate.py::test_result_screen_shows_verify_notice -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/web/templates/_result.html tests/web/test_generate.py
git commit -m "feat(web): bold verify-the-file notice on generate result (B.3)"
```

---

### Task 2: B.1 — Country Of Origin auto-fill across numbered columns

**Files:**
- Modify: `config/myntra/rules.yaml` (add one rule)
- Modify: `src/myntra/mapper.py:112-118` (new replication step in `map_product`)
- Test: `tests/test_mapper.py` (add one test)

**Interfaces:**
- Consumes: `rules` dict already threaded into `map_product(product, template, column_map, constants, rules=None)`; `constants` dict; `template.headers`.
- Produces: nothing other tasks depend on. The new rule key is `replicate_constant_across_numbered: list[str]`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_mapper.py`. Use a template that has `Country Of Origin` **and** `Country Of Origin2`/`Country Of Origin3` as vocab headers valued `India`:

```python
def test_replicate_constant_across_numbered_cols():
    headers = ["SKUCode", "Country Of Origin", "Country Of Origin2", "Country Of Origin3"]
    tmpl = TemplateInfo(
        headers=headers, header_row=3, first_data_row=4,
        col_index_by_header={h: i + 1 for i, h in enumerate(headers)},
        vocab_by_header={
            "Country Of Origin": ["India"],
            "Country Of Origin2": ["India"],
            "Country Of Origin3": ["India"],
        },
    )
    p = Product(handle="h", sku="S1", title="T", vendor="v", tags="", body_html="",
                price=1.0, compare_at_price=None, color=None, fabric=None,
                size=None, status="active", images=[])
    consts = {"Country Of Origin": "India"}
    rules = {"replicate_constant_across_numbered": ["Country Of Origin"]}
    row = map_product(p, tmpl, {}, consts, rules)
    assert row.cells["Country Of Origin"] == "India"
    assert row.cells["Country Of Origin2"] == "India"
    assert row.cells["Country Of Origin3"] == "India"
    assert "Country Of Origin2" not in row.blanks
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mapper.py::test_replicate_constant_across_numbered_cols -q`
Expected: FAIL — `KeyError: 'Country Of Origin2'` (only the base column was filled).

- [ ] **Step 3: Add the replication step to the mapper**

In `src/myntra/mapper.py`, in `map_product`, immediately after the constants loop (step 1, the `for header, val in constants.items(): _set_forced(...)` block) insert:

```python
    # 1b. replicate a base constant across its numbered siblings, e.g.
    # "Country Of Origin" -> "Country Of Origin2".."Country Of Origin5".
    # The template splits Country-Of-Origin into 5 vocab columns; the constant
    # only fills the base, leaving the rest flagged blank every run.
    for base in (rules.get("replicate_constant_across_numbered") or []):
        if base not in constants:
            continue
        pat = re.compile(rf"^{re.escape(base)}\d+$")
        for header in template.headers:
            if pat.match(header):
                _set_forced(row, template, header, constants[base])
```

(`re` is already imported at the top of `mapper.py`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_mapper.py::test_replicate_constant_across_numbered_cols -q`
Expected: PASS

- [ ] **Step 5: Add the rule to config**

In `config/myntra/rules.yaml`, after the `fabric_detection` block, add:

```yaml
# Some mandatory constants exist as several numbered vocab columns in the template
# (e.g. Country Of Origin, Country Of Origin2..5). The constant fills only the base
# column; list the base here to copy its value across every "<base><digits>" sibling.
replicate_constant_across_numbered:
  - "Country Of Origin"
```

- [ ] **Step 6: Run the mapper + config suites**

Run: `python -m pytest tests/test_mapper.py tests/test_config_loads.py -q`
Expected: PASS (existing tests still green; new test green).

- [ ] **Step 7: Commit**

```bash
git add config/myntra/rules.yaml src/myntra/mapper.py tests/test_mapper.py
git commit -m "feat(mapper): replicate Country Of Origin across numbered columns (B.1)"
```

---

### Task 3: B.2 — Undo for "Mark upload successful"

**Files:**
- Modify: `src/myntra/groupid_ledger.py` (new `unconfirm`)
- Create: `src/web/templates/_mark_upload.html`
- Create: `src/web/templates/_confirmed.html`
- Modify: `src/web/templates/_result.html` (include the mark-upload partial)
- Modify: `src/web/routers/generate.py` (confirm returns a fragment; new unconfirm route)
- Test: `tests/test_groupid_ledger.py` (ledger tests) and `tests/web/test_generate.py` (route test)

**Interfaces:**
- Consumes: `read_ledger`, `reserve`, `confirm` (existing); `job.batch_id`; `ledger_store(settings)`.
- Produces: `unconfirm(store, batch_id, key=LEDGER_KEY) -> int` (new next id after rollback; raises `ValueError` if a later batch was already confirmed, `KeyError` if no such confirmed batch). New route `POST /generate/unconfirm/{job_id}`. Partials `_mark_upload.html` (needs `job`) and `_confirmed.html` (needs `job`, `new_next`, optional `error`).

- [ ] **Step 1: Write the failing ledger tests**

Add to `tests/test_groupid_ledger.py`:

```python
def test_unconfirm_reverts_most_recent_batch():
    from src.myntra.groupid_ledger import unconfirm
    s = FakeStore()
    start, batch_id = reserve(s, count=3, filename="a.xlsx")   # range 1..3
    confirm(s, batch_id)                                        # next -> 4
    new_next = unconfirm(s, batch_id)                           # roll back to 1
    assert new_next == 1
    led = read_ledger(s)
    assert led["next_style_group_id"] == 1
    assert led["batches"][0]["status"] == "pending"


def test_unconfirm_blocked_when_later_batch_confirmed():
    import pytest
    from src.myntra.groupid_ledger import unconfirm
    s = FakeStore()
    _, b1 = reserve(s, count=2, filename="a.xlsx")   # range 1..2
    confirm(s, b1)                                    # next -> 3
    _, b2 = reserve(s, count=2, filename="b.xlsx")    # range 3..4
    confirm(s, b2)                                    # next -> 5
    with pytest.raises(ValueError):
        unconfirm(s, b1)                              # b2 already consumed IDs past b1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_groupid_ledger.py -q`
Expected: FAIL — `ImportError: cannot import name 'unconfirm'`.

- [ ] **Step 3: Implement `unconfirm` in the ledger**

In `src/myntra/groupid_ledger.py`, after `confirm`:

```python
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
```

- [ ] **Step 4: Run ledger tests to verify they pass**

Run: `python -m pytest tests/test_groupid_ledger.py -q`
Expected: PASS

- [ ] **Step 5: Create the two htmx partials**

Create `src/web/templates/_mark_upload.html`:

```html
<div style="margin-top:14px">
  <button class="btn green" hx-post="/generate/confirm/{{ job.id }}"
          hx-swap="outerHTML">✓ Mark upload successful</button>
</div>
```

Create `src/web/templates/_confirmed.html`:

```html
<div style="margin-top:14px">
  {% if error %}<p class="flag mono">⚠ {{ error }}</p>{% endif %}
  <p class="ok mono">✓ Confirmed. Ledger advanced to {{ new_next }}.</p>
  <button class="btn" hx-post="/generate/unconfirm/{{ job.id }}"
          hx-swap="outerHTML">↩ Undo</button>
</div>
```

- [ ] **Step 6: Point `_result.html` at the mark-upload partial**

In `src/web/templates/_result.html`, replace the confirm `<div>...</div>` block (the "Mark upload successful" button) with:

```html
    {% include "_mark_upload.html" %}
```

- [ ] **Step 7: Rewrite the confirm route + add the unconfirm route**

In `src/web/routers/generate.py`, replace the body of `confirm_upload` and add `unconfirm_upload`:

```python
@router.post("/generate/confirm/{job_id}", response_class=HTMLResponse)
def confirm_upload(request: Request, job_id: str):
    get_user(request)
    settings = get_settings(request)
    job = store.get(job_id)
    if not job or not job.batch_id:
        raise HTTPException(status_code=404, detail="unknown job")
    new_next = confirm(ledger_store(settings), job.batch_id)
    return _templates().TemplateResponse(
        request, "_confirmed.html", {"job": job, "new_next": new_next})


@router.post("/generate/unconfirm/{job_id}", response_class=HTMLResponse)
def unconfirm_upload(request: Request, job_id: str):
    get_user(request)
    settings = get_settings(request)
    job = store.get(job_id)
    if not job or not job.batch_id:
        raise HTTPException(status_code=404, detail="unknown job")
    try:
        unconfirm(ledger_store(settings), job.batch_id)
    except (ValueError, KeyError) as exc:
        # Guard tripped (a later batch was confirmed) — stay confirmed, show why.
        led = read_ledger(ledger_store(settings))
        return _templates().TemplateResponse(
            request, "_confirmed.html",
            {"job": job, "new_next": led["next_style_group_id"], "error": str(exc)})
    return _templates().TemplateResponse(request, "_mark_upload.html", {"job": job})
```

Update the import at the top of the file from:

```python
from src.myntra.groupid_ledger import reserve, confirm
```

to:

```python
from src.myntra.groupid_ledger import reserve, confirm, unconfirm, read_ledger
```

- [ ] **Step 8: Write the failing route test**

Add to `tests/web/test_generate.py` (reuse the `fake_main` pattern; drive generate → confirm → unconfirm and assert the ledger rolls back):

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

    import time
    for _ in range(20):
        if "Download" in client.get(f"/jobs/{job_id}").text:
            break
        time.sleep(0.05)

    from src.myntra.groupid_ledger import read_ledger
    from src.web.settings import ledger_store

    rc = client.post(f"/generate/confirm/{job_id}")
    assert "Undo" in rc.text
    assert read_ledger(ledger_store(settings))["next_style_group_id"] == 4

    ru = client.post(f"/generate/unconfirm/{job_id}")
    assert "Mark upload successful" in ru.text
    assert read_ledger(ledger_store(settings))["next_style_group_id"] == 1
```

- [ ] **Step 9: Run the route + ledger suites**

Run: `python -m pytest tests/web/test_generate.py tests/test_groupid_ledger.py -q`
Expected: PASS (existing `test_generate_runs_job_and_confirm_advances_ledger` still green — it only checks the ledger value, which is unchanged).

- [ ] **Step 10: Commit**

```bash
git add src/myntra/groupid_ledger.py src/web/routers/generate.py \
        src/web/templates/_mark_upload.html src/web/templates/_confirmed.html \
        src/web/templates/_result.html tests/test_groupid_ledger.py tests/web/test_generate.py
git commit -m "feat(web): undo Mark-upload-successful with guarded ledger rollback (B.2)"
```

---

### Task 4: B.4 — Manual styleGroupId seed (editable, with undo)

**Files:**
- Modify: `src/myntra/groupid_ledger.py` (new `set_next`, `undo_set_next`)
- Create: `src/web/templates/_style_start.html`
- Modify: `src/web/templates/generate.html` (use the partial; add Edit form)
- Modify: `src/web/routers/generate.py` (new set / undo routes; render the partial)
- Test: `tests/test_groupid_ledger.py` and `tests/web/test_generate.py`

**Interfaces:**
- Consumes: `read_ledger`, `ledger_store(settings)`, the `generate_form` route's existing `next_id` computation.
- Produces: `set_next(store, value, key=LEDGER_KEY) -> dict` with keys `next` (=value+1), `prev`, `warn` (bool, True when the new next is lower than the old one); `undo_set_next(store, key=LEDGER_KEY) -> int` (raises `ValueError` if nothing to undo). New routes `POST /generate/style-start` and `POST /generate/style-start/undo`, both rendering `_style_start.html` (context: `next_id`, optional `warn`, `undone`). Partial `_style_start.html` replaces the styleGroupId line in `generate.html`.

- [ ] **Step 1: Write the failing ledger tests**

Add to `tests/test_groupid_ledger.py`:

```python
def test_set_next_records_value_plus_one_and_undo_restores():
    from src.myntra.groupid_ledger import set_next, undo_set_next
    s = FakeStore()
    reserve(s, count=1, filename="a.xlsx")
    confirm(s, read_ledger(s)["batches"][0]["id"])   # next -> 2
    res = set_next(s, 40)                             # user says "last used = 40"
    assert res["next"] == 41
    assert res["prev"] == 2
    assert res["warn"] is False
    assert read_ledger(s)["next_style_group_id"] == 41
    assert undo_set_next(s) == 2
    assert read_ledger(s)["next_style_group_id"] == 2


def test_set_next_warns_when_lowering():
    from src.myntra.groupid_ledger import set_next
    s = FakeStore()
    reserve(s, count=10, filename="a.xlsx")
    confirm(s, read_ledger(s)["batches"][0]["id"])   # next -> 11
    res = set_next(s, 3)                              # lowering to 4 (< 11)
    assert res["next"] == 4
    assert res["warn"] is True
    assert read_ledger(s)["next_style_group_id"] == 4   # allowed despite warning


def test_undo_set_next_without_prior_raises():
    import pytest
    from src.myntra.groupid_ledger import undo_set_next
    s = FakeStore()
    with pytest.raises(ValueError):
        undo_set_next(s)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_groupid_ledger.py -q`
Expected: FAIL — `ImportError: cannot import name 'set_next'`.

- [ ] **Step 3: Implement `set_next` / `undo_set_next`**

In `src/myntra/groupid_ledger.py`, after `unconfirm`:

```python
def set_next(store, value, key=LEDGER_KEY):
    """Snap next_style_group_id to `value + 1`. The user enters the LAST styleGroupId
    they used (e.g. from a manual Myntra upload outside the app); the next batch
    continues from value+1. Records the previous next for a one-step undo. Returns
    {"next", "prev", "warn"}; warn=True when lowering the counter (risks reissuing
    IDs used by confirmed batches — allowed, the user is authoritative)."""
    led = read_ledger(store, key)
    prev = led["next_style_group_id"]
    new_next = value + 1
    led["style_seed_prev"] = prev
    led["next_style_group_id"] = new_next
    store.put_json(key, led)
    return {"next": new_next, "prev": prev, "warn": new_next < prev}


def undo_set_next(store, key=LEDGER_KEY):
    """Restore next_style_group_id to the value before the last set_next."""
    led = read_ledger(store, key)
    if "style_seed_prev" not in led:
        raise ValueError("nothing to undo")
    led["next_style_group_id"] = led.pop("style_seed_prev")
    store.put_json(key, led)
    return led["next_style_group_id"]
```

- [ ] **Step 4: Run ledger tests to verify they pass**

Run: `python -m pytest tests/test_groupid_ledger.py -q`
Expected: PASS

- [ ] **Step 5: Create the styleGroupId partial**

Create `src/web/templates/_style_start.html`:

```html
<div id="style-start">
  <p class="mono">styleGroupId start: <strong>{{ next_id }}</strong>
    {% if undone %}(auto, from ledger){% elif warn is defined and warn %}(set manually){% else %}(auto, from ledger){% endif %}
    <button type="button" class="btn small" onclick="document.getElementById('style-edit').style.display='block'">Edit</button>
  </p>
  {% if warn is defined and warn %}
    <p class="flag mono">⚠ Lowered below the previous counter — this can reissue styleGroupIds already used by confirmed batches.</p>
  {% endif %}
  <div id="style-edit" style="display:none;margin-top:8px">
    <label class="mono">Last used styleGroupId
      <input type="number" name="last_used" min="0" required></label>
    <button class="btn small" hx-post="/generate/style-start"
            hx-include="[name='last_used']" hx-target="#style-start"
            hx-swap="outerHTML">Save</button>
    <button class="btn small" hx-post="/generate/style-start/undo"
            hx-target="#style-start" hx-swap="outerHTML">Undo</button>
  </div>
</div>
```

- [ ] **Step 6: Use the partial in `generate.html`**

In `src/web/templates/generate.html`, replace the line
`<p class="mono">styleGroupId start: <strong>{{ next_id }}</strong> (auto, from ledger)</p>`
with:

```html
  {% include "_style_start.html" %}
```

- [ ] **Step 7: Add the set / undo routes**

In `src/web/routers/generate.py`, add (and extend the ledger import to include `set_next, undo_set_next`):

```python
from fastapi import Form  # add to the existing fastapi import line
```

```python
@router.post("/generate/style-start", response_class=HTMLResponse)
def style_start_set(request: Request, last_used: int = Form(...)):
    get_user(request)
    settings = get_settings(request)
    from src.myntra.groupid_ledger import set_next
    res = set_next(ledger_store(settings), last_used)
    return _templates().TemplateResponse(
        request, "_style_start.html", {"next_id": res["next"], "warn": res["warn"]})


@router.post("/generate/style-start/undo", response_class=HTMLResponse)
def style_start_undo(request: Request):
    get_user(request)
    settings = get_settings(request)
    from src.myntra.groupid_ledger import undo_set_next, read_ledger as _rl
    try:
        next_id = undo_set_next(ledger_store(settings))
    except ValueError:
        next_id = _rl(ledger_store(settings))["next_style_group_id"]
    return _templates().TemplateResponse(
        request, "_style_start.html", {"next_id": next_id, "undone": True})
```

- [ ] **Step 8: Write the failing route test**

Add to `tests/web/test_generate.py`:

```python
def test_style_start_set_and_undo(tmp_path):
    client, settings = _client(tmp_path)
    from src.myntra.groupid_ledger import read_ledger
    from src.web.settings import ledger_store

    r = client.post("/generate/style-start", data={"last_used": "40"})
    assert r.status_code == 200
    assert "41" in r.text
    assert read_ledger(ledger_store(settings))["next_style_group_id"] == 41

    ru = client.post("/generate/style-start/undo")
    assert ru.status_code == 200
    assert read_ledger(ledger_store(settings))["next_style_group_id"] == 1
```

- [ ] **Step 9: Run the web + ledger suites**

Run: `python -m pytest tests/web/test_generate.py tests/test_groupid_ledger.py -q`
Expected: PASS (existing `generate_form` render test, if any in `test_generate.py`, still green — the partial renders the same `next_id`).

- [ ] **Step 10: Commit**

```bash
git add src/myntra/groupid_ledger.py src/web/routers/generate.py \
        src/web/templates/_style_start.html src/web/templates/generate.html \
        tests/test_groupid_ledger.py tests/web/test_generate.py
git commit -m "feat(web): editable manual styleGroupId seed with undo (B.4)"
```

---

### Task 5: Full-suite verification + local smoke

**Files:** none (verification only).

- [ ] **Step 1: Run the entire suite**

Run: `python -m pytest -q`
Expected: all green (57 pre-existing + the new tests).

- [ ] **Step 2: Local smoke of the web app**

Run:
```bash
AUTH_DISABLED=1 LEDGER_LOCAL_PATH="$TMPDIR/ledger.json" \
  python -m uvicorn src.web.main:app --port 8000
```
Open http://localhost:8000/generate and confirm: the styleGroupId line has an **Edit** button; Edit → enter a value → Save shows `start: value+1 (set manually)` with **Undo**; upload a small CSV → result screen shows the **bold verify notice**; **Mark upload successful** → **Undo** appears and reverts.

- [ ] **Step 3: Stop the server.** No merge to `main` (branch stays `feat/hsn-knowledge-base`).

---

## Self-Review

**Spec coverage (against `2026-07-02-hsn-kb-and-app-fixes-design.md`):**
- B.1 COO auto-fill → Task 2 (rule + mapper `_set_forced` replication, vocab-validated). ✔
- B.2 undo mark-upload → Task 3 (`unconfirm` with `next == range[1]+1` guard; route + htmx swap back to the mark-upload button). ✔
- B.3 verify notice → Task 1 (bold copy near Download in `_result.html`). ✔
- B.4 manual styleGroupId seed → Task 4 (`set_next`=value+1, `undo_set_next` audit-trail restore, lower-than-current warn-but-allow, Edit/Save/Undo form). ✔
- Part A (HSN KB) and the SKU-dedup review screen are **intentionally out of scope** for this batch.

**Placeholder scan:** every code step shows the actual code and exact `pytest` command; no TBD/TODO/"handle edge cases".

**Type consistency:** `unconfirm`/`set_next`/`undo_set_next` signatures and return types are identical between their ledger-task definition and their route-task consumption; `_confirmed.html` context keys (`job`, `new_next`, `error`) match both `confirm_upload` and `unconfirm_upload`; `_style_start.html` context keys (`next_id`, `warn`, `undone`) match both style-start routes and the `generate_form` include (which passes `next_id`).
