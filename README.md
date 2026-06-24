# Myntra Bulk-Listing Automation — Phase 1

Turn a **Shopify product CSV export** + the **Myntra DIY saree template** into a
ready-to-upload Myntra sheet with Myntra-compliant JPG images — in one command,
deterministically.

Built for **Ijor** (ethnic wear). Phase 1 scope: **sarees only**.

> **Guiding principle:** the pipeline guesses nothing. All column mapping, pricing,
> and validation is plain code. Any value that does not match Myntra's allowed
> dropdown list is **flagged in a report, never silently written**. (LLM-based
> attribute enrichment is Phase 2.)

---

## Quick start

1. Put the two input files in `input/` (or leave them in the project root):
   - `products_export.csv` — Shopify product export
   - `Myntra-Sku-Template-2026-06-16.xlsx` — Myntra DIY saree template (with dropdowns)
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Run:
   ```
   python run.py
   ```

### Output (`output/`)
| File | Contents |
|---|---|
| `myntra_filled.xlsx` | The Myntra **Sarees** sheet filled with your products, **dropdowns preserved** |
| `images/SKU_n.jpg` | Each product's gallery converted to Myntra-compliant JPGs |
| `report.txt` | Per-SKU log: fields filled, blanks left for manual fill, vocab flags, image pass/fail |

> ⚠️ Close `output/myntra_filled.xlsx` in Excel before re-running, or the script
> cannot overwrite it.

---

## Major functions

### 1. Read the Myntra template + extract dropdown vocab — `src/template_reader.py`
- Detects the Sarees header row (`styleId` marker, row 3) and the first data row (row 4).
- The template's dropdowns are **37 Excel "x14" extension data-validations** that
  openpyxl cannot see. They are parsed straight from the raw sheet XML and resolved
  to their allowed-value lists in the `masterdata` sheet, producing an exact
  `{column → allowed values}` map. These lists are the controlled vocabularies that
  every written value is checked against.

### 2. Read + group the Shopify export — `src/shopify_reader.py`
- Loads the CSV and groups variant/image rows under each parent product (`Handle`).
- Forward-fills product-level fields (populated only on each product's first row).
- Collects the image gallery per product, ordered by `Image Position`.

### 3. Map columns + validate + apply business rules — `src/mapper.py`
- **Direct field mapping** (`config/column_map.yaml`): title, SKU, tags, description, fabric.
- **Deterministic pricing:** `MRP = Compare-At-Price (else Price)`, `ISP = Price`.
- **Constants on every row** (`config/constants.yaml`): brand / manufacturer /
  packer, size fields, AgeGroup, FashionType, Year, Season, etc.
- **Per-row rules** (`config/rules.yaml`):
  - HSN code applied when the product name contains a keyword (e.g. `cotton → 52081120`).
  - Prominent Colour derived by scanning the product name/description against the
    colour dropdown (longest/earliest match wins; small synonym map, e.g. golden→Gold).
- **Vocab validation:** every value targeting a dropdown column is matched
  (case-insensitive) to its allowed list and rewritten in Myntra's exact spelling.
  No match → blank cell + a flag in the report.

### 4. Convert images — `src/images.py`
- Downloads each Shopify image (WebP/PNG/JPG) and outputs **JPG only** (Myntra requirement).
- **Flattens transparency onto white** before converting (`alpha_composite`), so
  transparent areas don't turn black.
- Validates minimum dimensions and file size; JPEG quality 90; names `SKU_1.jpg`, `SKU_2.jpg`, …
- Failing images are flagged, not slotted into the sheet.

### 5. Fill the sheet + preserve dropdowns — `src/fill.py`
- Writes mapped values and image references into the Sarees sheet from row 4
  (first 7 images → the `Front/Side/Back/Detail/Look Shot/Additional 1–2` columns).
- After saving, **re-injects the 37 dropdown validations** that openpyxl drops, so
  the uploaded file keeps Myntra's dropdowns intact.

### 6. Report — `src/report.py`
- Emits `report.txt`: per-SKU filled-field count, blanks left for a manual pass,
  vocab flags (with the offending value), and image pass/fail — so there are **no
  silent gaps**.

`run.py` wires these together and is the single entry point.

---

## Configuration (no code changes needed)

| File | Purpose |
|---|---|
| `config/column_map.yaml` | Shopify field → Myntra Sarees column (direct copies) |
| `config/constants.yaml` | Fixed values written to every row (forced; canonicalized to dropdown vocab where possible) |
| `config/rules.yaml` | Per-row rules: HSN-by-name-keyword, Prominent-Colour-from-name, colour synonyms |
| `config/image_specs.yaml` | Min dimensions, max file size, JPEG quality, max images per product |

If Myntra changes a column or vocabulary, it's a one-line config edit.

---

## What Phase 1 deliberately does NOT do
- **No attribute invention.** Fields Shopify doesn't carry (Saree Fabric, Occasion,
  Pattern, Blouse, Wash Care, measurements, …) are left blank and listed in the
  report for a manual pass. (Phase 2 fills these via LLM.)
- **No persistent dedup / ledger.** Only an in-run check. (Phase 2 adds the SQLite
  ledger + create/update/skip routing that fixes Myntra's duplicate-listing error.)
- **No Myntra verification.** Uploads are confirmed manually. (Phase 2 reconciles
  against a Myntra catalog export.)

---

## Tech stack
Python 3.12 · pandas · openpyxl · Pillow · PyYAML · requests · pytest.

## Tests
```
python -m pytest -v
```
21 tests cover x14 vocab parsing, variant grouping, vocab validation, pricing,
HSN/colour rules, transparency flatten, dropdown preservation, and an end-to-end run.

---

## Project docs
- Design spec: `docs/superpowers/specs/2026-06-24-myntra-phase1-deterministic-fill-design.md`
- Implementation plan: `docs/superpowers/plans/2026-06-24-myntra-phase1-deterministic-fill.md`
