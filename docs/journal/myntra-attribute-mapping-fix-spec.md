# Spec: Myntra Attribute Mapping Audit & Fix — Listing Pipeline

**Project:** Myntra bulk-listing pipeline (Shopify CSV → Myntra template, sarees only)
**Type:** Correction / enhancement to Phase 1 deterministic transform
**Priority:** P0 — attributes directly control the customer-facing product title on Myntra

---

## 1. Problem Statement

Myntra does **not** display the product name we submit. It auto-generates the visible product title from attribute columns, approximately:

```
Brand + Ornamentation + Fabric + Type + Article Type
```

**Observed example:**

| Source | Title |
|---|---|
| Shopify (ours) | Banarasi Soft Tissue Salmon Pink Silk Saree |
| Myntra (generated) | Ijor Ethnic Partners — Zari Tissue Banarasi Saree |

The Myntra title is assembled from: `Zari` (Ornamentation) + `Tissue` (Saree Fabric) + `Banarasi` (Type) + `Saree` (Article Type). **Colour ("Salmon Pink") is missing entirely** because the colour attribute is not being populated (or not populated correctly) by the pipeline.

**Consequence:** Our pipeline currently treats attribute columns as secondary metadata. They are actually the primary lever for the displayed title, search indexing, and filter/facet discovery on Myntra. The fix is an attribute-mapping audit, not a title-field fix.

Brand name ("Ijor Ethnic Partners") is correct and registered — do not touch brand handling.

---

## 2. Scope

**In scope:**
- Audit of every attribute column currently written by the transform pipeline
- New/corrected extraction logic for: Colour, Colour Family, Saree Fabric, Type, Ornamentation, Border, Blouse Fabric
- Deterministic keyword-extraction rules from Shopify Title, Tags, and Body (HTML) fields
- Normalization dictionaries (Shopify vocabulary → Myntra accepted values)
- Validation step that flags rows with missing/unmapped attributes instead of silently defaulting

**Out of scope:**
- Phase 2 (GPT-4o-mini enrichment, SQLite dedup ledger, Streamlit review screen)
- Any LLM calls — Phase 1 stays fully deterministic
- Brand name, pricing, image handling (WebP→JPG logic unchanged)

---

## 3. Task 0 — Inventory (do this first)

Before writing any mapping code:

1. Open the actual Myntra saree template (`.xlsx`) in the repo and extract the **exact column headers** and, where present, the **dropdown/accepted value lists** for each attribute column (Myntra templates often embed valid values as data validation lists or a hidden sheet — check both).
2. Produce a table in `docs/myntra-attribute-inventory.md`:
   - Column header (exact string)
   - Required / optional (per template)
   - Accepted values (if constrained)
   - Current pipeline behaviour (mapped from where / hardcoded / left blank)
3. Do not guess accepted values. If a column is free-text, note it. If constrained, the normalization dictionary in Task 2 must only emit values from that list.
4. **Deliverable — vocabulary workbook:** In addition to the markdown inventory, generate a downloadable Excel file `outputs/myntra-attribute-vocabulary.xlsx` containing the complete dropdown vocabulary extracted from the Myntra bulk-listing template:
   - One sheet named `Summary`: every attribute column header, whether it is constrained (dropdown) or free-text, required/optional, and the count of accepted values.
   - One sheet per constrained attribute (sheet name = attribute name, truncated to Excel's 31-char limit), listing every accepted value exactly as it appears in the template — one value per row, no reformatting, no de-duplication beyond exact duplicates.
   - Extraction must cover BOTH data-validation dropdown lists on the entry sheet AND any hidden/reference sheets the template uses to store value lists (Myntra templates commonly keep master value lists on hidden sheets — use openpyxl and check `workbook.sheetnames` including hidden ones, plus each cell's data-validation formulae).
   - This workbook is the single source of truth for the normalization dictionaries in Task 2: every value emitted by the pipeline must exist in the corresponding sheet of this file.

---

## 4. Task 1 — Colour extraction (highest impact)

Colour is missing from generated titles and from filter facets. Fix:

**Extraction order (first hit wins):**
1. Shopify `Option1 Value` / variant option if the option name is Colour/Color
2. Shopify Tags (look for known colour tokens)
3. Shopify Title (scan for known colour tokens, longest match first — "Salmon Pink" must match before "Pink")

**Colour dictionary requirements:**
- Multi-word colours matched before single-word ("Salmon Pink", "Dusty Rose", "Off White", "Bottle Green", "Mustard Yellow", etc.)
- Map each extracted colour to BOTH:
  - `Colour` (specific, e.g., "Salmon Pink" — or nearest accepted value from template list)
  - `Colour Family` / `Base Colour` (e.g., "Pink") if the template has such a column
- Build the initial dictionary from the actual colours present in the current Shopify export (grep all titles/tags), then extend with common saree colours. Keep it in a separate data file (e.g., `mappings/colours.json`) so it's editable without code changes.

**Failure mode:** if no colour is found, do NOT default to a value. Flag the row (see Task 3).

---

## 5. Task 2 — Fabric / Type / Ornamentation / Border normalization

These four columns compose the title alongside colour. Rules:

**Saree Fabric**
- Extract fabric tokens from Shopify title/tags: Tissue, Silk, Cotton, Organza, Georgette, Chiffon, Linen, "Soft Tissue Silk", etc.
- Decide and document a canonical mapping when Shopify uses compound descriptors. Example decision needed: "Soft Tissue Silk" → `Tissue` or `Silk Blend`? Whatever is chosen lands verbatim in the customer-facing title, so prefer the term that (a) is an accepted template value and (b) reads best in a title. Record each decision in the mapping file with a comment.

**Type**
- Banarasi, Kanjeevaram, Chanderi, Paithani, etc. Extract from title/tags. Current behaviour appears correct for Banarasi — verify it isn't hardcoded.

**Ornamentation**
- Zari, Embroidered, Sequinned, Printed, Woven Design, etc. Currently "Zari" — verify source. If hardcoded, replace with extraction + dictionary.

**Border**
- Zari Border, Lace Border, Temple Border, No Border, etc. Note the live listing shows a duplication bug: "Embroidered saree with Zari Border border" — the word "border" is being appended to a value that already contains it. Fix the string composition so the suffix is added only when absent.

**Blouse Fabric**
- Usually same as saree fabric for our catalog; extract independently if Shopify data distinguishes it, else copy saree fabric. Document the rule.

All dictionaries live in versioned data files (`mappings/*.json`), not inline in code.

---

## 6. Task 3 — Validation & flagging

Add a validation pass after transform, before writing the output Excel:

- Any row missing a required attribute, or with an extracted value not in the template's accepted list → written to a separate `flagged_rows.csv` with columns: SKU/Handle, missing/invalid field, raw source strings (title, tags) for quick manual resolution.
- Console summary at end of run: `N rows OK, M rows flagged (see flagged_rows.csv)`.
- Flagged rows are EXCLUDED from the upload sheet — never upload a row with silent defaults, because the wrong attribute becomes the wrong public title.

---

## 7. Acceptance Criteria

1. Running the pipeline on the current Shopify export produces an upload sheet where, for the salmon pink Banarasi saree (reference product), the attribute columns yield a Myntra-generated title equivalent to: **"Ijor Ethnic Partners Salmon Pink Zari Tissue Banarasi Saree"** (exact word order is Myntra's; our obligation is that Colour, Ornamentation, Fabric, and Type columns are all correctly populated).
2. No attribute column in the output is hardcoded except Article Type = "Saree" (and any genuinely constant fields — list them in the inventory doc).
3. "Border border" duplication bug is gone.
4. Rows with unextractable colours/fabrics land in `flagged_rows.csv`, not the upload sheet.
5. Mapping dictionaries are external JSON files with at least the full vocabulary found in the current catalog.
6. Existing tests still pass; add tests for: multi-word colour matching precedence, unknown-colour flagging, border suffix de-duplication, compound fabric mapping.
7. `outputs/myntra-attribute-vocabulary.xlsx` exists, opens cleanly, and contains every constrained attribute from the template with its full accepted-value list (including values sourced from hidden reference sheets). Spot-check: the sheets for Colour, Saree Fabric, Type, Ornamentation, and Border are non-empty.

---

## 8. Reference Data

- Shopify title example: `Banarasi Soft Tissue Salmon Pink Silk Saree`
- Myntra generated title (current, wrong — no colour): `Ijor Ethnic Partners Zari Tissue Banarasi Saree`
- Myntra product code for reference listing: `43210624`
- Attributes visible on live listing: Type=Banarasi, Ornamentation=Zari, Border=Zari, Blouse Fabric=Tissue, Saree Fabric=Tissue, Wash Care=Dry Clean, Net Quantity=1
