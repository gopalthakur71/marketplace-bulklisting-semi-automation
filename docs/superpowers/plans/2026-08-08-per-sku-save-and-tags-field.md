# Per-SKU Save Button + Editable `tags` Field — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every SKU panel on the in-app attribute screen its own Save button that writes only that row, and surface the Myntra template's free-text `tags` column as an editable, pre-filled input.

**Architecture:** The save path already supports subsets — `write_attributes` writes only the ordinals it is handed, and `_submitted()` already parses per-ordinal form keys. So this is mostly a front-end change plus a thin second route. The existing bulk route and the new per-panel route share one extracted `_save_entries()` helper. `tags` is free text with no template vocabulary, so it travels a **separate** config list and validation function rather than loosening the exact-vocabulary rule that guards the twelve dropdown columns.

**Tech Stack:** FastAPI, Jinja2, htmx, openpyxl, pytest.

**Spec:** `docs/superpowers/specs/2026-08-08-per-sku-save-and-tags-field-design.md` (commit `435dee4`)
**Branch:** `feat/per-sku-save-and-tags` (already created and checked out)

## Global Constraints

- **Never loosen `validate_submitted`.** The twelve dropdown columns must keep exact-membership validation against the template's own vocabulary. Free text travels a parallel path.
- **`write_attributes` needs no change.** It resolves any header via `template.col_index_by_header`, and `tags` is one of the template's 80 headers. Do not modify it.
- **Re-apply `shared_to_inline` after any openpyxl re-save.** `write_attributes` already does this; do not add a second save path that skips it.
- **Never name a Jinja context key `values`** — in a template `p.values` resolves to `dict.values`, the method, silently breaking comparisons. This bit the original build; the existing code uses `chosen` for this reason.
- **Per-panel buttons must be `type="button"`.** A default `<button>` inside the existing `<form>` submits every panel.
- The bulk "Save attributes" button and route keep their current behaviour exactly.
- The 12 dropdown columns are indexed in `rules.yaml` order: `0` Prominent Colour, `1` Second Prominent Colour, `2` Third Prominent Colour, `3` Saree Fabric, `4` Blouse Fabric, `5` Type, `6` Ornamentation, `7` Border, `8` Pattern, `9` Print or Pattern Type, `10` Wash Care, `11` Usage. Free-text columns are indexed separately: `0` tags.
- Total filled count becomes **13** (12 dropdowns + tags).
- Run the full suite with `python -m pytest -q`. **It takes ~19 minutes** — each attribute test loads the ~973 KB template through openpyxl. Use targeted `pytest path::test_name` while iterating.

---

## File Structure

| File | Responsibility |
|---|---|
| `config/myntra/rules.yaml` | Declares `user_filled_freetext: [tags]` beside the existing `user_filled_attributes` |
| `src/myntra/attribute_entry.py` | Adds `user_filled_freetext()` + `validate_freetext()`. Pure, no I/O beyond the YAML read. |
| `src/web/routers/attributes.py` | Form parsing, the shared `_save_entries()` helper, the write lock, both save routes |
| `src/web/templates/_attr_panel.html` | Panel markup: tags input, Save button, id'd count span |
| `src/web/templates/_attr_panel_saved.html` | **New.** Compact per-panel save result + out-of-band count update |
| `src/web/static/app.css` | Footer strip and free-text input styling |
| `tests/test_attribute_entry.py` | Unit tests for the free-text core |
| `tests/web/test_attributes.py` | Route and rendering tests |
| `docs/APP-FEATURES-GUIDE.md` | Owner-facing description of both changes |

---

### Task 1: Free-text config and validation core

**Files:**
- Modify: `config/myntra/rules.yaml` (after the `user_filled_attributes` block, line ~41)
- Modify: `src/myntra/attribute_entry.py`
- Test: `tests/test_attribute_entry.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces:
  - `FALLBACK_USER_FREETEXT: list[str]` — module constant, `["tags"]`
  - `user_filled_freetext(config_dir=CONFIG_DIR) -> list[str]`
  - `validate_freetext(values: dict[str, str|None], columns: list[str]) -> dict[str, str|None]` — raises `AttributeValueError` for an unknown column; blank/whitespace-only becomes `None`; otherwise the stripped string.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_attribute_entry.py`:

```python
def test_user_filled_freetext_reads_the_yaml_list():
    from src.myntra.attribute_entry import user_filled_freetext
    assert user_filled_freetext() == ["tags"]


def test_validate_freetext_accepts_any_value():
    from src.myntra.attribute_entry import validate_freetext
    out = validate_freetext({"tags": "saree, cotton, handloom"}, ["tags"])
    assert out == {"tags": "saree, cotton, handloom"}


def test_validate_freetext_strips_whitespace():
    from src.myntra.attribute_entry import validate_freetext
    assert validate_freetext({"tags": "  festive  "}, ["tags"]) == {"tags": "festive"}


def test_validate_freetext_turns_blank_into_none():
    from src.myntra.attribute_entry import validate_freetext
    assert validate_freetext({"tags": "   "}, ["tags"]) == {"tags": None}
    assert validate_freetext({"tags": ""}, ["tags"]) == {"tags": None}
    assert validate_freetext({"tags": None}, ["tags"]) == {"tags": None}


def test_validate_freetext_rejects_an_unknown_column():
    from src.myntra.attribute_entry import AttributeValueError, validate_freetext
    with pytest.raises(AttributeValueError):
        validate_freetext({"styleGroupId": "9999"}, ["tags"])


def test_validate_freetext_does_not_check_any_vocabulary():
    """The whole point: a value that would be rejected as a dropdown is fine here."""
    from src.myntra.attribute_entry import validate_freetext
    assert validate_freetext({"tags": "Salmon Pink"}, ["tags"]) == {"tags": "Salmon Pink"}
```

Confirm `import pytest` is present at the top of the file; add it if not.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_attribute_entry.py -q -k freetext`
Expected: FAIL — `ImportError: cannot import name 'user_filled_freetext'`.

- [ ] **Step 3: Add the YAML list**

In `config/myntra/rules.yaml`, immediately after the `user_filled_attributes` block (which ends with `  - Usage`), add:

```yaml

# Free-text columns the seller fills by hand. These have NO dropdown vocabulary in
# the Myntra template, so they are deliberately kept OUT of user_filled_attributes:
# everything in that list is validated by exact membership in the template's own
# vocabulary, and that rule must never be relaxed. `tags` is pre-filled by the
# pipeline from the Shopify export (column_map.yaml: tags -> tags); the seller may
# overwrite it in the app.
user_filled_freetext:
  - tags
```

- [ ] **Step 4: Implement the two functions**

In `src/myntra/attribute_entry.py`, after `FALLBACK_USER_FILLED`, add:

```python
# Free-text columns: no template vocabulary, so no membership check is possible.
# Kept separate from FALLBACK_USER_FILLED so the exact-vocabulary rule that guards
# the dropdown columns can never accidentally be relaxed.
FALLBACK_USER_FREETEXT = ["tags"]
```

After `user_filled_attributes()`, add:

```python
def user_filled_freetext(config_dir=CONFIG_DIR):
    with open(os.path.join(config_dir, "rules.yaml"), encoding="utf-8") as fh:
        rules = yaml.safe_load(fh) or {}
    return rules.get("user_filled_freetext") or list(FALLBACK_USER_FREETEXT)
```

After `validate_submitted()`, add:

```python
def validate_freetext(values, columns):
    """Accept any text for a known free-text column. Blank -> None (clears the cell).

    Deliberately does NOT consult a vocabulary — these columns have none. The column
    name is still checked so a tampered form cannot write into an arbitrary template
    cell."""
    out = {}
    for column, value in values.items():
        if column not in columns:
            raise AttributeValueError(f"Unknown free-text column: {column}")
        if value is None or str(value).strip() == "":
            out[column] = None
            continue
        out[column] = str(value).strip()
    return out
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_attribute_entry.py -q`
Expected: PASS — the six new tests plus every pre-existing test in the file.

- [ ] **Step 6: Commit**

```bash
git add config/myntra/rules.yaml src/myntra/attribute_entry.py tests/test_attribute_entry.py
git commit -m "feat(attributes): free-text column list and validate_freetext"
```

---

### Task 2: Shared save helper, free-text parsing, write lock

**Files:**
- Modify: `src/web/routers/attributes.py`
- Test: `tests/web/test_attributes.py`

**Interfaces:**
- Consumes: `user_filled_freetext()`, `validate_freetext()` from Task 1.
- Produces:
  - `_WRITE_LOCK: threading.Lock` — module-level, held across the whole `write_attributes` call.
  - `_submitted_free(form, columns) -> dict[int, dict[str, str]]` — parses `free__{ordinal}__{index}`.
  - `_build_payload(entries, free, template, columns, free_columns) -> list[dict]` — validates and merges; raises `AttributeValueError`.
  - `async _save_entries(request, job_id) -> (job, ordinals: list[int], payload: list[dict], error: str|None)`

At this point the bulk route is rewritten on top of the helper and `tags` becomes savable through it. The Save button itself arrives in Task 4.

- [ ] **Step 1: Write the failing tests**

Append to `tests/web/test_attributes.py`:

```python
def _cell(xlsx, template, ordinal, header):
    import openpyxl
    wb = openpyxl.load_workbook(xlsx, data_only=True)
    v = wb["Sarees"].cell(row=template.first_data_row + ordinal,
                          column=template.col_index_by_header[header]).value
    wb.close()
    return v


def test_bulk_save_writes_tags_free_text(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    _client(tmp_path).post(f"/generate/attributes/{job.id}", data={
        "sku__0": "S1", "free__0__0": "saree, cotton, handloom"})
    assert _cell(job.result["filled"], read_template(V13), 0,
                 "tags") == "saree, cotton, handloom"


def test_bulk_save_accepts_tags_that_no_vocabulary_would_allow(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    r = _client(tmp_path).post(f"/generate/attributes/{job.id}", data={
        "sku__0": "S1", "free__0__0": "Salmon Pink"})
    assert "Saved" in r.text
    assert _cell(job.result["filled"], read_template(V13), 0, "tags") == "Salmon Pink"


def test_bulk_save_blank_tags_clears_the_cell(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    client = _client(tmp_path)
    client.post(f"/generate/attributes/{job.id}",
                data={"sku__0": "S1", "free__0__0": "keepme"})
    client.post(f"/generate/attributes/{job.id}",
                data={"sku__0": "S1", "free__0__0": "   "})
    assert _cell(job.result["filled"], read_template(V13), 0, "tags") is None


def test_bulk_save_still_writes_dropdowns_unchanged(tmp_path, monkeypatch):
    """Regression: the extracted helper must not change existing behaviour."""
    job = _job(tmp_path, monkeypatch, skus=("S1", "S2"))
    r = _client(tmp_path).post(f"/generate/attributes/{job.id}", data={
        "sku__0": "S1", "attr__0__5": "Banarasi",
        "sku__1": "S2", "attr__1__5": "Chanderi"})
    assert "Saved" in r.text
    t = read_template(V13)
    assert _cell(job.result["filled"], t, 0, "Type") == "Banarasi"
    assert _cell(job.result["filled"], t, 1, "Type") == "Chanderi"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/web/test_attributes.py -q -k tags`
Expected: FAIL — `tags` is `None`, because `free__` keys are currently ignored.

- [ ] **Step 3: Implement the helper, parser and lock**

In `src/web/routers/attributes.py`:

Add `import threading` at the top with the other stdlib imports, and extend the
`attribute_entry` import to include the two new names:

```python
from src.myntra.attribute_entry import (BRAND_COLOUR_HEADER, AttributeValueError,
                                        SkuMismatchError, attribute_vocab,
                                        derive_brand_colour, user_filled_attributes,
                                        user_filled_freetext, validate_freetext,
                                        validate_submitted, write_attributes)
```

After the `EXPIRED` constant, add:

```python
# Every save is a read-modify-write of the whole workbook (openpyxl load -> save ->
# shared_to_inline). Two panels saved at the same moment would interleave and the
# second write would silently drop the first one's values, with no error shown.
# Serialising all saves costs a single owner nothing.
_WRITE_LOCK = threading.Lock()
```

After `_submitted()`, add:

```python
def _submitted_free(form, columns):
    """Parse free__{ordinal}__{column_index} into {ordinal: {column: raw_value}}."""
    entries = {}
    for key, value in form.items():
        if not key.startswith("free__"):
            continue
        _, ordinal, col_index = key.split("__")
        ordinal, col_index = int(ordinal), int(col_index)
        if 0 <= col_index < len(columns):
            entries.setdefault(ordinal, {})[columns[col_index]] = str(value)
    return entries


def _build_payload(entries, free, template, columns, free_columns):
    """Validate every entry and merge dropdown + derived + free-text values.
    Raises AttributeValueError before anything is written."""
    vocab = attribute_vocab(template, columns)      # built once, not per entry
    has_brand_colour = BRAND_COLOUR_HEADER in template.col_index_by_header
    payload = []
    for ordinal, e in entries.items():
        values = validate_submitted(e["values"], vocab)
        # Derived, never typed — Myntra rejects a null Brand Colour (Remarks).
        if has_brand_colour:
            values[BRAND_COLOUR_HEADER] = derive_brand_colour(values)
        values.update(validate_freetext(free.get(ordinal, {}), free_columns))
        payload.append({"ordinal": ordinal, "sku": e["sku"], "values": values})
    return payload


async def _save_entries(request, job_id):
    """Shared by both save routes. Returns (job, ordinals, payload, error)."""
    job, _job_dir, xlsx, _csv = job_files(job_id)
    template = read_template(TEMPLATE)
    columns, free_columns = user_filled_attributes(), user_filled_freetext()
    form = await request.form()
    entries = _submitted(form, columns)
    free = _submitted_free(form, free_columns)
    ordinals = list(entries)
    try:
        payload = _build_payload(entries, free, template, columns, free_columns)
        with _WRITE_LOCK:
            write_attributes(xlsx, template, payload)
    except (AttributeValueError, SkuMismatchError) as exc:
        return job, ordinals, [], str(exc)
    return job, ordinals, payload, None
```

Now replace the body of the existing `attributes_save` route with a call to the
helper, keeping its decorator, path and response template exactly as they are:

```python
@router.post("/generate/attributes/{job_id}", response_class=HTMLResponse)
async def attributes_save(request: Request, job_id: str):
    get_user(request)
    job, _ordinals, payload, error = await _save_entries(request, job_id)
    if error:
        return _templates().TemplateResponse(
            request, "_attr_saved.html", {"job_id": job.id, "error": error})
    return _templates().TemplateResponse(
        request, "_attr_saved.html", {"job_id": job.id, "saved": len(payload)})
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/web/test_attributes.py -q`
Expected: PASS — the four new tests plus all fifteen pre-existing tests in the file
(the pre-existing ones are the regression gate on the extraction).

- [ ] **Step 5: Commit**

```bash
git add src/web/routers/attributes.py tests/web/test_attributes.py
git commit -m "feat(attributes): shared save helper, free-text parsing, write lock"
```

---

### Task 3: Render the tags input and the 13-column count

**Files:**
- Modify: `src/web/routers/attributes.py` (`_panels`, `attributes_form`)
- Modify: `src/web/templates/_attr_panel.html`
- Modify: `src/web/static/app.css`
- Test: `tests/web/test_attributes.py`

**Interfaces:**
- Consumes: `user_filled_freetext()` (Task 1); `_save_entries()` is untouched here.
- Produces: panel dicts gain `"free": {column: value_or_None}`; the template context gains `free_columns`; `total` becomes `len(columns) + len(free_columns)`; the count span gains `id="attr-count-{ordinal}"`.

- [ ] **Step 1: Write the failing tests**

First extend the `_job` helper so a test can build a sheet that already has tags.
In `tests/web/test_attributes.py`, change the signature and the `MappedRow` line:

```python
def _job(tmp_path, monkeypatch, skus=("S1", "S2"), with_images=True, tags=None):
    """A finished job on disk: built workbook + the Shopify export it came from."""
    ...
    cells = {"vendorSkuCode": s, "brand": "Ijor"}
    rows = []
    for s in skus:
        c = dict(cells, vendorSkuCode=s)
        if tags is not None:
            c["tags"] = tags
        rows.append((MappedRow(sku=s, cells=c), ImageResult(sku=s)))
```

(Keep the rest of `_job` exactly as it is. The default `tags=None` means every
existing test behaves identically.)

Then append the new tests:

```python
def _free_input_value(html, ordinal=0, index=0):
    """The value= of one free-text input. A bare `'value=""' in html` check would
    pass on any empty <option>, so match the field itself."""
    import re
    m = re.search(r'name="free__%d__%d"\s+value="([^"]*)"' % (ordinal, index), html)
    assert m, "no free-text input rendered for that ordinal"
    return m.group(1)


def test_panel_renders_a_tags_input_prefilled_from_the_sheet(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",), tags="saree, cotton")
    r = _client(tmp_path).get(f"/generate/attributes/{job.id}")
    assert _free_input_value(r.text) == "saree, cotton"


def test_tags_input_is_empty_when_the_sheet_has_none(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    r = _client(tmp_path).get(f"/generate/attributes/{job.id}")
    assert _free_input_value(r.text) == ""


def test_filled_count_is_out_of_thirteen_and_counts_tags(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",), tags="festive")
    r = _client(tmp_path).get(f"/generate/attributes/{job.id}")
    assert "1/13 filled" in r.text


def test_count_span_is_addressable_for_out_of_band_updates(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    r = _client(tmp_path).get(f"/generate/attributes/{job.id}")
    assert 'id="attr-count-0"' in r.text
```

Also update the two pre-existing assertions that hard-code twelve:
- `test_existing_values_are_preselected`: `"1/12 filled"` becomes `"1/13 filled"`.
- `test_screen_renders_a_panel_per_sku_with_twelve_selects`: leave
  `r.text.count('name="attr__0__') == 12` as is — there are still twelve *selects*.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/web/test_attributes.py -q -k "tags_input or thirteen or out_of_band"`
Expected: FAIL — `assert 'name="free__0__0"' in r.text` fails; no such field is rendered.

- [ ] **Step 3: Update the router**

In `src/web/routers/attributes.py`, change `_panels` to take the free-text columns
and include them in both the values and the count:

```python
def _panels(xlsx, csv_path, template, columns, free_columns):
    products = {}
    if os.path.exists(csv_path):
        products = {p.sku: p for p in read_products(csv_path)}
    panels = []
    for ordinal, attrs in enumerate(read_filled_rows(xlsx, template)):
        sku = attrs.get("vendorSkuCode") or ""
        p = products.get(sku)
        panels.append({
            "ordinal": ordinal,
            "sku": sku,
            "product_title": p.title if p else "",
            "image": (p.images[0] if p and p.images else None),
            # NOT "values": in Jinja `p.values` would resolve to dict.values (the
            # method), silently breaking the pre-selection comparison.
            "chosen": {c: attrs.get(c) for c in columns},
            "free": {c: attrs.get(c) for c in free_columns},
            "filled": sum(1 for c in list(columns) + list(free_columns)
                          if is_set(attrs.get(c))),
            # Shown read-only: what is in the sheet now, not a guess at what a
            # pending selection would produce.
            "brand_colour": attrs.get(BRAND_COLOUR_HEADER),
            "card": build_card(attrs, columns),
        })
    return panels
```

Note `build_card(attrs, columns)` keeps taking **only** the dropdown columns — the
preview card's spec list must not gain a tags row.

Then update `attributes_form`:

```python
@router.get("/generate/attributes/{job_id}", response_class=HTMLResponse)
def attributes_form(request: Request, job_id: str):
    user = get_user(request)
    job, _job_dir, xlsx, csv_path = job_files(job_id)
    template = read_template(TEMPLATE)
    columns, free_columns = user_filled_attributes(), user_filled_freetext()
    return _templates().TemplateResponse(request, "attributes.html", {
        "user": user, "job_id": job.id, "columns": columns,
        "free_columns": free_columns,
        "vocab": attribute_vocab(template, columns),
        "panels": _panels(xlsx, csv_path, template, columns, free_columns),
        "total": len(columns) + len(free_columns)})
```

- [ ] **Step 4: Update the panel template**

In `src/web/templates/_attr_panel.html`, give the count span an id — replace:

```html
    <span class="hint">{{ p.filled }}/{{ total }} filled</span>
```

with:

```html
    <span class="hint" id="attr-count-{{ p.ordinal }}">{{ p.filled }}/{{ total }} filled</span>
```

Then, immediately after the closing `</div>` of `.attr-body` and before `</details>`,
add the footer:

```html
  <div class="attr-footer">
    {% for column in free_columns %}
    <label class="hint attr-free">{{ column }}
      <input type="text" name="free__{{ p.ordinal }}__{{ loop.index0 }}"
             value="{{ p.free[column] or '' }}" autocomplete="off">
      <span class="hint">free text — pre-filled from Shopify, edit if needed</span>
    </label>
    {% endfor %}
  </div>
```

The input sits **outside** `.attr-grid` on purpose: that div carries
`hx-trigger="change"` for the live preview, and a text input inside it would fire a
pointless preview request on every blur.

- [ ] **Step 5: Add the styling**

Append to `src/web/static/app.css`, after the `.attr-derived` rule on line 73:

```css
.attr-footer{margin-top:12px;padding-top:10px;border-top:1px solid var(--line);
  display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap}
.attr-free{display:flex;flex-direction:column;gap:4px;flex:1;min-width:260px}
.attr-free input{background:var(--panel);color:var(--ink);border:1px solid var(--line);
  border-radius:6px;padding:6px 8px;width:100%}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/web/test_attributes.py -q`
Expected: PASS — all tests in the file, including the two updated count assertions.

- [ ] **Step 7: Commit**

```bash
git add src/web/routers/attributes.py src/web/templates/_attr_panel.html \
        src/web/static/app.css tests/web/test_attributes.py
git commit -m "feat(attributes): editable tags input, 13-column filled count"
```

---

### Task 4: The per-SKU Save button

**Files:**
- Modify: `src/web/routers/attributes.py`
- Create: `src/web/templates/_attr_panel_saved.html`
- Modify: `src/web/templates/_attr_panel.html`
- Modify: `src/web/static/app.css`
- Test: `tests/web/test_attributes.py`

**Interfaces:**
- Consumes: `_save_entries()` (Task 2), `_WRITE_LOCK` (Task 2), `user_filled_attributes()` / `user_filled_freetext()` (Task 1), the `attr-count-{ordinal}` span (Task 3).
- Produces: route `POST /generate/attributes/{job_id}/one`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/web/test_attributes.py`:

```python
def test_saving_one_panel_writes_only_that_row(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1", "S2"))
    client = _client(tmp_path)
    client.post(f"/generate/attributes/{job.id}",
                data={"sku__0": "S1", "attr__0__5": "Banarasi",
                      "sku__1": "S2", "attr__1__5": "Chanderi"})
    # Now save ONLY panel 0, changing it. Panel 1 must survive untouched.
    r = client.post(f"/generate/attributes/{job.id}/one",
                    data={"sku__0": "S1", "attr__0__5": "Chanderi"})
    assert r.status_code == 200
    t = read_template(V13)
    assert _cell(job.result["filled"], t, 0, "Type") == "Chanderi"
    assert _cell(job.result["filled"], t, 1, "Type") == "Chanderi"  # unchanged


def test_one_panel_save_returns_the_refreshed_count_out_of_band(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    r = _client(tmp_path).post(f"/generate/attributes/{job.id}/one", data={
        "sku__0": "S1", "attr__0__5": "Banarasi", "free__0__0": "festive"})
    assert 'id="attr-count-0"' in r.text
    assert 'hx-swap-oob="true"' in r.text
    assert "2/13 filled" in r.text
    assert "Saved" in r.text


def test_one_panel_save_rejects_off_vocab_inline_without_writing(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    r = _client(tmp_path).post(f"/generate/attributes/{job.id}/one",
                               data={"sku__0": "S1", "attr__0__0": "Salmon Pink"})
    assert r.status_code == 200                 # inline error, not a 500
    assert "not one of Myntra" in r.text
    assert 'hx-swap-oob' not in r.text          # count must NOT be updated
    assert _cell(job.result["filled"], read_template(V13), 0,
                 "Prominent Colour") is None


def test_one_panel_save_keeps_dropdowns_alive(tmp_path, monkeypatch):
    import openpyxl
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    wb = openpyxl.load_workbook(job.result["filled"])
    before = len(wb["Sarees"].data_validations.dataValidation)
    wb.close()
    _client(tmp_path).post(f"/generate/attributes/{job.id}/one",
                           data={"sku__0": "S1", "attr__0__7": "Zari"})
    wb = openpyxl.load_workbook(job.result["filled"])
    assert len(wb["Sarees"].data_validations.dataValidation) == before
    wb.close()


def test_one_panel_save_holds_the_write_lock(tmp_path, monkeypatch):
    """The lock must be held across the read-modify-write, not merely to exist."""
    import src.web.routers.attributes as attrs
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    seen = {}
    real = attrs.write_attributes

    def spy(*a, **k):
        seen["locked"] = attrs._WRITE_LOCK.locked()
        return real(*a, **k)

    monkeypatch.setattr(attrs, "write_attributes", spy)
    _client(tmp_path).post(f"/generate/attributes/{job.id}/one",
                           data={"sku__0": "S1", "attr__0__7": "Zari"})
    assert seen["locked"] is True


def test_one_panel_save_on_expired_job_says_session_expired(tmp_path, monkeypatch):
    monkeypatch.setattr(gen, "RUNTIME", str(tmp_path / "runtime"))
    r = _client(tmp_path).post("/generate/attributes/" + "0" * 32 + "/one",
                               data={"sku__0": "S1"})
    assert r.status_code == 404


def test_panel_has_a_save_button_that_posts_only_its_own_fields(tmp_path, monkeypatch):
    import re
    job = _job(tmp_path, monkeypatch, skus=("S1", "S2"))
    r = _client(tmp_path).get(f"/generate/attributes/{job.id}")
    buttons = re.findall(r"<button[^>]*hx-post=\"/generate/attributes/[^\"]+/one\"[^>]*>",
                         r.text)
    assert len(buttons) == 2
    # Load-bearing: a default submit button would post every panel at once. Counting
    # type="button" across the whole page would also match unrelated chrome.
    assert all('type="button"' in b for b in buttons)
    assert all('hx-include="closest .attr-panel"' in b for b in buttons)
    assert 'id="attr-save-0"' in r.text and 'id="attr-save-1"' in r.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/web/test_attributes.py -q -k "one_panel or save_button"`
Expected: FAIL — `404 Not Found` for `/one`, and the button assertions fail.

- [ ] **Step 3: Add the route**

In `src/web/routers/attributes.py`, add `is_set` to the `preview` import:

```python
from src.myntra.preview import build_card, is_set, read_filled_rows
```

(`is_set` is already imported — confirm and leave it alone if so.)

Add the route after `attributes_save`:

```python
@router.post("/generate/attributes/{job_id}/one", response_class=HTMLResponse)
async def attributes_save_one(request: Request, job_id: str):
    """Save a single panel. Same validation and writing as the bulk route; only the
    rendered response differs — a compact inline result plus an out-of-band refresh
    of that panel's filled count."""
    get_user(request)
    job, ordinals, payload, error = await _save_entries(request, job_id)
    ordinal = ordinals[0] if ordinals else 0
    if error:
        return _templates().TemplateResponse(
            request, "_attr_panel_saved.html",
            {"ordinal": ordinal, "error": error})
    columns, free_columns = user_filled_attributes(), user_filled_freetext()
    values = payload[0]["values"] if payload else {}
    filled = sum(1 for c in list(columns) + list(free_columns)
                 if is_set(values.get(c)))
    return _templates().TemplateResponse(
        request, "_attr_panel_saved.html",
        {"ordinal": ordinal, "filled": filled,
         "total": len(columns) + len(free_columns)})
```

- [ ] **Step 4: Create the compact partial**

Create `src/web/templates/_attr_panel_saved.html`:

```html
{% if error %}
  <span class="flag">⚠ Not saved — {{ error }}</span>
{% else %}
  <span class="ok">✅ Saved · {{ filled }}/{{ total }} filled</span>
  <span class="hint" id="attr-count-{{ ordinal }}" hx-swap-oob="true">{{ filled }}/{{ total }} filled</span>
{% endif %}
```

The second span is the out-of-band swap: htmx lifts any top-level element carrying
`hx-swap-oob="true"` out of the response and replaces the element with that id
elsewhere on the page — here the count in the panel summary. On the error branch it
is deliberately absent, so a failed save leaves the old count showing.

- [ ] **Step 5: Add the button to the panel**

In `src/web/templates/_attr_panel.html`, inside `.attr-footer` and after the
`{% endfor %}` of the free-text loop, add:

```html
    <button class="btn" type="button"
            hx-post="/generate/attributes/{{ job_id }}/one"
            hx-include="closest .attr-panel"
            hx-target="#attr-save-{{ p.ordinal }}"
            hx-swap="innerHTML"
            hx-disabled-elt="this">Save this SKU</button>
    <span class="attr-save-out" id="attr-save-{{ p.ordinal }}"></span>
```

`type="button"` is load-bearing — without it this submits the whole outer form.

- [ ] **Step 6: Style the status span**

Append to `src/web/static/app.css`:

```css
.attr-save-out{min-height:1em}
.attr-footer button[disabled]{opacity:.6;cursor:progress}
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m pytest tests/web/test_attributes.py -q`
Expected: PASS — every test in the file.

- [ ] **Step 8: Commit**

```bash
git add src/web/routers/attributes.py src/web/templates/_attr_panel_saved.html \
        src/web/templates/_attr_panel.html src/web/static/app.css \
        tests/web/test_attributes.py
git commit -m "feat(attributes): per-SKU Save button with inline result and oob count"
```

---

### Task 5: Owner documentation and full-suite verification

**Files:**
- Modify: `docs/APP-FEATURES-GUIDE.md`
- Test: the whole suite

**Interfaces:**
- Consumes: everything from Tasks 1–4.
- Produces: no code.

- [ ] **Step 1: Find the attribute-screen section**

Run: `grep -n -i "attribute" docs/APP-FEATURES-GUIDE.md`

Read the surrounding section so the new text matches its voice — this guide is
written for a reader with no technical background.

- [ ] **Step 2: Document both changes**

Add to that section, adapting the heading level to match its neighbours:

```markdown
**Saving one product at a time.** Each product box has its own **Save this SKU**
button. It saves just that product, so you can fill two or three, save them, and
come back to the rest later without losing anything. The counter at the top of the
box ("5/13 filled") updates as soon as the save succeeds. The **Save attributes**
button at the bottom still saves every product at once — use whichever suits you.

**One caveat worth knowing:** saved work lives with that batch while the app is
running. If the app is restarted or updated, the batch is cleared and you will be
asked to upload the file again. Finish a batch in one day where you can.

**The tags box.** Every product has a **tags** box, filled in automatically from the
Tags field on the product in Shopify. Unlike the dropdowns, you can type anything
you like here — Myntra does not restrict it. Leave it alone and your Shopify tags
are used as-is; clear it and the product goes up with no tags.
```

- [ ] **Step 3: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS. Baseline before this work was **241 passed**; this plan adds 21
tests (6 + 4 + 4 + 7), so expect **262 passed**.

**This takes about 19 minutes** — the attribute tests each load the ~973 KB template
through openpyxl. Do not assume a hang.

- [ ] **Step 4: Check the screen in a real browser**

```bash
LEDGER_LOCAL_PATH=src/web/runtime/ledger.json \
HSN_LOCAL_PATH=src/web/runtime/hsn_kb.json \
SKU_REGISTRY_LOCAL_PATH=src/web/runtime/sku_registry.json \
AUTH_DISABLED=1 uvicorn src.web.main:app --reload
```

PowerShell equivalent:

```powershell
$env:LEDGER_LOCAL_PATH="src/web/runtime/ledger.json"; $env:HSN_LOCAL_PATH="src/web/runtime/hsn_kb.json"; $env:SKU_REGISTRY_LOCAL_PATH="src/web/runtime/sku_registry.json"; $env:AUTH_DISABLED="1"; uvicorn src.web.main:app --reload
```

Generate a small batch, open the attribute screen, and confirm by eye:
1. Each expanded panel shows a tags box with a value and a **Save this SKU** button.
2. Saving one panel shows `✅ Saved` beside the button and updates that panel's
   count — and does **not** collapse the panel or scroll the page.
3. Saving panel 1 does not clear anything you picked in panel 2.
4. Reloading the screen brings back everything you saved, tags included.
5. Downloading the xlsx still opens in Excel with working dropdowns.

- [ ] **Step 5: Commit**

```bash
git add docs/APP-FEATURES-GUIDE.md
git commit -m "docs(guide): per-SKU save and the tags box"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §3.1 placement / footer strip | 3 (footer), 4 (button) |
| §3.2 htmx mechanism, `type="button"`, `hx-disabled-elt` | 4 |
| §3.3 extract `_save_entries`, two routes, no reuse of `_attr_saved.html` | 2, 4 |
| §3.4 write lock | 2 (implementation), 4 (test) |
| §3.5 stale count / oob | 3 (id'd span), 4 (oob swap) |
| §4.1–4.2 free text, separate config list | 1 |
| §4.3 pre-filled, any value, blank clears, `free__` keys, counted in tally | 1, 2, 3 |
| §4.4 `validate_freetext`, `validate_submitted` untouched | 1 |
| §5 data flow | 2 |
| §6 error handling (4 cases) | 2, 4 |
| §7 tests 1–9 | 2, 3, 4 |
| §8 limitation documented for the owner | 5 |
| §9 files touched | all |

No gaps.

**Placeholder scan:** none — every step carries the literal code, command, or copy.

**Type consistency:** `_save_entries` returns `(job, ordinals, payload, error)` and
is destructured that way in both routes. `_submitted_free` returns
`{ordinal: {column: str}}`, consumed by `_build_payload` as `free.get(ordinal, {})`.
`validate_freetext(values, columns)` argument order matches both call site and tests.
`_panels(xlsx, csv_path, template, columns, free_columns)` matches its one caller.
`user_filled_freetext()` returns a list in every task. The `_cell` test helper is
defined once in Task 2 and reused in Tasks 3–4.
