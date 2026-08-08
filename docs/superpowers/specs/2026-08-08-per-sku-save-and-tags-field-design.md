# Spec: Per-SKU Save button + editable `tags` field

**Project:** Myntra bulk-listing pipeline (Shopify CSV → Myntra template, sarees only)
**Type:** Incremental change to the existing in-app attribute-entry screen (additive; the bulk save path is unchanged)
**Design approved:** 2026-08-08 · **Spec written:** 2026-08-08
**Branch:** `feat/per-sku-save-and-tags`
**Builds on:** `docs/superpowers/specs/2026-07-26-in-app-attribute-entry-design.md`
(that spec's per-SKU accordion, photo, vocabulary dropdowns and live preview all stay exactly as built)

---

## 1. Problem

Two problems, reported by the owner from real use of the attribute screen with a 7-SKU batch.

**A — saving is all-or-nothing and invisible.** The screen renders every SKU as an accordion panel inside a
single `<form>`, with one "Save attributes" button at the very bottom
(`src/web/templates/attributes.html:18`). That button does in fact save partial work — it posts every
panel, blanks included, and the saved values are read back and pre-selected when the screen is reopened
(`src/web/routers/attributes.py:58`). But with seven panels expanded the button is far below the fold, and
nothing on screen says that leaving a panel half-filled is safe. The owner's mental model is therefore
"if I walk away I have to redo everything", and the UI gives him no reason to think otherwise.

**B — `tags` is filled but not visible or editable.** The Myntra template has a `tags` column, and the
pipeline already fills it from the Shopify export's `Tags` field (`config/myntra/column_map.yaml:6` →
`src/core/shopify_reader.py:47`). The owner cannot see or change that value anywhere in the app, so the
tags Myntra receives are whatever Shopify happened to hold.

## 2. Goals / Non-goals

**Goals**

1. A **Save button inside every SKU panel** that saves just that SKU, with inline confirmation, without
   collapsing the panel or scrolling away.
2. An **editable `tags` field** in each panel, pre-filled from the sheet, accepting any text the owner types.
3. Saving one panel must not touch any other panel's row.

**Non-goals**

- ❌ No change to the existing bulk "Save attributes" button. It stays and keeps its current behaviour.
- ❌ No autosave. Saving stays an explicit click.
- ❌ **No durability across container restarts.** Explicitly out of scope — see §8.
- ❌ No change to the twelve dropdown attributes, the vocabulary rule, the preview, or the Excel path.
- ❌ No change to brand, HSN, pricing, images, styleGroupId, or the generate pipeline.

## 3. Feature A — per-SKU Save

### 3.1 Placement

A footer strip inside the expanded panel, below the dropdown grid, with its status message inline beside
the button:

```
┌─ 164SDE226RPPG  Rajasi Panna Green…  5/13 filled ──────┐
│  ┌───────┐  Prominent Colour [Green ▾]   ┌───────────┐ │
│  │ photo │  Saree Fabric     [Cotton ▾]  │  preview  │ │
│  └───────┘  Border           [— ▾]       └───────────┘ │
│                                                        │
│  [ Save this SKU ]   ✅ Saved · 5/13 filled            │
└────────────────────────────────────────────────────────┘
```

### 3.2 Mechanism

The button is `type="button"` carrying htmx attributes:

| Attribute | Value | Why |
|---|---|---|
| `type` | `button` | **Critical.** A default `<button>` inside the existing form submits *all* panels. |
| `hx-post` | `/generate/attributes/{job_id}/one` | Dedicated route rendering a compact partial. |
| `hx-include` | `closest .attr-panel` | Posts only this panel's `sku__{n}` and `attr__{n}__{i}` fields. |
| `hx-target` | `#attr-save-{ordinal}` | Per-panel status span. |
| `hx-swap` | `innerHTML` | |
| `hx-disabled-elt` | `this` | Prevents a double-click racing itself during the ~seconds-long write. |

This is the same scoping pattern the live preview already uses successfully at
`src/web/templates/_attr_panel.html:17-21`, so it is proven in this codebase.

### 3.3 Routes

`write_attributes` already writes only the ordinals it is handed
(`src/myntra/attribute_entry.py:76`), and `_submitted()` already parses per-ordinal form keys, so the save
logic needs no behavioural change. Restructure rather than duplicate:

- Extract the body of the existing `attributes_save` into `_save_entries(request, job_id) -> (job, saved, error)`.
- `POST /generate/attributes/{job_id}` (existing) — calls the helper, renders `_attr_saved.html`. Unchanged
  from the outside.
- `POST /generate/attributes/{job_id}/one` (new) — calls the same helper, renders the new compact
  `_attr_panel_saved.html`.

Two thin routes over one shared helper, rather than a mode flag on the existing route: the two responses
differ only in template, and a separate route is directly testable.

`_attr_saved.html` is **not** reused for the panel response — it renders a full-width panel with a heading
and a download button, which would be wrong stamped into every row.

### 3.4 Write lock

Each save is a read-modify-write of the entire workbook: `openpyxl.load_workbook` → mutate → `wb.save` →
`shared_to_inline`. Two panels saved close together would interleave, and the second write would silently
drop the first one's values — a data-loss bug with no error message.

A module-level `threading.Lock` in `attributes.py`, held across the whole `write_attributes` call,
serialises saves. Correctness matters more than the throughput lost here; a single owner clicking Save will
never notice the queueing.

### 3.5 Stale count

The `N/13 filled` label in the panel summary is rendered server-side and would still show the old number
after a save, reading as "the save didn't work". The save response carries an `hx-swap-oob` span that
replaces `#attr-count-{ordinal}` with the recomputed count.

## 4. Feature B — editable `tags`

### 4.1 What the column is

Verified against `templates/myntra/Myntra-Sku-Template-2026-07-24.xlsx`: exactly one matching header,
`tags`, with **no data validation — free text**. It is one of the template's 80 headers and is currently
populated by the pipeline from Shopify.

### 4.2 Why it cannot be a dropdown attribute

Every one of the twelve existing attributes is validated by exact membership in the template's own
vocabulary; `validate_submitted` raises `AttributeValueError` for anything else
(`src/myntra/attribute_entry.py:43-57`). That rule is deliberate and must not be loosened globally.

`tags` is therefore declared in a **separate** config list, `user_filled_freetext`, in
`config/myntra/rules.yaml`, alongside the existing `user_filled_attributes`. Free-text columns travel a
parallel path that skips the vocabulary check by construction, so no dropdown column can ever accidentally
become unvalidated.

### 4.3 Behaviour

- Rendered as a single-line text input in the panel, below the dropdown grid.
- **Pre-filled** with the value currently in the sheet. An untouched field therefore round-trips unchanged
  and cannot silently wipe the Shopify tags.
- Accepts **any** text the owner types. No vocabulary check, no format check.
- Whitespace is stripped. An empty box writes a blank cell, consistent with how a cleared dropdown behaves.
- Posted as `free__{ordinal}__{index}`, keeping it unambiguously distinct from `attr__` keys during parsing.
- Counted in the panel's filled tally, which becomes `N/13`.

### 4.4 Validation surface

`validate_submitted` is left untouched. A sibling `validate_freetext(values, columns)` handles the
free-text set: it verifies the column is a known free-text column and normalises blank to `None`. An
unknown column still raises, so a tampered form cannot write to an arbitrary template cell.

## 5. Data flow

```
panel Save click
  └─ htmx POST /generate/attributes/{job}/one   (only this panel's fields)
       └─ _save_entries()
            ├─ _submitted()        attr__ → dropdown values
            ├─ _submitted_free()   free__ → tags
            ├─ validate_submitted()   exact-vocab check   (dropdowns)
            ├─ validate_freetext()    known-column check  (tags)
            ├─ derive_brand_colour()  unchanged
            └─ [lock] write_attributes() → wb.save → shared_to_inline
       └─ _attr_panel_saved.html  +  oob count span
```

## 6. Error handling

| Case | Behaviour |
|---|---|
| Off-vocabulary dropdown value | Inline `⚠` in that panel only; **nothing written** (`write_attributes` verifies all rows before writing any). |
| SKU mismatch (sheet changed underneath) | Existing `SkuMismatchError` message, shown inline in the panel. |
| Job expired / workbook gone | Existing 404 `session expired, please re-upload`. |
| Unknown free-text column | `AttributeValueError`, inline; nothing written. |

## 7. Testing

Added to `tests/web/test_attributes.py` unless noted:

1. Saving one panel writes that row **and leaves the other rows untouched**.
2. The compact partial comes back containing the refreshed filled count.
3. An off-vocabulary value in a single-panel save reports inline and writes nothing.
4. The bulk route still saves every panel exactly as before (regression).
5. `tags` round-trips: pre-filled from the sheet, edited, saved, re-read.
6. An untouched `tags` field leaves the Shopify-derived value unchanged.
7. Clearing `tags` blanks the cell.
8. Free-text values bypass the vocabulary check, while dropdown columns still enforce it
   (`tests/test_attribute_entry.py`).
9. Dropdowns survive a per-SKU save — the existing `_validation_count` invariant, re-asserted for the new
   path (`tests/test_attribute_entry.py`).

## 8. Known limitation — accepted, not solved

The owner's original framing was "if I have to go and come back I do not have to redo everything". This
spec makes saving incremental and obvious, which covers closing the tab and returning **while the app is
running**.

It does **not** survive a container restart. The job record is an in-memory dict (`src/web/jobs.py:72`) and
the built workbook lives at `/app/src/web/runtime/` inside a container started with `docker run --rm` and
no volume mount (`aws/ec2/userdata.sh:31`). A deploy or a box restart destroys both, and the screen then
returns `session expired, please re-upload`.

Making batches durable — persisting the workbook and job metadata to S3, plus an "unfinished batches" list
on the dashboard — was scoped out by the owner on 2026-08-08 in favour of shipping the button first. It
remains the natural follow-up and is recorded here so the limitation is not rediscovered as a bug.

## 9. Files touched

| File | Change |
|---|---|
| `config/myntra/rules.yaml` | New `user_filled_freetext: [tags]` list |
| `src/myntra/attribute_entry.py` | `user_filled_freetext()`, `validate_freetext()`; `write_attributes` unchanged |
| `src/web/routers/attributes.py` | Extract `_save_entries()`; new `/one` route; `_submitted_free()`; write lock; free-text + count in `_panels` |
| `src/web/templates/_attr_panel.html` | Save button, status span, `tags` input, id'd count span |
| `src/web/templates/_attr_panel_saved.html` | **New** compact save-result partial with oob count |
| `src/web/static/app.css` | Footer strip + free-text input styling |
| `tests/web/test_attributes.py` | Tests 1–7 |
| `tests/test_attribute_entry.py` | Tests 8–9 |
| `docs/APP-FEATURES-GUIDE.md` | Document the per-SKU save and the tags field |
