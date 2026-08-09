# HSN from the Shopify metafield — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read each product's HSN from the Shopify export's `custom.hsn_code` metafield, and move the review of it onto the existing per-SKU attribute screen, deleting the mid-build HSN question entirely.

**Architecture:** HSN flows export → `Product.hsn` → `hsn_source.normalize()` → `map_product(hsn=...)` → the sheet. Where the export has no usable code the cell is left blank and the attribute screen shows the gap loudly, so it is filled by hand before download. The signature-keyed HSN knowledge base is unwired but kept on disk.

**Tech Stack:** Python 3, pandas (CSV read), openpyxl (workbook read/write), FastAPI + Jinja2 + htmx (web), pytest.

**Spec:** [docs/superpowers/specs/2026-08-09-hsn-from-shopify-metafield-design.md](../specs/2026-08-09-hsn-from-shopify-metafield-design.md)

## Global Constraints

- The export header string is exactly `HSN Code (product.metafields.custom.hsn_code)`. Do not shorten, guess, or re-derive it.
- A usable HSN is exactly 8 digits after stripping surrounding whitespace: `\d{8}`. Anything else is treated as missing, never as an error during the build.
- `src/myntra/hsn_kb.py`, `src/myntra/signature.py`, `settings.hsn_store` and `HSN_LOCAL_PATH` stay on disk and their tests (`tests/test_hsn_kb.py`, `tests/test_signature.py`) must keep passing. Do not delete them.
- `sku_registry.content_hash` already excludes HSN via `_EXCLUDE = ("styleGroupId", "HSN")`. Do not add HSN to the hash and do not "fix" `scan_content_hashes` beyond its docstring.
- `hsn_override` (per-SKU pin from the SKU registry, used by the fix-flow rebuild) always beats the export value. Do not reorder this.
- Run the full suite with `python -m pytest -q` from the repo root. It must stay green at every commit.
- Work on branch `feat/hsn-from-shopify-metafield`. Commit after each task. Do not merge to `main` and do not push.

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `src/core/shopify_reader.py` | Knows the export's column names; adds `HSN_COL` | 1 |
| `src/core/models.py` | `Product.hsn` field | 1 |
| `src/myntra/hsn_source.py` (new) | The single definition of "a usable HSN". Pure; no web, jobs, or Shopify knowledge | 2 |
| `src/myntra/mapper.py` | Writes HSN into the row from `hsn` / `hsn_override` | 3 |
| `src/myntra/pipeline.py` | Threads the normalised export value per product | 3 |
| `src/web/routers/generate.py` | Loses the HSN pre-scan, route, and `hsn.json` | 3 |
| `src/myntra/sku_registry.py` | `update_hsn()` narrow updater | 4 |
| `src/web/routers/attributes.py` | Parses, validates, saves HSN; counts gaps; updates the registry | 5 |
| `src/myntra/attribute_entry.py` | `HSN_HEADER`, `validate_hsn()` | 5 |
| `src/web/templates/_attr_panel.html`, `attributes.html`, `_hsn_gap.html` (new), `_attr_saved.html`, `_attr_panel_saved.html` | The field, the banner, the out-of-band refresh | 5 |
| `docs/APP-FEATURES-GUIDE.md`, `docs/ARCHITECTURE.md`, `src/myntra/hsn_kb.py` | Documentation and the retained-not-wired note | 6 |

**Why Tasks 3 and 5 are large.** Changing `map_product`'s signature breaks its
callers until they are all updated, so the mapper, the pipeline and the generate
router are one refactor and must land in one commit to keep the suite green.
Likewise the attribute router and its templates: the router's new context
variables are meaningless until the templates render them. Splitting either pair
would leave a commit with a red suite, which the "green at every commit"
constraint forbids.

---

### Task 1: Read the HSN column out of the export

**Files:**
- Modify: `src/core/shopify_reader.py:5-7` (constants), `:42-56` (Product construction)
- Modify: `src/core/models.py:4-18`
- Test: `tests/test_shopify_reader.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `shopify_reader.HSN_COL: str`; `Product.hsn: str | None` — the **raw** cell value, not normalised. Normalisation is Task 2's job and happens at the pipeline, not the reader.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_shopify_reader.py`:

```python
def _write_csv(tmp_path, header_extra="", value_extra=""):
    """A one-product export. header_extra/value_extra append one trailing column."""
    p = tmp_path / "export.csv"
    p.write_text(
        "Handle,Title,Variant SKU,Image Src,Image Position" + header_extra + "\n"
        "h1,Product One,SKU1,https://cdn.example/a.webp,1" + value_extra + "\n",
        encoding="utf-8")
    return str(p)


def test_hsn_read_from_the_metafield_column(tmp_path):
    from src.core.shopify_reader import HSN_COL
    assert HSN_COL == "HSN Code (product.metafields.custom.hsn_code)"
    path = _write_csv(tmp_path, "," + HSN_COL, ",54075240")
    assert read_products(path)[0].hsn == "54075240"


def test_hsn_is_none_when_the_column_is_absent(tmp_path):
    # The older 61-column export has no HSN column at all; it must still read.
    assert read_products(_write_csv(tmp_path))[0].hsn is None


def test_hsn_is_returned_raw_not_normalised(tmp_path):
    # Two real products export as "52085990 " with a trailing space. The reader
    # hands the value over untouched; hsn_source.normalize owns the cleaning.
    from src.core.shopify_reader import HSN_COL
    path = _write_csv(tmp_path, "," + HSN_COL, ",52085990 ")
    assert read_products(path)[0].hsn == "52085990 "
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_shopify_reader.py -q`
Expected: FAIL — `ImportError: cannot import name 'HSN_COL'`.

- [ ] **Step 3: Add the field to the model**

In `src/core/models.py`, add `hsn` to `Product` **after** `images`. It must carry a
default, because `images` already has one and a dataclass cannot put a
defaultless field after a defaulted one:

```python
@dataclass
class Product:
    handle: str
    sku: str
    title: str
    vendor: str
    tags: str
    body_html: str
    price: float | None
    compare_at_price: float | None
    color: str | None
    fabric: str | None
    size: str | None
    status: str | None
    images: list[str] = field(default_factory=list)
    # Raw cell from the export's custom.hsn_code metafield. Normalised by
    # src/myntra/hsn_source.py at build time, never here.
    hsn: str | None = None
```

- [ ] **Step 4: Read the column**

In `src/core/shopify_reader.py`, add the constant beside the existing three:

```python
COLOR_COL = "Color (product.metafields.shopify.color-pattern)"
FABRIC_COL = "Fabric (product.metafields.shopify.fabric)"
SIZE_COL = "Size (product.metafields.shopify.size)"
HSN_COL = "HSN Code (product.metafields.custom.hsn_code)"
```

and one line in the `Product(...)` construction, after `status=fv("Status"),`:

```python
            status=fv("Status"),
            hsn=fv(HSN_COL),
            images=urls,
```

`fv()` already returns `None` for a column that is not in the frame, so an export
without the column needs no special case.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_shopify_reader.py -q`
Expected: PASS (5 tests).

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -q`
Expected: all pass. Nothing else reads `Product.hsn` yet.

- [ ] **Step 7: Commit**

```bash
git add src/core/shopify_reader.py src/core/models.py tests/test_shopify_reader.py
git commit -m "feat(reader): read HSN from the custom.hsn_code metafield column"
```

---

### Task 2: `hsn_source.normalize()` — the one definition of a usable HSN

**Files:**
- Create: `src/myntra/hsn_source.py`
- Test: `tests/test_hsn_source.py` (new)

**Interfaces:**
- Consumes: nothing.
- Produces: `normalize(raw) -> str | None`. Returns the stripped 8-digit string, or `None` for anything else. Used by Task 3 (mapper and pipeline) and Task 5 (attribute validation and gap counting).

- [ ] **Step 1: Write the failing test**

Create `tests/test_hsn_source.py`:

```python
import pytest

from src.myntra.hsn_source import normalize


@pytest.mark.parametrize("raw, expected", [
    ("54075240", "54075240"),          # the common saree code
    ("52085990 ", "52085990"),         # two real products export a trailing space
    ("  52084121  ", "52084121"),      # leading space too
])
def test_usable_codes_are_stripped_and_returned(raw, expected):
    assert normalize(raw) == expected


@pytest.mark.parametrize("raw", [
    None,                # column absent
    "",                  # metafield not filled
    "   ",               # whitespace only
    "5407",              # 4-digit chapter heading, not a full HSN
    "540752401",         # 9 digits
    "6211.42.90",        # punctuated
    "5407524a",          # letter
    "abc",               # stray text
])
def test_unusable_values_are_treated_as_missing(raw):
    assert normalize(raw) is None


def test_a_non_string_is_accepted_and_normalised():
    # openpyxl hands back HSN as an int, because it is in fill.NUMERIC_HEADERS.
    assert normalize(54075240) == "54075240"


def test_missing_is_none_not_an_exception():
    # A malformed code is a gap to fill on the attribute screen, never a crash
    # mid-build. This is why normalize returns rather than raises.
    assert normalize("nonsense") is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_hsn_source.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.myntra.hsn_source'`.

- [ ] **Step 3: Write the implementation**

Create `src/myntra/hsn_source.py`:

```python
"""The one definition of "a usable HSN".

Deliberately pure: it knows nothing about web requests, jobs, or Shopify, so the
reader, the build, and the attribute screen can all enforce one rule from one
place. Myntra wants an 8-digit code; anything else is a gap for the seller to
fill on the attribute screen, which is why an unusable value comes back as None
rather than raising mid-build."""
import re

_EIGHT_DIGITS = re.compile(r"\d{8}")


def normalize(raw):
    """The stripped 8-digit code, or None if there isn't one.

    Accepts a non-string (openpyxl returns HSN as an int, since it is in
    fill.NUMERIC_HEADERS) and blank/None alike."""
    if raw is None:
        return None
    value = str(raw).strip()
    return value if _EIGHT_DIGITS.fullmatch(value) else None
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_hsn_source.py -q`
Expected: PASS (13 tests, counting the parametrised cases).

- [ ] **Step 5: Commit**

```bash
git add src/myntra/hsn_source.py tests/test_hsn_source.py
git commit -m "feat(hsn): hsn_source.normalize, the single 8-digit rule"
```

---

### Task 3: Swap the HSN source through the mapper, the pipeline and the router

**This is one refactor, one commit.** `map_product`'s signature change breaks
`pipeline.main`, which breaks `generate.py`. Write all the tests first, change
all three layers, then commit once with the suite green. Do **not** commit after
each layer — an intermediate commit would leave the suite red.

**Files:**
- Modify: `src/myntra/mapper.py:1-4` (imports), `:113-114` (signature), `:153-172` (the HSN block)
- Modify: `src/myntra/pipeline.py:30-33` (signature), `:71-75` (the map call), `:120-132` (`scan_content_hashes`)
- Modify: `src/web/routers/generate.py:15,21` (imports), `:101-135`, `:138-149`, `:152-187`, `:205-206`, `:235`, `:262-284`
- Delete: `src/web/templates/_hsn_review.html`
- Test: `tests/test_mapper.py:111-153`, `tests/test_pipeline_override.py:43-93`, `tests/web/test_generate.py`

**Interfaces:**
- Consumes: `hsn_source.normalize` (Task 2), `Product.hsn` (Task 1).
- Produces: `map_product(product, template, column_map, constants, rules=None, hsn=None, hsn_override=None)`; `pipeline.main(...)` with no `hsn_by_signature` parameter (`hsn_by_sku` unchanged); `_start_build(request, job, csv_path, job_dir, count, settings, only_skus=None, style_group_id_by_sku=None)`. `POST /generate/hsn/{job_id}` no longer exists.

- [ ] **Step 1: Rewrite the mapper's HSN tests**

In `tests/test_mapper.py`, **replace** the three tests at lines 111-153
(`test_silk_attributes_left_blank_but_hsn_signature_still_derivable`,
`test_hsn_set_from_injected_map`, `test_hsn_unresolved_signature_is_flagged_not_guessed`,
`test_fabric_block_no_longer_sets_hsn`) with:

These use the file's existing `_template_with_rules()` (defined at line 83, whose
headers already include `HSN`) and its module-level `FABRIC_RULES` dict. No
`articleType` constant is needed any more — nothing derives a signature:

```python
def test_hsn_written_from_the_export_value():
    p = Product(handle="h", sku="S1", title="Banarasi Silk Saree Blue", vendor="V",
                tags="", body_html="", price=3000.0, compare_at_price=None,
                color=None, fabric="silk", size=None, status="active", images=[])
    row = map_product(p, _template_with_rules(), {}, {}, FABRIC_RULES,
                      hsn="50072010")
    assert row.cells["HSN"] == "50072010"


def test_hsn_override_beats_the_export_value():
    # The fix-flow rebuild pins a listed SKU's original code; it must win.
    p = Product(handle="h", sku="S1", title="Banarasi Silk Saree Blue", vendor="V",
                tags="", body_html="", price=3000.0, compare_at_price=None,
                color=None, fabric="silk", size=None, status="active", images=[])
    row = map_product(p, _template_with_rules(), {}, {}, FABRIC_RULES,
                      hsn="50072010", hsn_override="99999999")
    assert row.cells["HSN"] == "99999999"


def test_no_hsn_leaves_the_cell_blank_and_raises_no_flag():
    # The CLI and dedup-scan paths run without HSN. A gap is surfaced by the
    # attribute screen now, so a build-time flag would only be noise.
    p = Product(handle="h", sku="S2", title="Plain Saree", vendor="V", tags="",
                body_html="", price=100.0, compare_at_price=None, color=None,
                fabric=None, size=None, status="active", images=[])
    row = map_product(p, _template_with_rules(), {}, {}, FABRIC_RULES)
    assert "HSN" not in row.cells
    assert not any(f.field == "HSN" for f in row.flags)
```

In `tests/test_pipeline_override.py`, `test_hsn_override_wins_in_mapper`
(lines 80-93) builds its own two-header template inline. Change only its
`map_product` call to drop the removed keyword:

```python
    row = map_product(p, t, {}, {"articleType": "Sarees"}, rules,
                      hsn="50072010",
                      hsn_override="99999999")
    assert row.cells["HSN"] == "99999999"
```

- [ ] **Step 2: Rewrite the pipeline's HSN tests**

(The code for this step is under "Task 3 — pipeline tests" further down; write
these tests now, before changing any source.)

- [ ] **Step 3: Rewrite the web tests**

(The code for this step is under "Task 3 — web tests" further down.)

- [ ] **Step 4: Run all three test files to verify they fail**

Run: `python -m pytest tests/test_mapper.py tests/test_pipeline_override.py tests/web/test_generate.py -q`
Expected: FAIL — `TypeError: map_product() got an unexpected keyword argument 'hsn'`,
and the web tests still find the HSN screen.

- [ ] **Step 5: Change the mapper**

In `src/myntra/mapper.py`, delete the import on line 4:

```python
from src.myntra.hsn_kb import signature      # DELETE this line
```

Change the signature on lines 113-114:

```python
def map_product(product, template, column_map, constants, rules=None, hsn=None,
                hsn_override=None):
```

Replace lines 153-172 (steps 5 and 5b) with:

```python
    # 5. HSN. A pinned per-SKU code (hsn_override, e.g. a rebuild from the SKU
    # registry) wins; otherwise the code the Shopify export carried, already
    # normalised by the caller. Neither — the CLI path and the dedup scan —
    # leaves HSN blank and raises NO flag: a missing code is surfaced on the
    # attribute screen now, so flagging it here would only be noise.
    if hsn_override:
        _set(row, template, "HSN", str(hsn_override))
    elif hsn:
        _set(row, template, "HSN", str(hsn))
```

Note `fabric_cfg = rules.get("fabric_detection") or {}` goes with it — it existed
only to feed the removed signature. Confirm nothing else in `map_product` reads
`fabric_cfg` before deleting it (`grep -n fabric_cfg src/myntra/mapper.py` must
come back empty afterwards).

#### Task 3 — pipeline tests

This is the code for **Step 2** above. Write it before changing any source.

In `tests/test_pipeline_override.py`, replace `test_hsn_by_signature_written_to_sheet`
(lines 43-60) and `test_no_hsn_map_leaves_hsn_blank` (lines 63-77) with the code
below. It reuses the file's existing `_fake_fetch()` (line 17) and its inline
`openpyxl.load_workbook(...)["Sarees"]` + `hdr` idiom.

The shared fixture `tests/fixtures/products_export.csv` has no HSN column and
**must not gain one** — `tests/test_shopify_reader.py` and
`tests/test_end_to_end.py` both read it. Write a tmp CSV of the same shape
instead, via this new helper placed after `_fake_fetch`:

```python
def _csv_with_hsn(tmp_path, rows):
    """The shared fixture's columns plus the HSN metafield column."""
    from src.core.shopify_reader import HSN_COL
    path = tmp_path / "export.csv"
    header = ("Handle,Title,Vendor,Tags,Body (HTML),Variant SKU,Variant Price,"
              "Variant Compare At Price,Image Src,Image Position,Status,"
              "Color (product.metafields.shopify.color-pattern),"
              "Fabric (product.metafields.shopify.fabric),"
              "Size (product.metafields.shopify.size)," + HSN_COL + "\n")
    path.write_text(header + "".join(rows), encoding="utf-8")
    return str(path)


def test_export_hsn_written_to_sheet(tmp_path):
    warnings.filterwarnings("ignore")
    out = tmp_path / "out"
    csv = _csv_with_hsn(tmp_path, [
        "h1,Cotton Saree,V,,,S1,1200,1500,https://example.com/a.webp,1,active,red,cotton,Free,52081120\n",
        "h2,Silk Saree,V,,,S2,2500,,https://example.com/b.webp,1,active,blue,silk,Free,50072010 \n",
    ])
    main(template_path="templates/myntra/Myntra-Sku-Template-2026-06-16.xlsx",
         csv_path=csv, out_dir=str(out), config_dir="config/myntra",
         fetch=_fake_fetch(), upload=False)
    ws = openpyxl.load_workbook(out / "myntra_filled.xlsx")["Sarees"]
    hdr = {ws.cell(3, c).value: c for c in range(1, ws.max_column + 1)}
    # HSN is in NUMERIC_HEADERS -> written as an integer cell
    assert ws.cell(4, hdr["HSN"]).value == 52081120
    assert ws.cell(5, hdr["HSN"]).value == 50072010     # trailing space normalised


def test_missing_or_malformed_hsn_leaves_the_cell_blank(tmp_path):
    warnings.filterwarnings("ignore")
    out = tmp_path / "out"
    csv = _csv_with_hsn(tmp_path, [
        "h1,Cotton Saree,V,,,S1,1200,1500,https://example.com/a.webp,1,active,red,cotton,Free,\n",
        "h2,Silk Saree,V,,,S2,2500,,https://example.com/b.webp,1,active,blue,silk,Free,5407\n",
    ])
    main(template_path="templates/myntra/Myntra-Sku-Template-2026-06-16.xlsx",
         csv_path=csv, out_dir=str(out), config_dir="config/myntra",
         fetch=_fake_fetch(), upload=False)
    ws = openpyxl.load_workbook(out / "myntra_filled.xlsx")["Sarees"]
    hdr = {ws.cell(3, c).value: c for c in range(1, ws.max_column + 1)}
    assert ws.cell(4, hdr["HSN"]).value in (None, "")   # metafield not filled
    assert ws.cell(5, hdr["HSN"]).value in (None, "")   # 4 digits, not usable
```

Add the regression pin the spec calls for. `scan_content_hashes` takes no
template argument — it resolves the default V13 template itself:

```python
def test_content_hash_is_unaffected_by_hsn(tmp_path):
    """The duplicate-generation guard must not notice HSN.

    sku_registry._EXCLUDE already drops it from the fingerprint. If that ever
    changed, every SKU already in the registry would hash differently and the
    next upload would report the whole catalogue as "edited"."""
    from src.myntra.pipeline import scan_content_hashes
    row = ("h1,Cotton Saree,V,,,S1,1200,1500,https://example.com/a.webp,1,"
           "active,red,cotton,Free,")
    with_hsn = _csv_with_hsn(tmp_path / "b", [row + "52081120\n"])
    without = _csv_with_hsn(tmp_path / "a", [row + "\n"])
    assert scan_content_hashes(without) == scan_content_hashes(with_hsn)
```

`_csv_with_hsn` writes to `tmp_path / "export.csv"`, so the two calls need
different parent directories; create them first with
`(tmp_path / "a").mkdir()` and `(tmp_path / "b").mkdir()` at the top of the test.

- [ ] **Step 6: Thread the value through the pipeline**

In `src/myntra/pipeline.py`, add the import beside the others at the top:

```python
from src.myntra.hsn_source import normalize as normalize_hsn
```

Drop the parameter from the signature (lines 30-33):

```python
def main(template_path=None, csv_path=None, out_dir="output", config_dir="config/myntra",
         fetch=None, upload=None, style_group_id_start=None,
         only_skus=None, style_group_id_by_sku=None, hsn_by_sku=None,
         should_cancel=None):
```

Change the map call (lines 73-75):

```python
        mapped = map_product(p, template, column_map, constants, rules,
                             hsn=normalize_hsn(p.hsn),
                             hsn_override=hsn_by_sku.get(p.sku))
```

In `scan_content_hashes`, drop the removed keyword and correct the docstring to
name the real protection:

```python
def scan_content_hashes(csv_path, template_path=None, config_dir="config/myntra"):
    """(sku, content_hash) per product, with no image work — the upload-time
    input to the duplicate-generation guard.

    HSN is deliberately not passed, but that is belt-and-braces: the fingerprint
    itself excludes HSN (sku_registry._EXCLUDE), so a populated code can never
    shift a hash and make an already-listed SKU read as "edited"."""
    ...
    for p in read_products(csv_path):
        mapped = map_product(p, template, column_map, constants, rules)
```

#### Task 3 — web tests

This is the code for **Step 3** above. Write it before changing any source.

In `tests/web/test_generate.py`, replace the `_pass_hsn_and_wait` helper
(lines 19-29) with a plain poll:

```python
def _wait(client, job_id):
    """Poll until the sheet is ready. There is no HSN question any more — the
    build starts as soon as the duplicate guard is satisfied."""
    import time
    poll = client.get(f"/jobs/{job_id}")
    for _ in range(20):
        if "Download" in poll.text:
            return poll
        time.sleep(0.05)
        poll = client.get(f"/jobs/{job_id}")
    return poll
```

Replace every `_pass_hsn_and_wait(client, X)` call with `_wait(client, X)`, and
in `test_generate_runs_job_and_confirm_advances_ledger` replace the line

```python
    assert "One-time HSN" in r.text                 # pre-scan paused for HSN
```

with

```python
    assert "One-time HSN" not in r.text            # no HSN question any more
```

Delete `test_hsn_review_lists_signature_and_learns_on_submit` (line 140) and
`test_hsn_invalid_code_rerenders_with_error` (line 171) outright — both exercise
the removed route. Replace them with:

```python
def test_no_hsn_screen_and_the_route_is_gone(tmp_path, monkeypatch):
    client, _ = _client(tmp_path)

    def fake_main(csv_path=None, out_dir=None, style_group_id_start=None, **kw):
        assert "hsn_by_signature" not in kw       # the parameter is gone for good
        with open(f"{out_dir}/myntra_filled.xlsx", "wb") as fh:
            fh.write(b"xlsx-bytes")
        with open(f"{out_dir}/report.txt", "w") as fh:
            fh.write("1 rows\n")
        return {"filled": f"{out_dir}/myntra_filled.xlsx",
                "report": f"{out_dir}/report.txt", "products": 1, "uploaded": 0}

    monkeypatch.setattr(gen, "pipeline_main", fake_main)
    monkeypatch.setattr(gen, "count_products", lambda path: 1)

    csv = b"Handle,Title\na,A\n"
    r = client.post("/generate", files={"file": ("products_export.csv", csv, "text/csv")})
    assert r.status_code == 200
    assert "One-time HSN" not in r.text
    job_id = r.headers["x-job-id"]
    assert "Download" in _wait(client, job_id).text

    # The old route is gone, not merely unused.
    assert client.post(f"/generate/hsn/{job_id}", data={"hsn__0": "12345678"}
                       ).status_code == 404
```

- [ ] **Step 7: Strip the router**

In `src/web/routers/generate.py`:

Delete the KB import on line 15 entirely:

```python
from src.myntra.hsn_kb import signature, read_kb, suggest, learn   # DELETE
```

and drop `hsn_store` from line 21:

```python
from src.web.settings import ledger_store, sku_registry_store
```

Delete `_hsn_prescan_or_build` (lines 104-135) and the whole `hsn_submit` route
(lines 152-187). Simplify `_start_build` to drop the `hsn_by_signature` argument:

```python
def _start_build(request, job, csv_path, job_dir, count, settings,
                 only_skus=None, style_group_id_by_sku=None):
    start, batch_id = reserve(ledger_store(settings), count, "myntra_filled.xlsx")
    job.batch_id = batch_id
    job.range = [start, start + count - 1]
    job.status = "running"
    _spawn(job.id, csv_path, job_dir, start, settings, only_skus,
           style_group_id_by_sku)
    resp = _templates().TemplateResponse(
        request, "_stepper.html", {"job": job, "count": count})
    resp.headers["x-job-id"] = job.id
    return resp
```

Point the three former callers at `_start_build`:

- line 101 (`generate_submit`): `return _start_build(request, job, csv_path, job_dir, count, settings)`
- lines 205-206 (`generate_new_only`): `return _start_build(request, job, data["csv_path"], job_dir, len(only), settings, only_skus=only)`
- line 235 (`generate_continue_anyway`): the same substitution, keeping the `only_skus` / `style_group_id_by_sku` arguments it already passes.

Update `_spawn` and `_run_generate` (lines 262-284) to drop `hsn_by_signature`
from their parameter lists, from the thread `args` tuple, and from the
`pipeline_main(...)` call. Leave `hsn_by_sku` and `style_group_id_by_sku` alone.

- [ ] **Step 8: Delete the template**

```bash
git rm src/web/templates/_hsn_review.html
```

- [ ] **Step 9: Run the full suite — it must be green now**

Run: `python -m pytest -q`
Expected: all pass, including `tests/test_hsn_kb.py` and `tests/test_signature.py`
against the retained-but-unwired module.

If anything is still red, fix it before committing. This task's whole point is
that the three layers land together.

- [ ] **Step 10: Confirm nothing still references the removed pieces**

Run: `grep -rn "hsn_by_signature\|_hsn_review\|hsn_prescan\|generate/hsn" src/ tests/`
Expected: no output.

- [ ] **Step 11: Commit — once, for all three layers**

```bash
git add -A src/myntra/mapper.py src/myntra/pipeline.py src/web/routers/generate.py \
        src/web/templates tests/test_mapper.py tests/test_pipeline_override.py \
        tests/web/test_generate.py
git commit -m "feat(hsn): source HSN from the export, drop the mid-build question

map_product takes a plain hsn instead of a signature map; pipeline.main
normalises each product's metafield value; the generate router loses the
HSN pre-scan, the /generate/hsn route, _hsn_review.html and the per-job
hsn.json. One commit because the signature change breaks every caller
until they all move together."
```

---

### Task 4: `sku_registry.update_hsn()`

**Files:**
- Modify: `src/myntra/sku_registry.py:40+`
- Test: `tests/test_sku_registry.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `update_hsn(store, sku, hsn, key=REGISTRY_KEY) -> bool` — `True` if an existing entry was updated, `False` if the SKU is unknown. Task 5 calls it.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sku_registry.py`. It already defines a `FakeStore`
in-memory double at line 6 — use that, don't add another:

```python
def test_update_hsn_changes_only_that_field():
    store = FakeStore()
    record(store, "S1", "hash-1", 42, "50072010")
    assert update_hsn(store, "S1", "54075240") is True
    entry = read_registry(store)["S1"]
    assert entry["hsn"] == "54075240"
    assert entry["content_hash"] == "hash-1"      # untouched
    assert entry["style_group_id"] == 42          # untouched


def test_update_hsn_is_a_no_op_for_an_unknown_sku():
    store = FakeStore()
    assert update_hsn(store, "NEVER-BUILT", "54075240") is False
    assert read_registry(store) == {}             # no row invented


def test_update_hsn_accepts_none_to_clear():
    store = FakeStore()
    record(store, "S1", "hash-1", 42, "50072010")
    assert update_hsn(store, "S1", None) is True
    assert read_registry(store)["S1"]["hsn"] is None
```

Change the file's import line (line 3) to:

```python
from src.myntra.sku_registry import (content_hash, read_registry, partition,
                                     record, update_hsn)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_sku_registry.py -q`
Expected: FAIL — `ImportError: cannot import name 'update_hsn'`.

- [ ] **Step 3: Write the implementation**

Append to `src/myntra/sku_registry.py`:

```python
def update_hsn(store, sku, hsn, key=REGISTRY_KEY):
    """Correct the stored HSN of an already-built SKU. Returns whether it existed.

    The attribute screen can change an HSN after the build that recorded it. The
    fix flow's rebuild pins HSN *from here*, so without this the rebuild would
    quietly restore the stale build-time code.

    Deliberately does not create entries: only a completed build earns a registry
    row, and inventing one here would give the duplicate guard a SKU with no
    content hash to compare."""
    registry = read_registry(store, key)
    entry = registry.get(sku)
    if entry is None:
        return False
    entry["hsn"] = hsn
    store.put_json(key, registry)
    return True
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_sku_registry.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/myntra/sku_registry.py tests/test_sku_registry.py
git commit -m "feat(registry): update_hsn, a narrow post-build HSN correction"
```

---

### Task 5: HSN on the attribute screen

**This is one task, one commit.** The router's new context variables (`p.hsn`,
`p.hsn_missing`, `hsn_gaps`) are meaningless until the templates render them, and
the router tests assert on rendered HTML. Committing the router alone would leave
the suite red.

**Files:**
- Modify: `src/myntra/attribute_entry.py:19` (constants), new `validate_hsn`
- Modify: `src/web/routers/attributes.py` — `_panels`, `_filled_count`, `_submitted_hsn`, `_build_payload`, `_save_entries`, both save routes, `attributes_form`
- Create: `src/web/templates/_hsn_gap.html`
- Modify: `src/web/templates/_attr_panel.html:41-48`, `attributes.html:3-13`, `_attr_saved.html`, `_attr_panel_saved.html`
- Test: `tests/test_attribute_entry.py`, `tests/web/test_attributes.py`

**Interfaces:**
- Consumes: `hsn_source.normalize` (Task 2), `sku_registry.update_hsn` (Task 4).
- Produces: `attribute_entry.HSN_HEADER = "HSN"`; `attribute_entry.validate_hsn(raw) -> str | None` raising `AttributeValueError`; `attributes._hsn_gap_count(xlsx, template) -> int`; `_save_entries` returns a 5-tuple `(job, ordinals, payload, error, hsn_gaps)`; form field `hsn__{ordinal}`; the element `id="hsn-gap-banner"` refreshed out-of-band by both save templates.

- [ ] **Step 1: Write the failing validator test**

Append to `tests/test_attribute_entry.py`:

```python
def test_validate_hsn_accepts_eight_digits_and_strips():
    from src.myntra.attribute_entry import validate_hsn
    assert validate_hsn("54075240") == "54075240"
    assert validate_hsn(" 52085990 ") == "52085990"


def test_validate_hsn_blank_clears_the_cell():
    from src.myntra.attribute_entry import validate_hsn
    assert validate_hsn("") is None
    assert validate_hsn("   ") is None
    assert validate_hsn(None) is None


def test_validate_hsn_rejects_a_non_empty_bad_value():
    import pytest
    from src.myntra.attribute_entry import AttributeValueError, validate_hsn
    for bad in ("5407", "6211.42.90", "abc", "540752401"):
        with pytest.raises(AttributeValueError):
            validate_hsn(bad)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_attribute_entry.py -q`
Expected: FAIL — `ImportError: cannot import name 'validate_hsn'`.

- [ ] **Step 3: Add the constant and the validator**

In `src/myntra/attribute_entry.py`, add the import at the top:

```python
from src.myntra.hsn_source import normalize as normalize_hsn
```

and beside `BRAND_COLOUR_HEADER`:

```python
# Mandatory, free-text in the template, but validated — unlike `tags`, which
# accepts anything. Handled outside user_filled_attributes/_freetext so the
# "everything in the free-text list is unvalidated" invariant still holds.
HSN_HEADER = "HSN"
```

and the validator beside `validate_freetext`:

```python
def validate_hsn(raw):
    """Blank -> None (clears the cell). Non-blank must be a usable 8-digit code.

    Unlike a bad value read from the export — which normalize() reports as simply
    missing — a bad value *typed here* is a mistake worth showing, so this
    raises."""
    if raw is None or str(raw).strip() == "":
        return None
    value = normalize_hsn(raw)
    if value is None:
        raise AttributeValueError(
            f"HSN: '{str(raw).strip()}' is not an 8-digit code")
    return value
```

- [ ] **Step 4: Run it to verify it passes**

Run: `python -m pytest tests/test_attribute_entry.py -q`
Expected: PASS.

- [ ] **Step 5: Write the failing router tests**

In `tests/web/test_attributes.py`, extend the `_job` helper so a SKU can be given
a starting HSN — change its signature to
`def _job(tmp_path, monkeypatch, skus=("S1", "S2"), with_images=True, tags=None, hsn=None):`
and inside the row loop add:

```python
        if hsn is not None:
            cells["HSN"] = hsn
```

Then append:

```python
def test_hsn_renders_prefilled_from_the_sheet(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",), hsn="54075240")
    r = _client(tmp_path).get(f"/generate/attributes/{job.id}")
    assert 'name="hsn__0"' in r.text
    assert 'value="54075240"' in r.text


def test_hsn_gap_banner_counts_missing_codes(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1", "S2"))     # neither has an HSN
    r = _client(tmp_path).get(f"/generate/attributes/{job.id}")
    assert "2 SKUs still need an HSN" in r.text


def test_saving_a_valid_hsn_writes_it_and_clears_the_banner(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    client = _client(tmp_path)
    r = client.post(f"/generate/attributes/{job.id}/one",
                    data={"ordinal": 0, "sku__0": "S1", "hsn__0": "54075240"})
    assert r.status_code == 200
    assert "Saved" in r.text
    assert "Every SKU has an HSN" in r.text          # out-of-band banner refresh

    from src.myntra.preview import read_filled_rows
    from src.myntra.template_reader import read_template
    rows = read_filled_rows(job.result["filled"], read_template(V13))
    assert str(rows[0]["HSN"]) == "54075240"


def test_hsn_counts_toward_the_filled_total_of_fourteen(tmp_path, monkeypatch):
    # 12 dropdowns + tags + HSN. _filled_count is the single shared definition,
    # so the panel header and the save result cannot disagree.
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    r = _client(tmp_path).post(f"/generate/attributes/{job.id}/one",
                               data={"ordinal": 0, "sku__0": "S1",
                                     "hsn__0": "54075240"})
    assert "1/14 filled" in r.text


def test_saving_a_bad_hsn_is_rejected_and_writes_nothing(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",), hsn="54075240")
    client = _client(tmp_path)
    r = client.post(f"/generate/attributes/{job.id}/one",
                    data={"ordinal": 0, "sku__0": "S1", "hsn__0": "5407"})
    assert "Not saved" in r.text and "8-digit" in r.text

    from src.myntra.preview import read_filled_rows
    from src.myntra.template_reader import read_template
    rows = read_filled_rows(job.result["filled"], read_template(V13))
    assert str(rows[0]["HSN"]) == "54075240"        # unchanged


def test_per_panel_save_writes_only_the_requested_panels_hsn(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1", "S2"))
    client = _client(tmp_path)
    client.post(f"/generate/attributes/{job.id}/one",
                data={"ordinal": 1, "sku__0": "S1", "hsn__0": "11111111",
                      "sku__1": "S2", "hsn__1": "22222222"})

    from src.myntra.preview import read_filled_rows
    from src.myntra.template_reader import read_template
    rows = read_filled_rows(job.result["filled"], read_template(V13))
    assert rows[0]["HSN"] in (None, "")             # panel 0 untouched
    assert str(rows[1]["HSN"]) == "22222222"


def test_saving_an_hsn_corrects_the_sku_registry(tmp_path, monkeypatch):
    from src.myntra.sku_registry import read_registry, record
    from src.web.settings import Settings, sku_registry_store
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    s = Settings(auth_disabled=True, s3_bucket="b",
                 ledger_local_path=str(tmp_path / "led.json"),
                 hsn_local_path=str(tmp_path / "hsn.json"),
                 sku_registry_local_path=str(tmp_path / "reg.json"))
    record(sku_registry_store(s), "S1", "hash-1", 7, "50072010")

    from fastapi.testclient import TestClient
    from src.web.main import create_app
    TestClient(create_app(s)).post(
        f"/generate/attributes/{job.id}/one",
        data={"ordinal": 0, "sku__0": "S1", "hsn__0": "54075240"})

    entry = read_registry(sku_registry_store(s))["S1"]
    assert entry["hsn"] == "54075240"
    assert entry["content_hash"] == "hash-1"        # nothing else disturbed
```

- [ ] **Step 6: Run them to verify they fail**

Run: `python -m pytest tests/web/test_attributes.py -q`
Expected: FAIL — no `hsn__0` field is rendered and no banner text appears.

- [ ] **Step 7: Wire the router**

In `src/web/routers/attributes.py`:

Extend the imports:

```python
from src.myntra.attribute_entry import (BRAND_COLOUR_HEADER, HSN_HEADER,
                                        AttributeValueError,
                                        SkuMismatchError, attribute_vocab,
                                        derive_brand_colour, user_filled_attributes,
                                        user_filled_freetext, validate_freetext,
                                        validate_hsn, validate_submitted,
                                        write_attributes)
from src.myntra.hsn_source import normalize as normalize_hsn
from src.myntra.sku_registry import update_hsn
from src.web.routers.pages import get_user, get_settings
from src.web.settings import sku_registry_store
```

Count HSN in the shared filled-count, so the screen and the per-panel save cannot
disagree:

```python
def _filled_count(values, columns, free_columns):
    """The one definition of "N filled". Shared by the screen and the per-panel
    save so a future column change cannot make the two disagree."""
    return sum(1 for c in list(columns) + list(free_columns) + [HSN_HEADER]
               if is_set(values.get(c)))
```

Add the gap counter:

```python
def _hsn_gap_count(xlsx, template):
    """Rows in the built sheet with no usable HSN. Blank and malformed count alike
    — Myntra rejects both, so the screen must not distinguish them."""
    return sum(1 for attrs in read_filled_rows(xlsx, template)
               if normalize_hsn(attrs.get(HSN_HEADER)) is None)
```

In `_panels`, add the two per-panel keys (inside the dict appended per row):

```python
            "hsn": attrs.get(HSN_HEADER),
            "hsn_missing": normalize_hsn(attrs.get(HSN_HEADER)) is None,
```

In `attributes_form`, pass the totals through:

```python
    return _templates().TemplateResponse(request, "attributes.html", {
        "user": user, "job_id": job.id, "columns": columns,
        "free_columns": free_columns,
        "vocab": attribute_vocab(template, columns),
        "panels": _panels(xlsx, csv_path, template, columns, free_columns),
        "hsn_gaps": _hsn_gap_count(xlsx, template),
        "total": len(columns) + len(free_columns) + 1})
```

Add the form parser beside `_submitted_free`:

```python
def _submitted_hsn(form):
    """Parse hsn__{ordinal} into {ordinal: raw_value}."""
    entries = {}
    for key, value in form.items():
        if key.startswith("hsn__"):
            entries[int(key.split("__")[1])] = str(value)
    return entries
```

Merge it in `_build_payload` — note the extra `hsn` argument:

```python
def _build_payload(entries, free, hsn, template, columns, free_columns):
    """Validate every entry and merge dropdown + derived + free-text + HSN values.
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
        # Only when the panel actually posted the field: a form that omits it
        # must leave the sheet's HSN alone rather than clearing it.
        if ordinal in hsn:
            values[HSN_HEADER] = validate_hsn(hsn[ordinal])
        payload.append({"ordinal": ordinal, "sku": e["sku"], "values": values})
    return payload
```

Rework `_save_entries` to scope, save, correct the registry, and recount:

```python
async def _save_entries(request, job_id, only=None):
    """Shared by both save routes. Returns (job, ordinals, payload, error, hsn_gaps).

    `only` narrows the write to that single ordinal. The per-panel save relies on
    it: scoping cannot be done in the browser (htmx `hx-include` only ever ADDS
    fields), so the server is what guarantees one click writes one row — and that
    an off-vocabulary value in some other panel cannot fail this one."""
    job, _job_dir, xlsx, _csv = job_files(job_id)
    template = read_template(TEMPLATE)
    columns, free_columns = user_filled_attributes(), user_filled_freetext()
    form = await request.form()
    entries = _submitted(form, columns)
    free = _submitted_free(form, free_columns)
    hsn = _submitted_hsn(form)
    if only is not None:
        entries = {o: e for o, e in entries.items() if o == only}
        free = {o: v for o, v in free.items() if o == only}
        hsn = {o: v for o, v in hsn.items() if o == only}
    ordinals = list(entries)
    try:
        payload = _build_payload(entries, free, hsn, template, columns, free_columns)
        with _WRITE_LOCK:
            write_attributes(xlsx, template, payload)
            # Only after a successful write: a rejected save must not move the
            # registry. The fix-flow rebuild pins HSN from here, so a correction
            # made on this screen has to reach it or a later rebuild undoes it.
            reg_store = sku_registry_store(get_settings(request))
            for e in payload:
                if HSN_HEADER in e["values"]:
                    update_hsn(reg_store, e["sku"], e["values"][HSN_HEADER])
    except (AttributeValueError, SkuMismatchError) as exc:
        return job, ordinals, [], str(exc), _hsn_gap_count(xlsx, template)
    return job, ordinals, payload, None, _hsn_gap_count(xlsx, template)
```

Update both save routes for the 5-tuple and pass `hsn_gaps` to the templates:

```python
@router.post("/generate/attributes/{job_id}", response_class=HTMLResponse)
async def attributes_save(request: Request, job_id: str):
    get_user(request)
    job, _ordinals, payload, error, hsn_gaps = await _save_entries(request, job_id)
    if error:
        return _templates().TemplateResponse(
            request, "_attr_saved.html", {"job_id": job.id, "error": error})
    return _templates().TemplateResponse(
        request, "_attr_saved.html",
        {"job_id": job.id, "saved": len(payload), "hsn_gaps": hsn_gaps})
```

and in `attributes_save_one`, change the unpack to
`job, ordinals, payload, error, hsn_gaps = await _save_entries(request, job_id, only=ordinal)`
and add `"hsn_gaps": hsn_gaps` to the success-branch context only. The two early
`nothing` returns and the error branch stay exactly as they are — they emit no
out-of-band fragment, so no count or banner is disturbed.

- [ ] **Step 8: Create the banner partial**

Create `src/web/templates/_hsn_gap.html`:

```html
{% if hsn_gaps %}
  <span class="flag">⚠ {{ hsn_gaps }} SKU{{ "s" if hsn_gaps != 1 else "" }}
    still need an HSN — Myntra rejects a blank one at upload.</span>
{% else %}
  <span class="ok">✅ Every SKU has an HSN.</span>
{% endif %}
```

- [ ] **Step 9: Add the field to the panel**

In `src/web/templates/_attr_panel.html`, inside `.attr-footer`, immediately after
the `{% endfor %}` that closes the free-column loop (line 48):

```html
    <label class="hint attr-free">HSN
      <input type="text" name="hsn__{{ p.ordinal }}" value="{{ p.hsn or '' }}"
             inputmode="numeric" autocomplete="off">
      {% if p.hsn_missing %}
      <span class="flag">⚠ needs an 8-digit HSN — Myntra rejects a blank one</span>
      {% else %}
      <span class="hint">8 digits — from Shopify’s custom.hsn_code, edit if needed</span>
      {% endif %}
    </label>
```

It sits inside `.attr-footer`, which is inside `.attr-panel`, so the existing
`hx-include="closest .attr-panel"` on both save buttons picks it up with no
change. It is outside `.attr-grid`, so it does not trigger the live preview —
correct, since HSN does not appear on the Myntra card.

- [ ] **Step 10: Add the banner to the screen**

In `src/web/templates/attributes.html`, inside the `{% else %}` branch of
`{% if not panels %}` (line 14), immediately above `<div id="attr-form">`, so an
empty batch does not get told every SKU has an HSN:

```html
  <p><span id="hsn-gap-banner">{% include "_hsn_gap.html" %}</span></p>
```

- [ ] **Step 11: Refresh it out of band on save**

In `src/web/templates/_attr_panel_saved.html`, add the out-of-band span to the
success branch only:

```html
{% if error %}
  <span class="flag">⚠ Not saved — {{ error }}</span>
{% else %}
  <span class="ok">✅ Saved · {{ filled }}/{{ total }} filled</span>
  <span class="hint" id="attr-count-{{ ordinal }}" hx-swap-oob="true">{{ filled }}/{{ total }} filled</span>
  <span id="hsn-gap-banner" hx-swap-oob="true">{% include "_hsn_gap.html" %}</span>
{% endif %}
```

In `src/web/templates/_attr_saved.html`, add the same out-of-band span to its
success branch, after the download link (line 10):

```html
    <a class="btn" href="/generate/download/{{ job_id }}">⬇ Download xlsx</a>
    <span id="hsn-gap-banner" hx-swap-oob="true">{% include "_hsn_gap.html" %}</span>
```

The error branch of both files is left alone: it emits no out-of-band fragment,
so a rejected save disturbs neither the panel count nor the banner.

- [ ] **Step 12: Run the attribute tests**

Run: `python -m pytest tests/web/test_attributes.py -q`
Expected: PASS.

- [ ] **Step 13: Update the older count assertions**

`test_screen_renders_a_panel_per_sku_with_twelve_selects` counts 12 dropdowns
via `r.text.count('name="attr__0__')` — that number is unchanged and it should
still pass.

The total did change, though, from 13 to 14. Find every existing assertion of
the old total and update it:

Run: `grep -n "13 filled\|/13\|total.*13" tests/web/test_attributes.py`

Change each hit to 14. Then:

Run: `python -m pytest tests/web/test_attributes.py tests/test_attribute_entry.py -q`
Expected: PASS.

- [ ] **Step 14: Run the full suite**

Run: `python -m pytest -q`
Expected: all pass.

- [ ] **Step 15: Commit — once, router and templates together**

```bash
git add src/myntra/attribute_entry.py src/web/routers/attributes.py \
        src/web/templates tests/test_attribute_entry.py tests/web/test_attributes.py
git commit -m "feat(attributes): per-SKU HSN field, validation and gap banner

HSN becomes a 14th field on each panel, pre-filled from the export and
validated as 8 digits. A banner counts the SKUs still missing one and
refreshes out of band on save. Saving also corrects the SKU registry, so
a later fix-flow rebuild does not restore the stale build-time code."
```

---

### Task 6: Documentation and the retained-not-wired note

**Files:**
- Modify: `src/myntra/hsn_kb.py:1-14` (header comment)
- Modify: `docs/APP-FEATURES-GUIDE.md`, `docs/ARCHITECTURE.md`
- Test: none (documentation)

**Interfaces:**
- Consumes: everything above.
- Produces: nothing code-facing.

- [ ] **Step 1: Mark the knowledge base as deliberately unwired**

Add at the very top of `src/myntra/hsn_kb.py`, above the imports:

```python
"""RETAINED BUT NOT WIRED IN. Kept deliberately — this is not dead code.

HSN now comes from the Shopify export's custom.hsn_code metafield and is
reviewed per SKU on the attribute screen; see
docs/superpowers/specs/2026-08-09-hsn-from-shopify-metafield-design.md.

Nothing on the request path imports this module. It is kept, with its tests
passing, as a working fallback if the metafield approach disappoints. Its
category|fabric signature is known to be too coarse to distinguish real codes
(dhonkhali and katthai are both "saree|cotton" but need 52084121 and 52083170),
which is why it was retired rather than merely bypassed."""
```

- [ ] **Step 2: Update the owner-facing guide**

In `docs/APP-FEATURES-GUIDE.md`, find the section describing the "One-time HSN
codes for this batch" screen and replace it with a description of the new
behaviour, in the same plain-English register the file already uses:

- HSN is read from Shopify automatically, from the product metafield
  `custom.hsn_code`.
- There is no longer an HSN question during Generate.
- On the Fill attributes screen each SKU has an HSN box, pre-filled from Shopify.
- A banner at the top counts how many SKUs still need one; Myntra rejects a blank
  HSN at upload.
- Filling one in the app fixes this batch only — update the metafield in Shopify
  too, or the next export will show the same gap.

- [ ] **Step 3: Update the architecture map**

In `docs/ARCHITECTURE.md`, update the HSN references so a new agent is not sent
to the knowledge base: name `src/myntra/hsn_source.py` as the HSN rule, the
`custom.hsn_code` metafield as the source, the attribute screen as where gaps are
filled, and `hsn_kb.py` as retained-but-unwired.

- [ ] **Step 4: Verify no stale references remain**

Run: `grep -rn "One-time HSN" docs/ src/`
Expected: no output outside `docs/journal/` and `docs/superpowers/` (historical
records, which stay as written).

- [ ] **Step 5: Run the full suite one last time**

Run: `python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/myntra/hsn_kb.py docs/APP-FEATURES-GUIDE.md docs/ARCHITECTURE.md
git commit -m "docs(hsn): HSN comes from Shopify; hsn_kb retained but unwired"
```

---

## Done when

- `python -m pytest -q` is green.
- `grep -rn "hsn_by_signature\|_hsn_review\|generate/hsn" src/ tests/` returns nothing.
- Uploading an export with the `HSN Code (product.metafields.custom.hsn_code)` column goes straight from the upload form to the build, with no HSN question.
- The Fill attributes screen shows each SKU's HSN pre-filled, and a banner counting the gaps that clears as they are filled.
- The old 61-column export in `input/products_export.csv` still builds, with every HSN blank and the banner reporting every SKU.

Per the project's standing workflow, stop here: committed locally on
`feat/hsn-from-shopify-metafield`, not merged and not pushed. The owner tests
locally and pushes.
