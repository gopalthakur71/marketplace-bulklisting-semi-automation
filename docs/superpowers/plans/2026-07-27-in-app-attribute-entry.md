# In-App Attribute Entry — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the owner fill the 12 name-driving Myntra attributes inside the app — a per-SKU accordion with the product photo, vocabulary-only dropdowns, and a live listing preview — writing the choices into the already-built workbook without losing its Excel dropdowns.

**Architecture:** Additive surface on the existing Generate flow. After a build finishes, a new screen (`/generate/attributes/{job_id}`) reads the built `myntra_filled.xlsx` and the job's Shopify export from the job directory, renders one `<details>` panel per SKU with 12 `<select>`s populated strictly from the V13 template's `vocab_by_header`, and re-renders a preview card server-side on every change via htmx (calling the *existing* `src/myntra/preview.py` reconstruction, so no logic is duplicated in JS). Save writes the 12 cells per row with openpyxl and then re-applies `fill.py`'s shared-string→inline conversion, which a plain openpyxl save would otherwise undo.

**Tech Stack:** Python 3.12, openpyxl, pandas, FastAPI + htmx, Jinja2, pytest.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-07-26-in-app-attribute-entry-design.md` — read it before starting.
- **V13 template path (exact):** `templates/myntra/Myntra-Sku-Template-2026-07-24.xlsx` (referred to below as `V13`). Never hardcode it in `src/` — use `src.myntra.pipeline.DEFAULT_TEMPLATE_NAME`.
- **The 12 user-filled attribute headers (exact strings, this order):** `Prominent Colour`, `Second Prominent Colour`, `Third Prominent Colour`, `Saree Fabric`, `Blouse Fabric`, `Type`, `Ornamentation`, `Border`, `Pattern`, `Print or Pattern Type`, `Wash Care`, `Usage`
- **Entry sheet name:** `Sarees`; header row = 3; first data row = 4 (never hardcode — use `TemplateInfo.first_data_row`).
- **Dropdown options come ONLY from the template's `vocab_by_header`.** Never invent a value, never append `NA` to a list that does not have it. The placeholder option is UI-only with `value=""` and must never be written to a cell.
- **"Set" means:** not None, not empty after strip, not the literal `NA` (case-insensitive) — use `src.myntra.preview.is_set`.
- **The existing `/preview` upload flow must keep working unchanged.** This feature is additive.
- **Commit trailer:** end every commit message with `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.
- **Run tests with:** `python -m pytest` (from repo root, venv active). Full suite is currently **190 passed** — it must stay green.
- **Do not** modify brand, HSN, pricing, image, styleGroupId, identity, or Product Details logic.

## File Structure

| File | Responsibility |
|---|---|
| `config/myntra/rules.yaml` | `user_filled_attributes` grows from 9 to 12 entries (single source of truth). |
| `src/myntra/attribute_entry.py` *(new)* | Pure logic: load the column list, build `{column: [vocab]}`, validate submitted values, write them into a built workbook. No FastAPI imports. |
| `src/myntra/fill.py` | `_sheet_xml_name` / `_shared_to_inline` become public so the save path can re-apply the inline-string conversion. Behaviour unchanged. |
| `src/myntra/preview.py` | Gains `build_card()` — the one place a preview card dict is built. |
| `src/web/routers/attributes.py` *(new)* | Three routes: screen, live-preview fragment, save. |
| `src/web/routers/preview.py` | Uses `build_card()` and the shared column-list loader instead of its own copies. |
| `src/web/templates/attributes.html` *(new)* | The page: accordion of panels + one Save button. |
| `src/web/templates/_attr_panel.html` *(new)* | One SKU: photo, 12 selects, live preview slot. |
| `src/web/templates/_preview_card.html` *(new)* | The card markup, extracted from `_preview.html`; used by both preview surfaces. |
| `src/web/templates/_attr_saved.html` *(new)* | Post-save confirmation (or error) panel. |
| `src/web/templates/_result.html` | Gains the "Fill attributes" action. |
| `src/web/main.py` | Registers the new router. |

---

### Task 1: Widen the user-filled attribute list from 9 to 12

Add `Second Prominent Colour`, `Third Prominent Colour` and `Usage` to the one list that drives the mapper (blank these columns), `/preview` (show them as specs, flag them when missing), and — from Task 5 — the new screen. All three exist in V13 with real vocabularies (53, 53, 9), verified 2026-07-27.

**Files:**
- Modify: `config/myntra/rules.yaml:28-37`
- Modify: `src/web/routers/preview.py:16-18`
- Test: `tests/test_config_loads.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `rules["user_filled_attributes"]` is a 12-item `list[str]` in the Global-Constraints order. Everything downstream reads this list; nothing hardcodes 9.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config_loads.py`:

```python
def test_user_filled_attributes_are_the_twelve_with_v13_vocab():
    import yaml
    from src.myntra.template_reader import read_template
    from src.myntra.pipeline import DEFAULT_TEMPLATE_NAME, _resolve

    rules = yaml.safe_load(open("config/myntra/rules.yaml", encoding="utf-8"))
    cols = rules["user_filled_attributes"]
    assert cols == [
        "Prominent Colour", "Second Prominent Colour", "Third Prominent Colour",
        "Saree Fabric", "Blouse Fabric", "Type", "Ornamentation", "Border",
        "Pattern", "Print or Pattern Type", "Wash Care", "Usage"]

    t = read_template(_resolve(DEFAULT_TEMPLATE_NAME, "templates/myntra"))
    for c in cols:
        assert c in t.col_index_by_header, f"{c} missing from the template header row"
        assert t.vocab_by_header.get(c), f"{c} has no dropdown vocabulary"
    # none of the 12 may also be a forced constant (that would fight the blanking)
    consts = yaml.safe_load(open("config/myntra/constants.yaml", encoding="utf-8"))
    assert not (set(cols) & set(consts)), "a user-filled column is also a constant"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config_loads.py::test_user_filled_attributes_are_the_twelve_with_v13_vocab -v`
Expected: FAIL — the assertion on the list mismatches (9 entries, different order).

- [ ] **Step 3: Write minimal implementation**

In `config/myntra/rules.yaml`, replace the whole `user_filled_attributes:` block with:

```yaml
# Attributes the user fills by hand — either in the app's "Fill attributes" screen
# or in Excel via the template dropdowns. The mapper leaves these BLANK (the app
# never guesses them). The preview and the attribute-entry screen both read this
# list to know which columns are user-owned.
user_filled_attributes:
  - Prominent Colour
  - Second Prominent Colour
  - Third Prominent Colour
  - Saree Fabric
  - Blouse Fabric
  - Type
  - Ornamentation
  - Border
  - Pattern
  - Print or Pattern Type
  - Wash Care
  - Usage
```

In `src/web/routers/preview.py`, update the fallback list (used only when the YAML key is absent) to match:

```python
_FALLBACK_USER_FILLED = [
    "Prominent Colour", "Second Prominent Colour", "Third Prominent Colour",
    "Saree Fabric", "Blouse Fabric", "Type", "Ornamentation", "Border",
    "Pattern", "Print or Pattern Type", "Wash Care", "Usage"]
```

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS. If a test asserts an exact 9-item spec table or an exact "Not filled:" string from `/preview` (look in `tests/web/test_preview.py` and `tests/test_preview.py`), update the expectation to the 12 columns — widening the list is the intended change. Do **not** narrow the list to make a test pass.

- [ ] **Step 5: Commit**

```bash
git add config/myntra/rules.yaml src/web/routers/preview.py tests/
git commit -m "feat(config): add Second/Third Prominent Colour and Usage to the user-filled attributes

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `attribute_entry` — column list, vocabulary, and value validation

The pure half of the feature: what the dropdowns offer and what may be written. No FastAPI, no file writing yet (that is Task 3).

**Files:**
- Create: `src/myntra/attribute_entry.py`
- Test: `tests/test_attribute_entry.py`

**Interfaces:**
- Consumes: `TemplateInfo.vocab_by_header` (from `read_template`).
- Produces:
  - `user_filled_attributes(config_dir="config/myntra") -> list[str]`
  - `attribute_vocab(template, columns) -> dict[str, list[str]]`
  - `AttributeValueError(Exception)`
  - `validate_submitted(values: dict[str, str], vocab: dict[str, list[str]]) -> dict[str, str | None]` — blank/whitespace becomes `None`; any non-blank value must be an exact member of that column's vocabulary or the call raises.

- [ ] **Step 1: Write the failing test**

Create `tests/test_attribute_entry.py`:

```python
import pytest

from src.myntra.attribute_entry import (user_filled_attributes, attribute_vocab,
                                        validate_submitted, AttributeValueError)
from src.myntra.template_reader import read_template

V13 = "templates/myntra/Myntra-Sku-Template-2026-07-24.xlsx"


def test_user_filled_attributes_reads_the_twelve_from_rules():
    cols = user_filled_attributes()
    assert len(cols) == 12
    assert cols[0] == "Prominent Colour"
    assert "Usage" in cols


def test_attribute_vocab_only_from_template_and_na_where_the_sheet_has_it():
    t = read_template(V13)
    vocab = attribute_vocab(t, user_filled_attributes())
    assert set(vocab) == set(user_filled_attributes())
    # exact sizes read off V13 (2026-07-27)
    assert len(vocab["Prominent Colour"]) == 53
    assert len(vocab["Usage"]) == 9
    assert len(vocab["Border"]) == 10
    # NA is offered only where Myntra actually lists it — never injected
    for col in ["Prominent Colour", "Second Prominent Colour", "Third Prominent Colour",
                "Blouse Fabric", "Type", "Ornamentation", "Usage"]:
        assert any(v.strip().upper() == "NA" for v in vocab[col]), col
    for col in ["Saree Fabric", "Border", "Pattern", "Print or Pattern Type", "Wash Care"]:
        assert not any(v.strip().upper() == "NA" for v in vocab[col]), col


def test_validate_submitted_blank_becomes_none():
    vocab = {"Border": ["Zari", "Solid"], "Pattern": ["Solid"]}
    out = validate_submitted({"Border": "", "Pattern": "   "}, vocab)
    assert out == {"Border": None, "Pattern": None}


def test_validate_submitted_passes_exact_vocab_values():
    vocab = {"Border": ["Zari", "Solid"]}
    assert validate_submitted({"Border": "Zari"}, vocab) == {"Border": "Zari"}


def test_validate_submitted_rejects_off_vocab_value():
    vocab = {"Border": ["Zari", "Solid"]}
    with pytest.raises(AttributeValueError) as exc:
        validate_submitted({"Border": "Salmon Pink"}, vocab)
    assert "Border" in str(exc.value)
    assert "Salmon Pink" in str(exc.value)


def test_validate_submitted_rejects_unknown_column():
    with pytest.raises(AttributeValueError):
        validate_submitted({"Nonexistent Column": "x"}, {"Border": ["Zari"]})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_attribute_entry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.myntra.attribute_entry'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/myntra/attribute_entry.py`:

```python
"""The attributes the seller decides by hand (colour, fabric, type, border, ...).

The pipeline never guesses these; they are offered as dropdowns whose options come
strictly from the Myntra template's own vocabulary, and are written into the built
workbook only after an exact-membership check."""
import os

import yaml

CONFIG_DIR = os.path.join("config", "myntra")

# Used only if rules.yaml somehow lacks the key; the YAML is the source of truth.
FALLBACK_USER_FILLED = [
    "Prominent Colour", "Second Prominent Colour", "Third Prominent Colour",
    "Saree Fabric", "Blouse Fabric", "Type", "Ornamentation", "Border",
    "Pattern", "Print or Pattern Type", "Wash Care", "Usage"]


class AttributeValueError(Exception):
    """A submitted value is not an exact member of that column's Myntra vocabulary."""


def user_filled_attributes(config_dir=CONFIG_DIR):
    with open(os.path.join(config_dir, "rules.yaml"), encoding="utf-8") as fh:
        rules = yaml.safe_load(fh) or {}
    return rules.get("user_filled_attributes") or list(FALLBACK_USER_FILLED)


def attribute_vocab(template, columns):
    """{column: [accepted values]} straight from the template. Nothing is added."""
    return {c: list(template.vocab_by_header.get(c) or []) for c in columns}


def validate_submitted(values, vocab):
    """Blank -> None (clears the cell). Non-blank must be exactly in vocab, else raise."""
    out = {}
    for column, value in values.items():
        if column not in vocab:
            raise AttributeValueError(f"Unknown attribute column: {column}")
        if value is None or str(value).strip() == "":
            out[column] = None
            continue
        v = str(value).strip()
        if v not in vocab[column]:
            raise AttributeValueError(
                f"{column}: '{v}' is not one of Myntra's accepted values")
        out[column] = v
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_attribute_entry.py -v`
Expected: PASS (6 tests).

> If a vocab-size assertion fails, the template file changed — do not edit the assertion blindly; re-read the sizes from the template and confirm with the owner before changing the spec's §3 table.

- [ ] **Step 5: Commit**

```bash
git add src/myntra/attribute_entry.py tests/test_attribute_entry.py
git commit -m "feat(attributes): vocabulary loader and strict value validation

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Write attributes into the built workbook (dropdowns + inline strings survive)

The riskiest part. `fill_template` does **not** stop at `wb.save()` — it then rewrites the `Sarees` sheet XML converting shared strings to inline strings, because Myntra's upload parser cannot resolve shared strings. A plain openpyxl re-save silently re-introduces shared strings and produces a file Myntra rejects. So the save path must re-apply that conversion, and both invariants (dropdowns, inline strings) get locked by tests.

**Files:**
- Modify: `src/myntra/fill.py:32,54,160-161,166` (rename two private helpers to public; no behaviour change)
- Modify: `src/myntra/attribute_entry.py` (add `write_attributes`, `SkuMismatchError`)
- Test: `tests/test_attribute_entry.py`

**Interfaces:**
- Consumes: `fill.sheet_xml_name(xlsx_path, sheet_title) -> str`, `fill.shared_to_inline(out_path, sheet_xml) -> None`, `TemplateInfo`.
- Produces:
  - `SkuMismatchError(Exception)`
  - `write_attributes(xlsx_path, template, entries) -> int` where `entries` is a `list[dict]` of `{"ordinal": int, "sku": str, "values": dict[str, str | None]}`. Returns the number of rows written. Validates **every** entry (row exists, SKU matches) before writing anything; `None` blanks the cell.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_attribute_entry.py`:

```python
import warnings
import zipfile

import openpyxl

from src.core.models import MappedRow, ImageResult
from src.myntra.fill import fill_template
from src.myntra.attribute_entry import write_attributes, SkuMismatchError


def _built(tmp_path, skus=("S1", "S2")):
    """A freshly built workbook: identity columns filled, the 12 attrs blank."""
    warnings.filterwarnings("ignore")
    t = read_template(V13)
    rows = [(MappedRow(sku=s, cells={"vendorSkuCode": s, "brand": "Ijor"}),
             ImageResult(sku=s)) for s in skus]
    out = tmp_path / "myntra_filled.xlsx"
    fill_template(V13, t, rows, str(out))
    return t, str(out)


def _validation_count(path):
    warnings.filterwarnings("ignore")
    wb = openpyxl.load_workbook(path)
    n = len(wb["Sarees"].data_validations.dataValidation)
    wb.close()
    return n


def _sarees_xml(path):
    from src.myntra.fill import sheet_xml_name
    with zipfile.ZipFile(path) as z:
        return z.read(sheet_xml_name(path, "Sarees")).decode("utf-8")


def _cell(path, template, row_ordinal, header):
    warnings.filterwarnings("ignore")
    wb = openpyxl.load_workbook(path, data_only=True)
    v = wb["Sarees"].cell(row=template.first_data_row + row_ordinal,
                          column=template.col_index_by_header[header]).value
    wb.close()
    return v


def test_write_attributes_writes_the_right_row_and_leaves_others_blank(tmp_path):
    t, path = _built(tmp_path)
    n = write_attributes(path, t, [
        {"ordinal": 1, "sku": "S2",
         "values": {"Border": "Zari", "Type": "Banarasi", "Pattern": None}}])
    assert n == 1
    assert _cell(path, t, 1, "Border") == "Zari"
    assert _cell(path, t, 1, "Type") == "Banarasi"
    assert _cell(path, t, 1, "Pattern") is None       # explicit blank stays blank
    assert _cell(path, t, 0, "Border") is None        # the other SKU is untouched
    assert _cell(path, t, 1, "vendorSkuCode") == "S2"  # identity columns preserved


def test_write_attributes_is_idempotent_and_can_clear(tmp_path):
    t, path = _built(tmp_path)
    write_attributes(path, t, [{"ordinal": 0, "sku": "S1", "values": {"Border": "Zari"}}])
    write_attributes(path, t, [{"ordinal": 0, "sku": "S1", "values": {"Border": "Solid"}}])
    assert _cell(path, t, 0, "Border") == "Solid"
    write_attributes(path, t, [{"ordinal": 0, "sku": "S1", "values": {"Border": None}}])
    assert _cell(path, t, 0, "Border") is None


def test_write_attributes_preserves_dropdowns(tmp_path):
    """KEY INVARIANT: the downloaded file must still have live Excel dropdowns."""
    t, path = _built(tmp_path)
    before = _validation_count(path)
    assert before > 0
    write_attributes(path, t, [{"ordinal": 0, "sku": "S1", "values": {"Border": "Zari"}}])
    assert _validation_count(path) == before


def test_write_attributes_keeps_strings_inline(tmp_path):
    """Myntra's parser cannot resolve shared strings; a bare openpyxl save undoes
    fill.py's inline conversion, so the save path must re-apply it."""
    t, path = _built(tmp_path)
    write_attributes(path, t, [{"ordinal": 0, "sku": "S1", "values": {"Border": "Zari"}}])
    xml = _sarees_xml(path)
    assert 't="s"' not in xml
    assert "Zari" in xml


def test_write_attributes_rejects_sku_mismatch_without_writing(tmp_path):
    t, path = _built(tmp_path)
    with pytest.raises(SkuMismatchError):
        write_attributes(path, t, [
            {"ordinal": 0, "sku": "S1", "values": {"Border": "Zari"}},
            {"ordinal": 1, "sku": "WRONG", "values": {"Border": "Solid"}}])
    assert _cell(path, t, 0, "Border") is None   # nothing written at all
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_attribute_entry.py -v`
Expected: FAIL — `ImportError: cannot import name 'write_attributes'` (and `sheet_xml_name`).

- [ ] **Step 3: Write minimal implementation**

In `src/myntra/fill.py`, make the two helpers public. Rename `_sheet_xml_name` → `sheet_xml_name` (line 32) and `_shared_to_inline` → `shared_to_inline` (line 54). **Also rename `shared_to_inline`'s second parameter** from `sheet_xml_name` to `sheet_xml` so it no longer shadows the function, updating its one use inside the body:

```python
def sheet_xml_name(xlsx_path, sheet_title):
    wb = openpyxl.load_workbook(xlsx_path, read_only=True)
    idx = wb.sheetnames.index(sheet_title)
    wb.close()
    return f"xl/worksheets/sheet{idx + 1}.xml"


def shared_to_inline(out_path, sheet_xml):
    """Convert shared-string cells (t="s") in one sheet to inline strings
    (t="inlineStr"). Myntra's upload parser does not resolve shared strings, so
    text — including the column headers — must be embedded inline."""
```

inside its body, the zip loop condition becomes `if item.filename == sheet_xml:`.

Then update the three call sites at the bottom of `fill_template` (lines 160-161 and 166) to the new names:

```python
    sarees_xml = sheet_xml_name(template_path, SHEET_SAREES_NAME)
    shared_to_inline(out_path, sarees_xml)

    # Re-inject the dropdown validations openpyxl dropped on save (manual-edit copy
    # only — breaks Myntra's upload parser, so off by default).
    if preserve_dropdowns:
        sheet_xml = sheet_xml_name(template_path, SHEET_SAREES_NAME)
```

In `src/myntra/attribute_entry.py`, add at the top:

```python
import warnings

import openpyxl

from src.myntra.fill import SHEET_SAREES_NAME, sheet_xml_name, shared_to_inline
```

and append:

```python
class SkuMismatchError(Exception):
    """A submitted row ordinal does not carry the SKU the form claimed for it."""


def write_attributes(xlsx_path, template, entries):
    """Write the user-chosen attributes into an already-built workbook, in place.

    entries: [{"ordinal": int, "sku": str, "values": {header: value_or_None}}]
    A None value blanks the cell, so re-saving is idempotent and a cleared dropdown
    really clears. Every entry is verified before anything is written."""
    warnings.filterwarnings("ignore")
    sku_col = template.col_index_by_header.get("vendorSkuCode")
    if sku_col is None:
        raise SkuMismatchError("Template has no vendorSkuCode column")

    wb = openpyxl.load_workbook(xlsx_path)
    try:
        ws = wb[SHEET_SAREES_NAME]
        # Pass 1: verify every target row before touching any cell.
        for e in entries:
            r = template.first_data_row + int(e["ordinal"])
            actual = ws.cell(row=r, column=sku_col).value
            actual = "" if actual is None else str(actual).strip()
            if actual != str(e["sku"]).strip():
                raise SkuMismatchError(
                    f"Row {r} holds SKU '{actual}', not '{e['sku']}' — "
                    "the sheet changed since the screen was opened")
        # Pass 2: write.
        for e in entries:
            r = template.first_data_row + int(e["ordinal"])
            for header, value in e["values"].items():
                col = template.col_index_by_header.get(header)
                if col is None:
                    continue
                ws.cell(row=r, column=col).value = value
        wb.save(xlsx_path)
    finally:
        wb.close()

    # openpyxl re-writes text as shared strings; Myntra's parser cannot resolve
    # them, so re-apply fill.py's inline conversion (see fill.fill_template).
    shared_to_inline(xlsx_path, sheet_xml_name(xlsx_path, SHEET_SAREES_NAME))
    return len(entries)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_attribute_entry.py -v`
Expected: PASS (11 tests).

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS — `tests/test_fill.py`, `tests/test_inline_strings.py` and `tests/test_dropdowns.py` must be unaffected by the rename (they call `fill_template`, not the private helpers).

- [ ] **Step 6: Commit**

```bash
git add src/myntra/fill.py src/myntra/attribute_entry.py tests/test_attribute_entry.py
git commit -m "feat(attributes): write chosen attributes into the built workbook

Keeps the Excel dropdowns and re-applies the shared-string -> inline conversion
that Myntra's upload parser requires and a bare openpyxl save would undo.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: One card builder, one card template

Both preview surfaces must render identical cards. Extract the card dict into `preview.build_card()` and the markup into `_preview_card.html`, then make `/preview` use both. Pure refactor — `/preview`'s existing tests must pass untouched.

**Files:**
- Modify: `src/myntra/preview.py` (add `build_card`)
- Create: `src/web/templates/_preview_card.html`
- Modify: `src/web/templates/_preview.html`
- Modify: `src/web/routers/preview.py:52-60`
- Test: `tests/test_preview.py`

**Interfaces:**
- Consumes: `reconstruct_title`, `reconstruct_design_details`, `missing_attributes`.
- Produces: `build_card(attrs: dict, user_filled: list[str]) -> dict` with keys `sku`, `title`, `design_details`, `specs` (list of `(header, value)` pairs), `missing`. `_preview_card.html` renders a card from a context variable named `c`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_preview.py`:

```python
def test_build_card_shapes_one_card():
    from src.myntra.preview import build_card
    attrs = {"vendorSkuCode": "S1", "Prominent Colour": "Blue", "Type": "Banarasi",
             "Saree Fabric": "Pure Silk", "Border": "NA"}
    uf = ["Prominent Colour", "Type", "Saree Fabric", "Border"]
    card = build_card(attrs, uf)
    assert card["sku"] == "S1"
    assert card["title"] == "Pure Silk Banarasi Saree"
    assert card["design_details"][0] == "Blue Banarasi sarees"
    assert card["specs"] == [("Prominent Colour", "Blue"), ("Type", "Banarasi"),
                             ("Saree Fabric", "Pure Silk"), ("Border", "NA")]
    assert card["missing"] == ["Border"]          # NA counts as not filled


def test_build_card_falls_back_to_skucode():
    from src.myntra.preview import build_card
    assert build_card({"SKUCode": "S9"}, [])["sku"] == "S9"
    assert build_card({}, [])["sku"] == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_preview.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_card'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/myntra/preview.py`:

```python
def build_card(attrs, user_filled):
    """The one place a preview card is assembled, so the upload preview and the
    in-app live preview can never drift apart."""
    return {
        "sku": attrs.get("vendorSkuCode") or attrs.get("SKUCode") or "",
        "title": reconstruct_title(attrs),
        "design_details": reconstruct_design_details(attrs),
        "specs": [(h, attrs.get(h)) for h in user_filled],
        "missing": missing_attributes(attrs, user_filled),
    }
```

Create `src/web/templates/_preview_card.html` with the card markup moved verbatim out of `_preview.html`:

```html
<div class="card">
  <strong class="mono">{{ c.sku }}</strong>
  {% if c.missing %}
    <p class="flag mono">⚠ Not filled: {{ c.missing | join(", ") }}</p>
  {% endif %}
  <div class="hint">Title (approximate)</div>
  <p><strong>{{ c.title }}</strong></p>
  <div class="hint">Design Details (approximate)</div>
  <ul>{% for line in c.design_details %}<li>{{ line }}</li>{% endfor %}</ul>
  <div class="hint">Specifications (exact)</div>
  <table>
    {% for header, value in c.specs %}
    <tr><td class="mono">{{ header }}</td><td>{{ value or "—" }}</td></tr>
    {% endfor %}
  </table>
</div>
```

Replace `src/web/templates/_preview.html` with:

```html
<div class="panel">
  <h3 class="ok">Preview — {{ cards | length }} product(s)</h3>
  <p class="flag mono"><strong>⚠ Title &amp; Design Details are auto-generated by Myntra
    from the attributes — this is our best reconstruction, not guaranteed word-for-word.
    Specifications are exact.</strong></p>
  {% for c in cards %}{% include "_preview_card.html" %}{% endfor %}
</div>
```

In `src/web/routers/preview.py`, import `build_card` and replace the card comprehension in `preview_submit`:

```python
from src.myntra.preview import (read_filled_rows, build_card)
```

```python
    user_filled = _user_filled()
    cards = [build_card(attrs, user_filled) for attrs in rows]
```

(`reconstruct_title`, `reconstruct_design_details` and `missing_attributes` are no longer used in this module — drop them from the import. Keep `_user_filled` for now; Task 5 replaces it.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_preview.py tests/web/test_preview.py -v`
Expected: PASS — including the untouched `/preview` web tests, which prove the refactor changed no output.

- [ ] **Step 5: Commit**

```bash
git add src/myntra/preview.py src/web/routers/preview.py src/web/templates/_preview.html src/web/templates/_preview_card.html tests/test_preview.py
git commit -m "refactor(preview): single build_card + shared card partial

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: The "Fill attributes" screen (GET)

Render one collapsed panel per SKU: photo from the job's Shopify export, 12 vocabulary-only dropdowns pre-selected with whatever is already in the workbook, and the current preview card.

**Files:**
- Create: `src/web/routers/attributes.py`
- Create: `src/web/templates/attributes.html`
- Create: `src/web/templates/_attr_panel.html`
- Modify: `src/web/main.py:39-44`
- Modify: `src/web/templates/_result.html:10`
- Test: `tests/web/test_attributes.py`

**Interfaces:**
- Consumes: `user_filled_attributes`, `attribute_vocab` (Task 2); `read_filled_rows`, `build_card`, `is_set` (Tasks 4 / existing); `read_products`; `src.web.routers.generate.RUNTIME` and `_safe_job_id`; `store` from `src.web.jobs`.
- Produces: route `GET /generate/attributes/{job_id}`; module-level helper `job_files(job_id) -> (job, job_dir, xlsx_path, csv_path)` raising `HTTPException(404, "session expired, please re-upload")`; form field names `attr__{ordinal}__{column_index}` and `sku__{ordinal}` (relied on by Task 6).

- [ ] **Step 1: Write the failing test**

Create `tests/web/test_attributes.py`:

```python
import os
import warnings

from fastapi.testclient import TestClient

from src.core.models import MappedRow, ImageResult
from src.myntra.fill import fill_template
from src.myntra.template_reader import read_template
from src.web.jobs import store
from src.web.main import create_app
from src.web.settings import Settings
import src.web.routers.generate as gen

V13 = "templates/myntra/Myntra-Sku-Template-2026-07-24.xlsx"


def _client(tmp_path):
    s = Settings(auth_disabled=True, s3_bucket="b",
                 ledger_local_path=str(tmp_path / "led.json"),
                 hsn_local_path=str(tmp_path / "hsn.json"),
                 sku_registry_local_path=str(tmp_path / "reg.json"))
    return TestClient(create_app(s))


def _job(tmp_path, monkeypatch, skus=("S1", "S2"), with_images=True):
    """A finished job on disk: built workbook + the Shopify export it came from."""
    warnings.filterwarnings("ignore")
    monkeypatch.setattr(gen, "RUNTIME", str(tmp_path / "runtime"))
    job = store.create()
    job_dir = os.path.join(gen.RUNTIME, job.id)
    os.makedirs(job_dir, exist_ok=True)

    t = read_template(V13)
    rows = [(MappedRow(sku=s, cells={"vendorSkuCode": s, "brand": "Ijor"}),
             ImageResult(sku=s)) for s in skus]
    xlsx = os.path.join(job_dir, "myntra_filled.xlsx")
    fill_template(V13, t, rows, xlsx)

    with open(os.path.join(job_dir, "products_export.csv"), "w",
              newline="", encoding="utf-8") as fh:
        fh.write("Handle,Title,Variant SKU,Image Src,Image Position\n")
        for i, s in enumerate(skus, start=1):
            img = f"https://cdn.example/{s}.jpg" if with_images else ""
            fh.write(f"h{i},Product {i},{s},{img},1\n")

    job.status = "done"
    job.result = {"filled": xlsx, "report": "", "products": len(skus), "uploaded": 0}
    return job


def test_screen_renders_a_panel_per_sku_with_twelve_selects(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch)
    r = _client(tmp_path).get(f"/generate/attributes/{job.id}")
    assert r.status_code == 200
    assert r.text.count('class="attr-panel"') == 2
    assert r.text.count('name="attr__0__') == 12          # 12 dropdowns for SKU 1
    assert 'name="sku__0"' in r.text and 'value="S1"' in r.text
    assert "https://cdn.example/S1.jpg" in r.text          # photo from the export
    assert "Product 1" in r.text                           # title from the export


def test_dropdown_options_come_only_from_the_template_vocab(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    r = _client(tmp_path).get(f"/generate/attributes/{job.id}")
    assert "<option value=\"\">— choose —</option>" in r.text
    assert ">Banarasi<" in r.text          # a real Type value
    assert ">Zari<" in r.text              # a real Ornamentation value
    assert ">Salmon Pink<" not in r.text   # not in Myntra's colour list


def test_existing_values_are_preselected(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    from src.myntra.attribute_entry import write_attributes
    t = read_template(V13)
    write_attributes(job.result["filled"], t,
                     [{"ordinal": 0, "sku": "S1", "values": {"Border": "Zari"}}])
    r = _client(tmp_path).get(f"/generate/attributes/{job.id}")
    assert '<option value="Zari" selected>Zari</option>' in r.text
    assert "1/12 filled" in r.text


def test_missing_image_falls_back_to_placeholder(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",), with_images=False)
    r = _client(tmp_path).get(f"/generate/attributes/{job.id}")
    assert r.status_code == 200
    assert "no photo" in r.text


def test_unknown_job_says_session_expired(tmp_path, monkeypatch):
    monkeypatch.setattr(gen, "RUNTIME", str(tmp_path / "runtime"))
    r = _client(tmp_path).get("/generate/attributes/" + "0" * 32)
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/web/test_attributes.py -v`
Expected: FAIL — 404 on every screen request (route not registered).

- [ ] **Step 3: Write minimal implementation**

Create `src/web/routers/attributes.py`:

```python
import os

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse

from src.core.shopify_reader import read_products
from src.myntra.attribute_entry import attribute_vocab, user_filled_attributes
from src.myntra.pipeline import DEFAULT_TEMPLATE_NAME
from src.myntra.preview import build_card, is_set, read_filled_rows
from src.myntra.template_reader import read_template
from src.web.jobs import store
from src.web.routers.pages import get_user

router = APIRouter()
TEMPLATE = os.path.join("templates", "myntra", DEFAULT_TEMPLATE_NAME)
EXPIRED = "session expired, please re-upload"


def _templates():
    from src.web.main import templates
    return templates


def job_files(job_id):
    """(job, job_dir, xlsx_path, csv_path) or 404 if the job/build is gone.
    RUNTIME is read from the generate router at call time so tests can point it
    at a tmp dir."""
    from src.web.routers.generate import RUNTIME, _safe_job_id
    job_id = _safe_job_id(job_id)
    job = store.get(job_id)
    if not job or not job.result:
        raise HTTPException(status_code=404, detail=EXPIRED)
    job_dir = os.path.join(RUNTIME, job_id)
    xlsx = job.result.get("filled")
    if not xlsx or not os.path.exists(xlsx):
        raise HTTPException(status_code=404, detail=EXPIRED)
    return job, job_dir, xlsx, os.path.join(job_dir, "products_export.csv")


def _panels(xlsx, csv_path, template, columns):
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
            "values": {c: attrs.get(c) for c in columns},
            "filled": sum(1 for c in columns if is_set(attrs.get(c))),
            "card": build_card(attrs, columns),
        })
    return panels


@router.get("/generate/attributes/{job_id}", response_class=HTMLResponse)
def attributes_form(request: Request, job_id: str):
    user = get_user(request)
    job, _job_dir, xlsx, csv_path = job_files(job_id)
    template = read_template(TEMPLATE)
    columns = user_filled_attributes()
    return _templates().TemplateResponse(request, "attributes.html", {
        "user": user, "job_id": job.id, "columns": columns,
        "vocab": attribute_vocab(template, columns),
        "panels": _panels(xlsx, csv_path, template, columns),
        "total": len(columns)})
```

Create `src/web/templates/_attr_panel.html`:

```html
<details class="attr-panel">
  <summary>
    <strong class="mono">{{ p.sku }}</strong>
    <span class="hint">{{ p.product_title }}</span>
    <span class="hint">{{ p.filled }}/{{ total }} filled</span>
  </summary>
  <input type="hidden" name="sku__{{ p.ordinal }}" value="{{ p.sku }}">
  <div class="attr-body">
    <div class="attr-photo">
      {% if p.image %}
        <img src="{{ p.image }}" alt="{{ p.sku }}" loading="lazy">
      {% else %}
        <div class="drop">no photo</div>
      {% endif %}
    </div>
    <div class="attr-grid"
         hx-post="/generate/attributes/{{ job_id }}/preview"
         hx-trigger="change"
         hx-include="closest .attr-panel"
         hx-target="#attr-preview-{{ p.ordinal }}"
         hx-swap="innerHTML">
      {% for column in columns %}
      <label class="hint">{{ column }}
        <select name="attr__{{ p.ordinal }}__{{ loop.index0 }}">
          <option value="">— choose —</option>
          {% for v in vocab[column] %}
          <option value="{{ v }}"{% if p.values[column] == v %} selected{% endif %}>{{ v }}</option>
          {% endfor %}
        </select>
      </label>
      {% endfor %}
    </div>
    <div class="attr-preview" id="attr-preview-{{ p.ordinal }}">
      {% with c = p.card %}{% include "_preview_card.html" %}{% endwith %}
    </div>
  </div>
</details>
```

Create `src/web/templates/attributes.html`:

```html
{% extends "base.html" %}
{% block content %}
<div class="panel">
  <h2>Fill attributes</h2>
  <p class="hint">Myntra builds the public title and Design Details from these attributes.
    Pick them per product — the preview updates as you choose. Anything you leave on
    “— choose —” stays blank in the file and still has its Excel dropdown.</p>
  <p class="flag mono"><strong>⚠ Title &amp; Design Details are auto-generated by Myntra —
    the preview is our best reconstruction, not guaranteed word-for-word.</strong></p>

  {% if not panels %}
    <p class="hint">Nothing to fill in this batch.</p>
    <a class="btn" href="/generate/download/{{ job_id }}">⬇ Download xlsx</a>
  {% else %}
  <form hx-post="/generate/attributes/{{ job_id }}" hx-target="#attr-save-out"
        hx-swap="innerHTML">
    {% for p in panels %}{% include "_attr_panel.html" %}{% endfor %}
    <button class="btn" type="submit">Save attributes</button>
  </form>
  <div id="attr-save-out"></div>
  {% endif %}
</div>
{% endblock %}
```

In `src/web/main.py`, register the router:

```python
    from src.web.routers import pages, generate, fix, auth_routes, preview, attributes
    app.include_router(pages.router)
    app.include_router(generate.router)
    app.include_router(fix.router)
    app.include_router(auth_routes.router)
    app.include_router(preview.router)
    app.include_router(attributes.router)
```

In `src/web/templates/_result.html`, add the new action right after the download link (line 10):

```html
    <a class="btn" href="/generate/attributes/{{ job.id }}">✎ Fill attributes</a>
```

Add to the end of `src/web/static/app.css`:

```css
.attr-panel{border:1px solid var(--line);border-radius:8px;padding:10px;margin:10px 0}
.attr-panel summary{cursor:pointer;display:flex;gap:12px;align-items:center}
.attr-body{display:flex;gap:16px;flex-wrap:wrap;margin-top:12px}
.attr-photo img{width:180px;border-radius:8px}
.attr-grid{display:grid;grid-template-columns:repeat(2,minmax(180px,1fr));gap:8px}
.attr-grid select{width:100%;background:var(--panel);color:var(--ink);
  border:1px solid var(--line);border-radius:6px;padding:6px}
.attr-preview{flex:1;min-width:260px}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/web/test_attributes.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/web/routers/attributes.py src/web/templates/attributes.html src/web/templates/_attr_panel.html src/web/main.py src/web/templates/_result.html src/web/static/app.css tests/web/test_attributes.py
git commit -m "feat(web): in-app Fill attributes screen with photo and vocab dropdowns

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Live preview fragment + Save

The two POSTs that make the screen work. Live preview re-renders one card from the posted values using the same `build_card`. Save validates every value against the vocabulary, then writes all SKUs at once.

**Files:**
- Modify: `src/web/routers/attributes.py`
- Create: `src/web/templates/_attr_saved.html`
- Test: `tests/web/test_attributes.py`

**Interfaces:**
- Consumes: `validate_submitted`, `write_attributes`, `AttributeValueError`, `SkuMismatchError` (Tasks 2-3); `job_files`, the `attr__{ordinal}__{column_index}` / `sku__{ordinal}` field names (Task 5).
- Produces: routes `POST /generate/attributes/{job_id}/preview` (returns a `_preview_card.html` fragment) and `POST /generate/attributes/{job_id}` (returns `_attr_saved.html`, HTTP 200 on both success and validation failure so htmx can swap it in).

- [ ] **Step 1: Write the failing test**

Add to `tests/web/test_attributes.py`:

```python
def test_live_preview_reconstructs_from_posted_values(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    # column indexes: 0 Prominent Colour .. 5 Type .. 9 Print or Pattern Type
    r = _client(tmp_path).post(f"/generate/attributes/{job.id}/preview", data={
        "sku__0": "S1", "attr__0__0": "Blue", "attr__0__3": "Pure Silk",
        "attr__0__5": "Banarasi", "attr__0__9": "Floral"})
    assert r.status_code == 200
    assert "Floral Pure Silk Banarasi Saree" in r.text
    assert "Blue Banarasi sarees" in r.text


def test_save_writes_all_skus_and_download_serves_the_updated_file(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1", "S2"))
    client = _client(tmp_path)
    r = client.post(f"/generate/attributes/{job.id}", data={
        "sku__0": "S1", "attr__0__5": "Banarasi", "attr__0__7": "Zari",
        "sku__1": "S2", "attr__1__5": "Chanderi"})
    assert r.status_code == 200
    assert "Saved" in r.text

    t = read_template(V13)
    import openpyxl
    wb = openpyxl.load_workbook(job.result["filled"], data_only=True)
    ws = wb["Sarees"]
    assert ws.cell(row=t.first_data_row,
                   column=t.col_index_by_header["Type"]).value == "Banarasi"
    assert ws.cell(row=t.first_data_row,
                   column=t.col_index_by_header["Border"]).value == "Zari"
    assert ws.cell(row=t.first_data_row + 1,
                   column=t.col_index_by_header["Type"]).value == "Chanderi"
    assert ws.cell(row=t.first_data_row,
                   column=t.col_index_by_header["Pattern"]).value is None
    wb.close()

    d = client.get(f"/generate/download/{job.id}")
    assert d.status_code == 200


def test_save_reopens_with_values_selected(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    client = _client(tmp_path)
    client.post(f"/generate/attributes/{job.id}",
                data={"sku__0": "S1", "attr__0__7": "Zari"})
    r = client.get(f"/generate/attributes/{job.id}")
    assert '<option value="Zari" selected>Zari</option>' in r.text


def test_save_rejects_off_vocab_value_without_writing(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    client = _client(tmp_path)
    r = client.post(f"/generate/attributes/{job.id}",
                    data={"sku__0": "S1", "attr__0__0": "Salmon Pink"})
    assert r.status_code == 200          # htmx-swappable error panel, not a 500
    assert "Prominent Colour" in r.text
    assert "not one of Myntra" in r.text
    t = read_template(V13)
    import openpyxl
    wb = openpyxl.load_workbook(job.result["filled"], data_only=True)
    v = wb["Sarees"].cell(row=t.first_data_row,
                          column=t.col_index_by_header["Prominent Colour"]).value
    wb.close()
    assert v is None


def test_save_keeps_dropdowns_alive_in_the_downloaded_file(tmp_path, monkeypatch):
    """KEY INVARIANT end-to-end: the owner's Excel check must still pass."""
    import openpyxl
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    wb = openpyxl.load_workbook(job.result["filled"])
    before = len(wb["Sarees"].data_validations.dataValidation)
    wb.close()
    _client(tmp_path).post(f"/generate/attributes/{job.id}",
                           data={"sku__0": "S1", "attr__0__7": "Zari"})
    wb = openpyxl.load_workbook(job.result["filled"])
    assert len(wb["Sarees"].data_validations.dataValidation) == before
    wb.close()


def test_save_on_expired_job_says_session_expired(tmp_path, monkeypatch):
    monkeypatch.setattr(gen, "RUNTIME", str(tmp_path / "runtime"))
    r = _client(tmp_path).post("/generate/attributes/" + "0" * 32,
                               data={"sku__0": "S1"})
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/web/test_attributes.py -v`
Expected: FAIL — 405/404 on both POSTs (routes not defined).

- [ ] **Step 3: Write minimal implementation**

In `src/web/routers/attributes.py`, extend the imports:

```python
from src.myntra.attribute_entry import (AttributeValueError, SkuMismatchError,
                                        attribute_vocab, user_filled_attributes,
                                        validate_submitted, write_attributes)
```

and append:

```python
def _submitted(form, columns):
    """Parse attr__{ordinal}__{column_index} + sku__{ordinal} into
    {ordinal: {"sku": str, "values": {column: raw_value}}}, in ordinal order."""
    entries = {}
    for key, value in form.items():
        if key.startswith("sku__"):
            ordinal = int(key.split("__")[1])
            entries.setdefault(ordinal, {"sku": "", "values": {}})["sku"] = str(value)
        elif key.startswith("attr__"):
            _, ordinal, col_index = key.split("__")
            ordinal, col_index = int(ordinal), int(col_index)
            if 0 <= col_index < len(columns):
                entry = entries.setdefault(ordinal, {"sku": "", "values": {}})
                entry["values"][columns[col_index]] = str(value)
    return dict(sorted(entries.items()))


@router.post("/generate/attributes/{job_id}/preview", response_class=HTMLResponse)
async def attributes_live_preview(request: Request, job_id: str):
    get_user(request)
    job_files(job_id)                      # 404s an expired job before doing work
    columns = user_filled_attributes()
    entries = _submitted(await request.form(), columns)
    ordinal, entry = next(iter(entries.items()), (0, {"sku": "", "values": {}}))
    attrs = dict(entry["values"])
    attrs["vendorSkuCode"] = entry["sku"]
    return _templates().TemplateResponse(
        request, "_preview_card.html", {"c": build_card(attrs, columns)})


@router.post("/generate/attributes/{job_id}", response_class=HTMLResponse)
async def attributes_save(request: Request, job_id: str):
    get_user(request)
    job, _job_dir, xlsx, _csv = job_files(job_id)
    template = read_template(TEMPLATE)
    columns = user_filled_attributes()
    vocab = attribute_vocab(template, columns)
    entries = _submitted(await request.form(), columns)

    try:
        payload = [{"ordinal": ordinal, "sku": e["sku"],
                    "values": validate_submitted(e["values"], vocab)}
                   for ordinal, e in entries.items()]
        saved = write_attributes(xlsx, template, payload)
    except (AttributeValueError, SkuMismatchError) as exc:
        return _templates().TemplateResponse(
            request, "_attr_saved.html", {"job_id": job.id, "error": str(exc)})
    return _templates().TemplateResponse(
        request, "_attr_saved.html", {"job_id": job.id, "saved": saved})
```

Create `src/web/templates/_attr_saved.html`:

```html
<div class="panel">
  {% if error %}
    <h3 class="flag">⚠ Nothing was saved</h3>
    <p class="mono">{{ error }}</p>
    <p class="hint">Reload the screen and pick a value from the dropdown.</p>
  {% else %}
    <h3 class="ok">✅ Saved — {{ saved }} product(s) updated</h3>
    <p class="hint">Anything you left blank is still blank in the file and keeps its
      Excel dropdown, so you can finish in Excel if you prefer.</p>
    <a class="btn" href="/generate/download/{{ job_id }}">⬇ Download xlsx</a>
  {% endif %}
</div>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/web/test_attributes.py -v`
Expected: PASS (11 tests).

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS — 190 previous tests plus the new ones. If `tests/web/test_pages.py` asserts an exact route or nav list, add the new routes there.

- [ ] **Step 6: Commit**

```bash
git add src/web/routers/attributes.py src/web/templates/_attr_saved.html tests/web/test_attributes.py
git commit -m "feat(web): live listing preview and save for in-app attribute entry

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Document the feature

The repo's agent-facing and owner-facing docs must describe the new surface, or the next session re-derives it from code.

**Files:**
- Modify: `AGENTS.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/APP-FEATURES-GUIDE.md`
- Modify: `README.md`
- Create: `docs/journal/2026-07-27.md`

**Interfaces:**
- Consumes: the finished behaviour of Tasks 1-6.
- Produces: no code interface.

- [ ] **Step 1: Update the agent-facing docs**

In `AGENTS.md` and `docs/ARCHITECTURE.md`, add `src/myntra/attribute_entry.py` and `src/web/routers/attributes.py` to the module map, and describe the flow: build → optional in-app fill (`/generate/attributes/{job_id}`) → download → optional Excel fill → optional `/preview` → upload to Myntra. State explicitly that **12** attributes are user-filled (not 9) and that `config/myntra/rules.yaml:user_filled_attributes` is the single source of truth.

Also record the constraint that will otherwise be rediscovered the hard way:

> Any code path that re-saves a built workbook with openpyxl **must** re-apply
> `fill.shared_to_inline()` afterwards — Myntra's upload parser cannot resolve shared
> strings, and openpyxl re-creates them on every save.

- [ ] **Step 2: Update the owner-facing docs**

In `docs/APP-FEATURES-GUIDE.md`, add a "Fill attributes" section in plain English: what the screen is for, that dropdowns are Myntra's own words, that the preview is approximate for title/Design Details and exact for specifications, that skipping is fine, and that the downloaded file still has Excel dropdowns. In `README.md`, add the screen to the flow description.

- [ ] **Step 3: Write the journal entry**

Create `docs/journal/2026-07-27.md` covering: the spec, this plan, what was built, the shared-string finding from §6.2 of the spec, and the remaining owner-run Myntra upload test.

- [ ] **Step 4: Verify no stale claims remain**

Run: `python -m pytest -q` (docs-only change; the suite must still pass)
Then grep for stale numbers and remove or correct each hit:

```bash
grep -rn "nine attributes\|9 attributes\|the 9 " AGENTS.md docs/ README.md
```

Expected: no hit that still describes the user-filled set as nine.

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md docs/ README.md
git commit -m "docs: document the in-app attribute entry screen

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Final manual verification (owner-run, not a code task)

Myntra acceptance cannot be tested in code. After Tasks 1-7 are green:

1. Run the app locally: `AUTH_DISABLED=1 python -m uvicorn src.web.main:app --port 8000`.
2. Generate a real batch → on the result screen press **Fill attributes**.
3. Expand a SKU, check the photo matches the SKU, pick values, watch the preview update.
4. Save → **Download xlsx** → open in Excel → **confirm the dropdowns are still live** on the 12 columns and the values you picked are there.
5. Upload that file to Myntra → confirm it is accepted and the generated title matches the preview closely.

If Myntra rejects the file, do not weaken the tests — capture the exact error and check first whether shared strings crept back in (`t="s"` in `xl/worksheets/sheet1.xml`).

## Self-Review

**Spec coverage:**
- §3 twelve columns + vocab table + `rules.yaml` as source of truth → Task 1 (config) + Task 2 (vocab test asserts the exact NA/no-NA split). ✓
- §4 placement after build, optional, result-panel entry point, download unchanged → Task 5 (`_result.html` button, `attributes.html`) + Task 6 (save panel links to the existing download route). ✓
- §5.1 accordion, photo by SKU, 12 selects, placeholder writes nothing, one Save → Task 5 (`_attr_panel.html`, `attributes.html`). ✓
- §5.1 "n/12 filled" counter from saved state → Task 5 (`filled` in `_panels`, asserted by `test_existing_values_are_preselected`). ✓
- §5.2 server-side live preview via htmx calling the existing reconstruction → Task 6 (`attributes_live_preview`). ✓
- §5.3 shared `build_card` + extracted `_preview_card.html` + one column-list loader → Task 4 (+ Task 1 fallback alignment). ✓
- §6 save into the built workbook, `first_data_row + ordinal`, SKU verification, idempotent blanking → Task 3. ✓
- §6.1 dropdown survival → Task 3 `test_write_attributes_preserves_dropdowns` + Task 6 end-to-end `test_save_keeps_dropdowns_alive_in_the_downloaded_file`. ✓
- §6.2 shared-string→inline re-application → Task 3 (`shared_to_inline` made public, called after save; `test_write_attributes_keeps_strings_inline`). ✓
- §6.3 vocabulary guard → Task 2 (`validate_submitted`) + Task 6 (`test_save_rejects_off_vocab_value_without_writing`). ✓
- §7 component table → Tasks 1-6 cover every listed file; §7.1 joins → Task 5 `_panels` + Task 3 SKU verification. ✓
- §8 every listed test → Tasks 1-6; the manual gate → "Final manual verification". ✓
- §9 edge cases: no image → Task 5 `test_missing_image_falls_back_to_placeholder`; expired job → Task 5 + Task 6 tests; re-save → Task 3 idempotence + Task 6 `test_save_reopens_with_values_selected`; skipped screen → download route untouched; empty batch → `attributes.html` "Nothing to fill" branch. ✓
- §10 risks → the two invariant tests plus the manual gate. ✓
- Docs (asked for alongside the spec) → Task 7. ✓

**Placeholder scan:** none — every step contains real code, real commands, and real expected output. No "add error handling"-style steps.

**Type consistency:**
- `user_filled_attributes(config_dir="config/myntra") -> list[str]` — Tasks 2, 5, 6.
- `attribute_vocab(template, columns) -> dict[str, list[str]]` — Tasks 2, 5, 6.
- `validate_submitted(values, vocab) -> dict[str, str | None]`, raising `AttributeValueError` — Tasks 2, 6.
- `write_attributes(xlsx_path, template, entries) -> int` with entry keys `ordinal` / `sku` / `values`, raising `SkuMismatchError` — Tasks 3, 5 (test), 6.
- `fill.sheet_xml_name(xlsx_path, sheet_title)` / `fill.shared_to_inline(out_path, sheet_xml)` — Task 3 defines, Task 3 consumes.
- `build_card(attrs, user_filled) -> dict` with keys `sku`/`title`/`design_details`/`specs`/`missing`; `_preview_card.html` reads exactly those from `c` — Tasks 4, 5, 6.
- `job_files(job_id) -> (job, job_dir, xlsx, csv_path)` — Task 5 defines, Task 6 consumes.
- Form field names `attr__{ordinal}__{column_index}` and `sku__{ordinal}` — emitted in Task 5's `_attr_panel.html`, parsed by Task 6's `_submitted`, used in both tasks' tests.
