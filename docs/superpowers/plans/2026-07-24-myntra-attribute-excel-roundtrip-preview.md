# Myntra Attributes User-Decided in Excel + Listing Preview — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the pipeline guessing/hard-coding the 9 name-driving Myntra attributes — leave them blank with the template's native dropdowns for the owner to fill in Excel — and add a round-trip re-upload preview that shows exact specs plus a labelled-approximate reconstruction of Myntra's title/description.

**Architecture:** Switch the pipeline from the old x14-dropdown template (2026-06-16) to the V13 plain-dropdown template (2026-07-24) whose data-validations openpyxl preserves on save. Teach `read_template` to parse plain (non-x14) validations. Blank the 9 user-filled attributes in the mapper via a declarative config list. Add a small read-only preview surface (`/preview`) that parses a filled workbook and renders per-product cards.

**Tech Stack:** Python 3.12, openpyxl, FastAPI + htmx, Jinja2, pytest.

## Global Constraints

- **V13 template path (exact):** `templates/myntra/Myntra-Sku-Template-2026-07-24.xlsx`
- **Old template path (still used by existing 06-16 tests, keep working):** `templates/myntra/Myntra-Sku-Template-2026-06-16.xlsx`
- **The 9 user-filled attribute headers (exact strings, this order):** `Prominent Colour`, `Saree Fabric`, `Blouse Fabric`, `Type`, `Ornamentation`, `Border`, `Pattern`, `Print or Pattern Type`, `Wash Care`
- **Entry sheet name:** `Sarees`; masterdata sheet name: `masterdata`; header row = 3; first data row = 4.
- **"Set" means:** value is not None, not empty after strip, and not the literal `NA` (case-insensitive). `NA` counts as blank for the preview.
- **No machine guessing / no self-learning / no pre-fill** of the 9 attributes. Ever.
- **Commit trailer:** end every commit message with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- **Run tests with:** `python -m pytest` (from repo root, venv active).
- **Do not** modify brand, HSN, pricing, image, styleGroupId, identity, or Product Details logic.

---

### Task 1: Teach `read_template` to parse plain (non-x14) validations

The V13 template stores dropdowns as ordinary `<dataValidation type="list">` entries (openpyxl exposes these via `ws.data_validations`), not the x14 extension the current reader parses. Without this, `read_template(V13)` returns an empty `vocab_by_header` and silently breaks vocab validation across the pipeline.

**Files:**
- Modify: `src/myntra/template_reader.py`
- Test: `tests/test_template_reader.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `read_template(path) -> TemplateInfo` unchanged signature; now populates `vocab_by_header` from plain validations when present, falling back to x14 parsing when there are no plain list validations (so the 06-16 template still works).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_template_reader.py`:

```python
V13 = "templates/myntra/Myntra-Sku-Template-2026-07-24.xlsx"


def test_vocab_extracted_from_plain_validations_v13():
    t = read_template(V13)
    assert t.headers[0] == "styleId"
    assert t.col_index_by_header["brand"] == 6
    # dropdowns resolved from the masterdata sheet via plain (non-x14) validations
    assert "Banarasi" in t.vocab_by_header["Type"]
    assert "Solid" in t.vocab_by_header["Border"]
    assert "Zari" in t.vocab_by_header["Ornamentation"]
    assert "Blue" in t.vocab_by_header["Prominent Colour"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_template_reader.py::test_vocab_extracted_from_plain_validations_v13 -v`
Expected: FAIL — `KeyError: 'Type'` (vocab_by_header empty, because only x14 is parsed).

- [ ] **Step 3: Write minimal implementation**

In `src/myntra/template_reader.py`, add a plain-validation parser and use it first. Add these helpers above `read_template`:

```python
from openpyxl.utils import get_column_letter

_MD_RANGE = re.compile(
    r"(?:'?(?P<sheet>[^'!]+)'?!)?\$?(?P<c1>[A-Z]+)\$?(?P<r1>\d+)"
    r"(?::\$?(?P<c2>[A-Z]+)\$?(?P<r2>\d+))?$"
)


def _plain_list_validations(ws, data_row):
    """(col_index, formula1) for each plain list validation covering data_row.
    First validation seen per column wins."""
    out = {}
    for dv in ws.data_validations.dataValidation:
        if dv.type != "list":
            continue
        for rng in dv.sqref.ranges:
            if rng.min_row <= data_row <= rng.max_row:
                for c in range(rng.min_col, rng.max_col + 1):
                    out.setdefault(c, str(dv.formula1))
    return out


def _resolve_validation_values(wb, formula):
    """Resolve a list-validation formula to accepted values, in order, exact dups
    removed. Handles inline 'A,B,C' lists and masterdata!$X$r0:$X$r1 ranges."""
    f = (formula or "").strip().strip('"')
    if not f:
        return []
    if "!" not in f and "$" not in f and "," in f:            # inline list
        return list(dict.fromkeys(v.strip() for v in f.split(",") if v.strip()))
    m = _MD_RANGE.match(f)
    if not m:
        return []
    sheet = m.group("sheet")
    if not sheet or sheet not in wb.sheetnames:
        return []
    ws = wb[sheet]
    col = column_index_from_string(m.group("c1"))
    r1 = int(m.group("r1"))
    r2 = int(m.group("r2")) if m.group("r2") else r1
    if r2 < r1:
        return []
    values = []
    for r in range(r1, r2 + 1):
        v = ws.cell(row=r, column=col).value
        if v not in (None, ""):
            values.append(str(v).strip())
    return list(dict.fromkeys(values))
```

Then in `read_template`, replace the vocab-building block (the part that calls `_parse_x14_validations` and loops over `validations`) with plain-first, x14-fallback:

```python
    data_row = header_row + 1
    plain = _plain_list_validations(ws, data_row)
    vocab_by_header = {}
    if plain:
        for col_index, formula in plain.items():
            header = headers[col_index - 1] if col_index - 1 < len(headers) else None
            if not header:
                continue
            values = _resolve_validation_values(wb, formula)
            if values:
                vocab_by_header[header] = values
    else:
        sheet_xml = _find_sheet_xml_name(path, SHEET_SAREES_NAME)
        for col_index, md_col, r0, r1 in _parse_x14_validations(path, sheet_xml):
            header = headers[col_index - 1] if col_index - 1 < len(headers) else None
            if not header:
                continue
            values = []
            for r in range(r0, r1 + 1):
                v = md.cell(row=r, column=md_col).value
                if v not in (None, ""):
                    values.append(str(v).strip())
            vocab_by_header[header] = values
```

(Keep the existing `_parse_x14_validations`, `_find_sheet_xml_name`, imports, and the `md = wb[MASTERDATA_NAME]` line — the x14 branch still uses `md`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_template_reader.py -v`
Expected: PASS — both the new V13 test and the existing `test_vocab_extracted_from_x14` (06-16) pass. The 06-16 file has no plain list validations, so it uses the x14 fallback.

- [ ] **Step 5: Commit**

```bash
git add src/myntra/template_reader.py tests/test_template_reader.py
git commit -m "feat(template): parse plain (non-x14) dropdowns so V13 template vocab resolves

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Switch the pipeline default template to V13 (single constant)

The template path is hardcoded in three places. Introduce one constant and point everything at V13.

**Files:**
- Modify: `src/myntra/pipeline.py:20-25` and `src/myntra/pipeline.py:100-101`
- Modify: `src/web/routers/fix.py:24`
- Test: `tests/test_pipeline_override.py`

**Interfaces:**
- Consumes: `read_template` from Task 1.
- Produces: `src.myntra.pipeline.DEFAULT_TEMPLATE_NAME` (str) = `"Myntra-Sku-Template-2026-07-24.xlsx"`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_pipeline_override.py`:

```python
def test_default_template_is_v13_and_has_vocab():
    from src.myntra.pipeline import DEFAULT_TEMPLATE_NAME, _resolve
    from src.myntra.template_reader import read_template
    assert DEFAULT_TEMPLATE_NAME == "Myntra-Sku-Template-2026-07-24.xlsx"
    path = _resolve(DEFAULT_TEMPLATE_NAME, "templates/myntra")
    t = read_template(path)
    assert "Banarasi" in t.vocab_by_header["Type"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pipeline_override.py::test_default_template_is_v13_and_has_vocab -v`
Expected: FAIL — `ImportError: cannot import name 'DEFAULT_TEMPLATE_NAME'`.

- [ ] **Step 3: Write minimal implementation**

In `src/myntra/pipeline.py`, add the constant after the imports (near line 12) and use it in both `main` and `scan_content_hashes`:

```python
DEFAULT_TEMPLATE_NAME = "Myntra-Sku-Template-2026-07-24.xlsx"
```

Replace in `main` (currently line 23-24):

```python
    template_path = template_path or _resolve(DEFAULT_TEMPLATE_NAME, "templates/myntra")
```

Replace in `scan_content_hashes` (currently line 100-101):

```python
    template_path = template_path or _resolve(DEFAULT_TEMPLATE_NAME, "templates/myntra")
```

In `src/web/routers/fix.py:24`, change:

```python
TEMPLATE = os.path.join("templates", "myntra", "Myntra-Sku-Template-2026-07-24.xlsx")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_pipeline_override.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/myntra/pipeline.py src/web/routers/fix.py tests/test_pipeline_override.py
git commit -m "feat(pipeline): switch default template to V13 via one DEFAULT_TEMPLATE_NAME constant

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Template-compatibility guard

Fail loudly if the active template is missing any header the config writes to, so a future template swap can never silently drop a column.

**Files:**
- Create: `src/myntra/template_guard.py`
- Modify: `src/myntra/pipeline.py` (call the guard in `main` after `read_template` + config load)
- Test: `tests/test_template_guard.py`

**Interfaces:**
- Consumes: `TemplateInfo` (from `src.core.models`), the `column_map` dict, the `constants` dict.
- Produces: `assert_template_compatible(template, column_map, constants) -> None` (raises `TemplateIncompatibleError` on any missing header); `TemplateIncompatibleError(Exception)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_template_guard.py`:

```python
import pytest
import yaml

from src.core.models import TemplateInfo
from src.myntra.template_reader import read_template
from src.myntra.template_guard import (assert_template_compatible,
                                       TemplateIncompatibleError)

V13 = "templates/myntra/Myntra-Sku-Template-2026-07-24.xlsx"


def test_guard_passes_for_v13_with_real_config():
    t = read_template(V13)
    cmap = yaml.safe_load(open("config/myntra/column_map.yaml", encoding="utf-8"))
    consts = yaml.safe_load(open("config/myntra/constants.yaml", encoding="utf-8"))
    assert_template_compatible(t, cmap, consts)  # must not raise


def test_guard_raises_listing_missing_headers():
    t = TemplateInfo(headers=["brand"], header_row=3, first_data_row=4,
                     col_index_by_header={"brand": 1}, vocab_by_header={})
    with pytest.raises(TemplateIncompatibleError) as exc:
        assert_template_compatible(t, {"title": "vendorArticleName"}, {"brand": "Ijor"})
    assert "vendorArticleName" in str(exc.value)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_template_guard.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.myntra.template_guard'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/myntra/template_guard.py`:

```python
class TemplateIncompatibleError(Exception):
    """The active template is missing a header the pipeline writes to."""


def assert_template_compatible(template, column_map, constants):
    """Raise if any header written by the column map or constants is absent from
    the template's Sarees header row. Fail loud on a template swap, never silent."""
    expected = set(column_map.values()) | set(constants.keys())
    missing = sorted(h for h in expected if h not in template.col_index_by_header)
    if missing:
        raise TemplateIncompatibleError(
            "Template is missing expected headers: " + ", ".join(missing))
```

In `src/myntra/pipeline.py` `main`, right after `template = read_template(template_path)` (line 32) and after the config loads (they are loaded just above at lines 27-30), add:

```python
    from src.myntra.template_guard import assert_template_compatible
    assert_template_compatible(template, column_map, constants)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_template_guard.py -v`
Expected: PASS.

> If `test_guard_passes_for_v13_with_real_config` FAILS, that is a real finding — V13 genuinely lacks a header the config expects. Do not weaken the guard. Stop and reconcile the config/constants against V13's header row before continuing.

- [ ] **Step 5: Commit**

```bash
git add src/myntra/template_guard.py tests/test_template_guard.py src/myntra/pipeline.py
git commit -m "feat(pipeline): guard that the active template has every configured header

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Leave the 9 attributes blank (declarative list + remove auto-fills)

Introduce `user_filled_attributes` in `rules.yaml`; the mapper pops those from the row so they are never emitted. Remove the now-dead colour extraction and the fabric block's attribute writes (keep fabric-keyword detection, which HSN still needs). Remove the `Type`/`Border`/`Pattern` constants.

**Files:**
- Modify: `config/myntra/rules.yaml`
- Modify: `config/myntra/constants.yaml`
- Modify: `src/myntra/mapper.py`
- Test: `tests/test_mapper.py`

**Interfaces:**
- Consumes: `rules["user_filled_attributes"]` (list[str]).
- Produces: `map_product(...)` still returns `MappedRow`, but `row.cells` never contains any header in `user_filled_attributes`; those headers appear in `row.blanks` when they are vocab-controlled.

- [ ] **Step 1: Write the failing test**

In `tests/test_mapper.py`, ADD a new test and UPDATE the two that assert removed behaviour.

Add:

```python
def test_user_filled_attributes_are_left_blank():
    rules = {"user_filled_attributes": ["Prominent Colour", "Saree Fabric", "Type"]}
    p = Product(handle="h", sku="S1", title="Banarasi Silk Saree Blue", vendor="V",
                tags="Banarasi, Silk", body_html="", price=1000.0, compare_at_price=None,
                color="Blue", fabric="silk", size=None, status="active", images=[])
    # even if the column map points at them, they must be blanked
    cmap = {"title": "vendorArticleName", "sku": "vendorSkuCode",
            "color": "Prominent Colour", "fabric": "Saree Fabric"}
    row = map_product(p, _template(), cmap, {}, rules)
    for h in ["Prominent Colour", "Saree Fabric", "Type"]:
        assert h not in row.cells
    assert row.cells["vendorSkuCode"] == "S1"   # non-attribute columns still filled
```

Replace `test_cotton_fabric_block_and_colour_and_forced_brand` with:

```python
def test_fabric_and_colour_no_longer_auto_filled():
    p = Product(handle="h", sku="S1", title="Lavender Pure Cotton Saree", vendor="V",
                tags="", body_html="", price=2000.0, compare_at_price=None,
                color=None, fabric=None, size=None, status="active", images=[])
    consts = {"brand": "Ijor Ethnic Partners"}
    rules = {**FABRIC_RULES,
             "user_filled_attributes": ["Saree Fabric", "Wash Care", "Prominent Colour"]}
    row = map_product(p, _template_with_rules(), {}, consts, rules)
    assert "Saree Fabric" not in row.cells
    assert "Wash Care" not in row.cells
    assert "Prominent Colour" not in row.cells
    assert row.cells["brand"] == "Ijor Ethnic Partners"   # forced even if not in vocab
    assert any(f.field == "brand" for f in row.flags)      # but flagged
```

Replace `test_silk_fabric_block` with:

```python
def test_silk_attributes_left_blank_but_hsn_signature_still_derivable():
    p = Product(handle="h", sku="S2", title="Banarasi Silk Saree Blue", vendor="V",
                tags="", body_html="", price=3000.0, compare_at_price=None,
                color=None, fabric=None, size=None, status="active", images=[])
    rules = {**FABRIC_RULES,
             "user_filled_attributes": ["Saree Fabric", "Wash Care", "Prominent Colour"]}
    hsn_map = {"saree|silk": "50072010"}
    consts = {"articleType": "Sarees"}
    row = map_product(p, _template_with_rules(), {}, consts, rules,
                      hsn_by_signature=hsn_map)
    assert "Saree Fabric" not in row.cells
    assert "Prominent Colour" not in row.cells
    assert row.cells["HSN"] == "50072010"   # fabric keyword 'silk' still feeds HSN
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mapper.py -v`
Expected: FAIL — `test_user_filled_attributes_are_left_blank` fails (attributes still present), and the two replaced tests fail because the old fills still happen.

- [ ] **Step 3: Write minimal implementation**

In `src/myntra/mapper.py` `map_product`:

(a) Delete step 5's attribute-writing loop but keep `fabric_cfg` for HSN. Replace the block currently labelled `# 5. Fabric detection -> Saree/Blouse Fabric, Wash Care, HSN` (the `fabric_cfg`/`haystack`/`for keyword` loop) with just:

```python
    # 5. Fabric keyword config is retained ONLY to feed the HSN signature below;
    # Saree/Blouse Fabric and Wash Care are user-filled in Excel (blanked in step 8).
    fabric_cfg = rules.get("fabric_detection") or {}
```

(Leave step 5b, the HSN block, exactly as-is — it uses `fabric_cfg.get("order")`.)

(b) Delete step 6 entirely (the whole `# 6. Prominent Colour from name, then description` block, including the `if rules.get("prominent_colour_from_name") ...` through its `else: row.flags.append(...)`).

(c) Add a new step just before step 7 (`# 7. record vocab-controlled headers left blank`):

```python
    # 8. Attributes the user fills by hand in Excel — never emitted by the pipeline.
    for header in (rules.get("user_filled_attributes") or []):
        row.cells.pop(header, None)
```

(The existing step 7 loop then records any of these that are vocab-controlled into `row.blanks`.)

In `config/myntra/rules.yaml`:
- Add:

```yaml
# Attributes the user fills by hand in Excel via the template dropdowns. The mapper
# leaves these BLANK (the app never guesses them). The preview (Feature B) also reads
# this list to know which columns are user-owned.
user_filled_attributes:
  - Prominent Colour
  - Saree Fabric
  - Blouse Fabric
  - Type
  - Ornamentation
  - Border
  - Pattern
  - Print or Pattern Type
  - Wash Care
```

- Remove these now-dead keys: `prominent_colour_from_name`, `colour_scan_exclude`, `colour_synonyms`, `brand_colour_remarks_from_prominent`. Leave `fabric_detection` (its `order` still feeds HSN), `replicate_constant_across_numbered`, `auto_style_group_id`, `style_group_id_start`, `product_details_after_marker`.

In `config/myntra/constants.yaml`, remove the three attribute constants under `# --- Content defaults ...`:

```yaml
Pattern: Solid
Border: Solid
```

and the `Type: NA` line under `# --- Mandatory attribute defaults ---`. Keep `Occasion: Work`, `Blouse: NA`, `materialCareDescription`, and all other constants.

- [ ] **Step 4: Run the full suite to verify green**

Run: `python -m pytest -q`
Expected: PASS. Watch specifically:
- `tests/test_mapper.py` — the 3 changed/added tests pass; `test_hsn_*`, `test_replicate_*` still pass.
- `tests/test_config_loads.py` and `tests/test_signature.py` — if either asserts the removed constants/keys, update the assertion to match (removing `Type`/`Border`/`Pattern` constants and the colour_* rule keys is intended). If `test_end_to_end.py` asserts colour/fabric cells are populated, update it to assert they are blank.

- [ ] **Step 5: Commit**

```bash
git add src/myntra/mapper.py config/myntra/rules.yaml config/myntra/constants.yaml tests/
git commit -m "feat(mapper): leave the 9 name-driving attributes blank for user Excel entry

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Verify the V13 output keeps its dropdowns and leaves attributes blank

Lock in the mechanism: a file filled from V13 must still carry its 11,100 plain dropdowns after `fill_template` (which runs `_shared_to_inline`), and the 9 attribute columns must be empty.

**Files:**
- Test: `tests/test_dropdowns.py`
- Modify (only if the test fails): `src/myntra/fill.py`

**Interfaces:**
- Consumes: `fill_template`, `read_template`, `MappedRow`, `ImageResult`.
- Produces: no new interface — a characterization test that guards the mechanism.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_dropdowns.py`:

```python
import openpyxl

V13 = "templates/myntra/Myntra-Sku-Template-2026-07-24.xlsx"


def _count_plain_validations(path):
    warnings.filterwarnings("ignore")
    wb = openpyxl.load_workbook(path)
    n = len(wb["Sarees"].data_validations.dataValidation)
    wb.close()
    return n


def test_v13_output_preserves_plain_dropdowns_and_blanks_attributes(tmp_path):
    warnings.filterwarnings("ignore")
    assert _count_plain_validations(V13) == 11100
    t = read_template(V13)
    row = MappedRow(sku="S1", cells={"vendorSkuCode": "S1", "brand": "Ijor Ethnic Partners"})
    out = tmp_path / "filled.xlsx"
    fill_template(V13, t, [(row, ImageResult(sku="S1"))], str(out))
    # dropdowns survive fill_template (incl. its shared-string -> inline pass)
    assert _count_plain_validations(str(out)) == 11100
    # the 9 attribute columns are empty in the data row
    wb = openpyxl.load_workbook(str(out))
    ws = wb["Sarees"]
    for header in ["Prominent Colour", "Saree Fabric", "Blouse Fabric", "Type",
                   "Ornamentation", "Border", "Pattern", "Print or Pattern Type",
                   "Wash Care"]:
        col = t.col_index_by_header[header]
        assert ws.cell(row=t.first_data_row, column=col).value in (None, "")
    wb.close()
```

- [ ] **Step 2: Run test to verify it passes (expected) or fails (then fix)**

Run: `python -m pytest tests/test_dropdowns.py::test_v13_output_preserves_plain_dropdowns_and_blanks_attributes -v`
Expected: PASS with no code change — openpyxl preserves plain validations and `_shared_to_inline` only rewrites `<c t="s">` cells. If it unexpectedly FAILS on the dropdown count, the `_shared_to_inline` zip step is dropping the `<dataValidations>` block — fix `src/myntra/fill.py` `_shared_to_inline` to leave any part other than cell `<c>` elements byte-for-byte unchanged (it already only substitutes `<c ... t="s">…</c>`; verify the regex is not matching across the validations block), then re-run.

- [ ] **Step 3: (only if Step 2 failed) implement the fix, else skip**

No change expected. If needed, scope the `_shared_to_inline` substitution strictly to cell elements as described above.

- [ ] **Step 4: Run the existing dropdown tests to confirm no regression**

Run: `python -m pytest tests/test_dropdowns.py -v`
Expected: PASS — the 06-16 x14 tests and the new V13 test all pass.

- [ ] **Step 5: Commit**

```bash
git add tests/test_dropdowns.py src/myntra/fill.py
git commit -m "test(fill): lock V13 output keeps plain dropdowns and blanks attributes

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Preview reconstruction logic (pure functions)

Reconstruct Myntra's approximate title/design-details from a row's attributes, flag blanks, and read a filled workbook's rows by header.

**Files:**
- Create: `src/myntra/preview.py`
- Test: `tests/test_preview.py`

**Interfaces:**
- Consumes: `TemplateInfo` (for `read_filled_rows`).
- Produces:
  - `is_set(value) -> bool`
  - `reconstruct_title(attrs: dict) -> str`
  - `reconstruct_design_details(attrs: dict) -> list[str]`
  - `missing_attributes(attrs: dict, user_filled: list[str]) -> list[str]`
  - `read_filled_rows(xlsx_path: str, template: TemplateInfo) -> list[dict]` — one `{header: value_or_None}` dict per data row that has a `vendorSkuCode`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_preview.py`:

```python
from src.myntra.preview import (is_set, reconstruct_title,
                                reconstruct_design_details, missing_attributes,
                                read_filled_rows)


def test_is_set_treats_blank_and_na_as_unset():
    assert is_set("Blue")
    assert not is_set("")
    assert not is_set(None)
    assert not is_set("NA")
    assert not is_set(" na ")


def test_reconstruct_title_full_with_blouse_piece():
    attrs = {"Print or Pattern Type": "Floral", "Ornamentation": "Zari",
             "Saree Fabric": "Pure Silk", "Type": "Banarasi",
             "Blouse Fabric": "Pure Silk"}
    assert reconstruct_title(attrs) == \
        "Floral Zari Pure Silk Banarasi Saree With Unstitched Blouse Piece"


def test_reconstruct_title_skips_unset_and_na():
    attrs = {"Type": "NA", "Saree Fabric": "", "Ornamentation": None,
             "Blouse Fabric": ""}
    assert reconstruct_title(attrs) == "Saree"


def test_reconstruct_design_details_three_lines():
    attrs = {"Prominent Colour": "Blue", "Type": "Banarasi", "Pattern": "Solid",
             "Border": "Solid", "Ornamentation": "Zari"}
    dd = reconstruct_design_details(attrs)
    assert dd == ["Blue Banarasi sarees", "Solid saree with Solid Border",
                  "Has Zari detail"]


def test_reconstruct_design_details_minimal():
    attrs = {"Prominent Colour": "Red"}
    assert reconstruct_design_details(attrs) == ["Red sarees"]


def test_missing_attributes_flags_blank_and_na():
    uf = ["Type", "Border", "Prominent Colour"]
    attrs = {"Type": "Banarasi", "Border": "NA", "Prominent Colour": ""}
    assert missing_attributes(attrs, uf) == ["Border", "Prominent Colour"]


def test_read_filled_rows_reads_by_header(tmp_path):
    from src.myntra.template_reader import read_template
    from src.myntra.fill import fill_template
    from src.core.models import MappedRow, ImageResult
    V13 = "templates/myntra/Myntra-Sku-Template-2026-07-24.xlsx"
    t = read_template(V13)
    row = MappedRow(sku="S1", cells={"vendorSkuCode": "S1", "Type": "Banarasi",
                                     "Prominent Colour": "Blue"})
    out = tmp_path / "filled.xlsx"
    fill_template(V13, t, [(row, ImageResult(sku="S1"))], str(out))
    rows = read_filled_rows(str(out), t)
    assert len(rows) == 1
    assert rows[0]["vendorSkuCode"] == "S1"
    assert rows[0]["Type"] == "Banarasi"
    assert rows[0]["Prominent Colour"] == "Blue"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_preview.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.myntra.preview'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/myntra/preview.py`:

```python
"""Reconstruct Myntra's auto-generated title/design-details from a filled row's
attributes (approximate, for the in-app preview) and read a filled workbook.

Myntra generates the customer-facing title and 'Design Details' prose from the
attribute columns; the exact wording is not something we can guarantee, so these
reconstructions are labelled approximate in the UI. Specifications shown alongside
are exact (they are the values the user entered)."""
import warnings

import openpyxl

SHEET_SAREES_NAME = "Sarees"

# Title order, reverse-engineered from live IJOR listings (2026-07-24). Colour is
# NOT in the saree title. Only attributes that are set are included.
_TITLE_ORDER = ["Print or Pattern Type", "Ornamentation", "Saree Fabric", "Type"]


def is_set(value):
    """True unless the value is None, blank, or the literal 'NA' (case-insensitive)."""
    if value is None:
        return False
    s = str(value).strip()
    return s != "" and s.upper() != "NA"


def reconstruct_title(attrs):
    parts = [str(attrs.get(h)).strip() for h in _TITLE_ORDER if is_set(attrs.get(h))]
    parts.append("Saree")
    title = " ".join(parts)
    if is_set(attrs.get("Blouse Fabric")):
        title += " With Unstitched Blouse Piece"
    return title


def reconstruct_design_details(attrs):
    lines = []
    colour, typ = attrs.get("Prominent Colour"), attrs.get("Type")
    if is_set(colour):
        l1 = str(colour).strip()
        if is_set(typ):
            l1 += " " + str(typ).strip()
        lines.append(l1 + " sarees")
    pattern, border = attrs.get("Pattern"), attrs.get("Border")
    if is_set(pattern) or is_set(border):
        p = str(pattern).strip() if is_set(pattern) else ""
        b = str(border).strip() if is_set(border) else ""
        lines.append(f"{p} saree with {b} Border".replace("  ", " ").strip())
    orn = attrs.get("Ornamentation")
    if is_set(orn):
        lines.append(f"Has {str(orn).strip()} detail")
    return lines


def missing_attributes(attrs, user_filled):
    return [h for h in user_filled if not is_set(attrs.get(h))]


def read_filled_rows(xlsx_path, template):
    """One {header: value_or_None} dict per data row that carries a vendorSkuCode."""
    warnings.filterwarnings("ignore")
    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    ws = wb[SHEET_SAREES_NAME]
    sku_col = template.col_index_by_header.get("vendorSkuCode")
    rows = []
    for r in range(template.first_data_row, ws.max_row + 1):
        if sku_col is None or not is_set(ws.cell(row=r, column=sku_col).value):
            continue
        cells = {}
        for header, col in template.col_index_by_header.items():
            v = ws.cell(row=r, column=col).value
            cells[header] = None if v is None else str(v).strip()
        rows.append(cells)
    wb.close()
    return rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_preview.py -v`
Expected: PASS (all 7).

- [ ] **Step 5: Commit**

```bash
git add src/myntra/preview.py tests/test_preview.py
git commit -m "feat(preview): reconstruct Myntra title/design-details + read filled rows

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Preview web route + templates

A read-only `/preview` surface: upload the filled workbook, render one card per product (exact specs + labelled-approximate title/design-details + missing-attribute flags). Link it from the generate result screen.

**Files:**
- Create: `src/web/routers/preview.py`
- Create: `src/web/templates/preview.html`
- Create: `src/web/templates/_preview.html`
- Modify: `src/web/main.py` (register the router)
- Modify: `src/web/templates/_result.html` (link to the preview)
- Test: `tests/web/test_preview.py`

**Interfaces:**
- Consumes: `read_template`, and `read_filled_rows`, `reconstruct_title`, `reconstruct_design_details`, `missing_attributes` from Task 6; `get_user` from `src.web.routers.pages`.
- Produces: routes `GET /preview` and `POST /preview`.

- [ ] **Step 1: Write the failing test**

Create `tests/web/test_preview.py`:

```python
from fastapi.testclient import TestClient

from src.web.main import create_app
from src.web.settings import Settings
from src.myntra.template_reader import read_template
from src.myntra.fill import fill_template
from src.core.models import MappedRow, ImageResult

V13 = "templates/myntra/Myntra-Sku-Template-2026-07-24.xlsx"


def _client(tmp_path):
    s = Settings(auth_disabled=True, s3_bucket="b",
                 ledger_local_path=str(tmp_path / "led.json"),
                 hsn_local_path=str(tmp_path / "hsn.json"),
                 sku_registry_local_path=str(tmp_path / "reg.json"))
    return TestClient(create_app(s))


def _filled(tmp_path):
    t = read_template(V13)
    row = MappedRow(sku="S1", cells={
        "vendorSkuCode": "S1", "Type": "Banarasi", "Saree Fabric": "Pure Silk",
        "Ornamentation": "Zari", "Print or Pattern Type": "Floral",
        "Prominent Colour": "Blue", "Pattern": "Solid", "Border": "Solid",
        "Blouse Fabric": "Pure Silk"})
    out = tmp_path / "filled.xlsx"
    fill_template(V13, t, [(row, ImageResult(sku="S1"))], str(out))
    return out


def test_preview_form_renders(tmp_path):
    r = _client(tmp_path).get("/preview")
    assert r.status_code == 200
    assert "Preview" in r.text


def test_preview_shows_reconstruction_and_specs(tmp_path):
    out = _filled(tmp_path)
    client = _client(tmp_path)
    with open(out, "rb") as fh:
        r = client.post("/preview", files={"file": (
            "filled.xlsx", fh.read(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 200
    assert "Floral Zari Pure Silk Banarasi Saree With Unstitched Blouse Piece" in r.text
    assert "Blue Banarasi sarees" in r.text
    assert "Wash Care" in r.text          # a spec row label is shown
    assert "Wash Care" in r.text


def test_preview_rejects_non_xlsx(tmp_path):
    r = _client(tmp_path).post(
        "/preview", files={"file": ("x.csv", b"a,b", "text/csv")})
    assert r.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/web/test_preview.py -v`
Expected: FAIL — 404 on `/preview` (route not registered).

- [ ] **Step 3: Write minimal implementation**

Create `src/web/routers/preview.py`:

```python
import os
import tempfile

import yaml
from fastapi import APIRouter, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse

from src.myntra.template_reader import read_template
from src.myntra.preview import (read_filled_rows, reconstruct_title,
                                reconstruct_design_details, missing_attributes)
from src.web.routers.pages import get_user

router = APIRouter()
TEMPLATE = os.path.join("templates", "myntra", "Myntra-Sku-Template-2026-07-24.xlsx")
_FALLBACK_USER_FILLED = [
    "Prominent Colour", "Saree Fabric", "Blouse Fabric", "Type", "Ornamentation",
    "Border", "Pattern", "Print or Pattern Type", "Wash Care"]


def _templates():
    from src.web.main import templates
    return templates


def _user_filled():
    with open(os.path.join("config", "myntra", "rules.yaml"), encoding="utf-8") as fh:
        rules = yaml.safe_load(fh)
    return rules.get("user_filled_attributes") or _FALLBACK_USER_FILLED


@router.get("/preview", response_class=HTMLResponse)
def preview_form(request: Request):
    get_user(request)
    return _templates().TemplateResponse(request, "preview.html", {"user": get_user(request)})


@router.post("/preview", response_class=HTMLResponse)
async def preview_submit(request: Request, file: UploadFile = File(...)):
    get_user(request)
    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Please upload the filled .xlsx file")
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    with open(path, "wb") as out:
        out.write(await file.read())
    try:
        template = read_template(TEMPLATE)
        rows = read_filled_rows(path, template)
    finally:
        os.remove(path)
    user_filled = _user_filled()
    cards = [{
        "sku": attrs.get("vendorSkuCode") or attrs.get("SKUCode") or "",
        "title": reconstruct_title(attrs),
        "design_details": reconstruct_design_details(attrs),
        "specs": [(h, attrs.get(h)) for h in user_filled],
        "missing": missing_attributes(attrs, user_filled),
        "front_image": attrs.get("Front Image"),
    } for attrs in rows]
    return _templates().TemplateResponse(request, "_preview.html", {"cards": cards})
```

Create `src/web/templates/preview.html`:

```html
{% extends "base.html" %}
{% block content %}
<div class="panel">
  <h2>Preview Myntra listings</h2>
  <p class="hint">Fill the attribute columns (Colour, Fabric, Type, Ornamentation,
    Border, Pattern, Print/Pattern, Wash Care) in the downloaded Excel file, then
    upload it here to see how each listing will look on Myntra before you upload it there.</p>
  <form hx-post="/preview" hx-target="#preview-out" hx-swap="innerHTML"
        hx-encoding="multipart/form-data">
    <input type="file" name="file" accept=".xlsx" required>
    <button class="btn" type="submit">Preview listings →</button>
  </form>
  <div id="preview-out"></div>
</div>
{% endblock %}
```

Create `src/web/templates/_preview.html`:

```html
<div class="panel">
  <h3 class="ok">Preview — {{ cards | length }} product(s)</h3>
  <p class="flag mono"><strong>⚠ Title &amp; Design Details are auto-generated by Myntra
    from the attributes — this is our best reconstruction, not guaranteed word-for-word.
    Specifications are exact.</strong></p>
  {% for c in cards %}
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
  {% endfor %}
</div>
```

In `src/web/main.py`, register the router — change the import line (39) and add an include:

```python
    from src.web.routers import pages, generate, fix, auth_routes, preview
    app.include_router(pages.router)
    app.include_router(generate.router)
    app.include_router(fix.router)
    app.include_router(auth_routes.router)
    app.include_router(preview.router)
```

In `src/web/templates/_result.html`, add a link to the preview just after the download button (after the `<a class="btn" href="/generate/download/{{ job.id }}">⬇ Download xlsx</a>` line):

```html
    <p class="hint" style="margin-top:12px">After filling the attribute dropdowns in Excel,
      <a href="/preview">preview the Myntra listings →</a> before uploading to Myntra.</p>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/web/test_preview.py -v`
Expected: PASS (all 4).

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS. If `tests/web/test_pages.py` asserts an exact set of routes or nav links, update it to include `/preview`.

- [ ] **Step 6: Commit**

```bash
git add src/web/routers/preview.py src/web/templates/preview.html src/web/templates/_preview.html src/web/main.py src/web/templates/_result.html tests/web/test_preview.py
git commit -m "feat(web): round-trip listing preview from the filled Excel file

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Final manual verification (owner-run, not a code task)

Myntra acceptance cannot be tested in code. After the tasks above are green, the owner:
1. Runs one real generate batch → downloads `myntra_filled.xlsx`.
2. Opens it in Excel, fills the 9 attribute columns via the dropdowns, saves.
3. Uploads the filled file to `/preview` → sanity-checks the cards.
4. Uploads the **same** file to Myntra → confirms it is accepted.

If Myntra rejects it, do not weaken the tests — capture the exact Myntra error and open a follow-up (candidate causes: a header the V13 template renamed vs. Myntra's importer, or a validation artefact from the Excel re-save).

## Self-Review

**Spec coverage:**
- §3.1 nine blank columns → Task 4 (list) + Task 5 (verified blank in output). ✓
- §3.2 stop colour/fabric/constant writes, keep fabric_detection for HSN → Task 4. ✓
- §3.3 no pre-fill (fully blank) → Task 4. ✓
- §3.4 template switch + openpyxl preserves plain dropdowns → Task 2 + Task 1 (reader) + Task 5. ✓
- §3.5 header-compat guard → Task 3. ✓
- §4.1 round-trip re-upload flow, read-only → Task 7. ✓
- §4.2 specs exact + title/DD approximate with badge → Task 6 (logic) + Task 7 (badge in `_preview.html`). ✓
- §4.3 missing-attribute check → Task 6 `missing_attributes` + Task 7 card. ✓
- §5 dropdown-preservation + `_shared_to_inline` non-interference → Task 5. ✓
- §5 owner upload test → "Final manual verification" section. ✓
- §6 testing items (mapper no longer emits attributes; V13 dropdowns retained; guard; reconstruction; read-only) → Tasks 4/5/3/6/7. ✓

**Placeholder scan:** none — every code/test step contains full code and exact commands.

**Type consistency:** `DEFAULT_TEMPLATE_NAME` (str) consistent across Tasks 2/3. `assert_template_compatible(template, column_map, constants)` / `TemplateIncompatibleError` consistent Task 3. `user_filled_attributes` (list[str]) consistent Tasks 4/6/7. Preview functions `is_set`, `reconstruct_title`, `reconstruct_design_details`, `missing_attributes`, `read_filled_rows` named identically in Tasks 6 and 7. Preview card keys (`sku`, `title`, `design_details`, `specs`, `missing`, `front_image`) match between `preview.py` and `_preview.html`.
