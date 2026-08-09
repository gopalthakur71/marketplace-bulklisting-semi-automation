# HSN from the Shopify metafield, reviewed on the attribute screen

**Date:** 2026-08-09
**Branch:** `feat/hsn-from-shopify-metafield`
**Supersedes:** Part 1 of
[2026-08-03-hsn-from-shopify-and-stop-clear-design.md](2026-08-03-hsn-from-shopify-and-stop-clear-design.md),
which was deferred on 2026-08-03 and is now revived with two changes: the header
string is confirmed, and the hard block is replaced by review on the attribute
screen.

## The problem

The app does not read the HSN column out of the Shopify export. It never could —
until now the export had no such column, so the app asked for HSN on screen
instead and remembered the answers in a knowledge base.

A `custom.hsn_code` product metafield has since been created and filled, and it
now appears in the export. Three places still have to change before the app can
see it:

| Where | What is missing |
|---|---|
| [`src/core/shopify_reader.py:5-7`](../../../src/core/shopify_reader.py) | Knows exactly three metafield columns — Color, Fabric, Size. No HSN constant, so the column is read into the DataFrame and dropped. |
| [`src/core/models.py:5-18`](../../../src/core/models.py) | `Product` has no `hsn` field to hold it. |
| [`src/myntra/mapper.py:157-172`](../../../src/myntra/mapper.py) | HSN comes only from `hsn_by_signature` (the on-screen ask) or `hsn_override` (registry rebuild). Nothing reads it from the product. |

**The header string is confirmed** against the owner's live export, and it is not
what the deferred spec guessed:

```
HSN Code (product.metafields.custom.hsn_code)
```

Not `HSN (product.metafields.custom.hsn)`. Shopify derives the header from the
metafield's name and its namespace/key, and both differ from the guess.

Note that `input/products_export.csv` in the repo is still the older 61-column
export with no HSN column at all. It must keep reading cleanly.

## What the real data says

Two findings from the owner's export (18 products) drove the design.

**Half the catalogue has no HSN yet.** Nine products carry a valid code
(`54075240`, `52084121`, `52083170`, `52085990`); nine are blank — onion pink,
salmon pink, nisha swarna, crimson red mashru, black kora, semi katan blue,
georgette red, georgette black, nilambari. A hard block, as the deferred spec
designed, would reject the whole batch today.

**Two products carry a trailing space** (`"52085990 "` on lavender and
turquoise), so stripping before validating is not optional.

**The `category|fabric` signature cannot distinguish HSN codes.** Dhonkhali has
fabric `cotton` and HSN `52084121`; katthai has fabric `cotton` and HSN
`52083170`. Same signature, two different codes. The per-product metafield gets
both right; any signature-grouped question has to pick one and stamp it on both.
This is the evidence that retires the knowledge base rather than merely
sidelining it.

## Decisions

| Decision | Rationale |
|---|---|
| The export is the source; gaps are filled by hand | Matches where the data actually lives now. |
| Review happens on the **attribute screen**, not a new one | The owner already inspects every SKU there. A second screen is redundant. |
| **No HSN screen during the build at all** | With review moved downstream there is nothing left to ask before generating. |
| A gap does **not** block the build | Nine of eighteen products would block today. The sheet is generated; the gap is made loud on the panel. |
| HSN gets its own 8-digit validator, not the free-text one | A wrong tax code is a different class of mistake from a wrong tag. |
| The knowledge base is retired, not deleted | Its signature is demonstrably too coarse (see above). Kept on disk as a working fallback. |

## Design

### 1. Reading

`shopify_reader.py` gains a fourth metafield constant beside the existing three:

```python
HSN_COL = "HSN Code (product.metafields.custom.hsn_code)"
```

`models.Product` gains `hsn: str | None`, populated via the existing `fv()`
helper. `fv()` already returns `None` for an absent column, so the old export
reads cleanly with every product at `hsn=None` — no crash, no special case.

### 2. Normalising

New module `src/myntra/hsn_source.py`, deliberately small and pure — no knowledge
of web requests, jobs, or Shopify:

```python
def normalize(raw) -> str | None
```

Strips surrounding whitespace, then returns the value only if it matches `\d{8}`
exactly. Everything else — blank, `None`, 4-digit `5407`, `6211.42.90` with
punctuation, stray text — returns `None` and is treated exactly as missing. This
is the same 8-digit standard the removed on-screen form enforced; only the source
of the number changes.

Returning `None` rather than raising is deliberate: a malformed value is a gap to
be filled on the panel, not a crash during the build.

### 3. Mapping

`map_product` swaps its `hsn_by_signature` parameter for a plain `hsn`:

```python
def map_product(product, template, column_map, constants, rules=None,
                hsn=None, hsn_override=None):
```

Precedence:

1. `hsn_override` — a per-SKU code pinned from the SKU registry, used by the fix
   flow's deterministic rebuild. Still wins over everything.
2. `hsn` — the normalised value from the export, passed in by the pipeline.
3. Neither — the HSN cell is left blank and **no flag is raised**. The attribute
   panel is what surfaces the gap now, so a build-time flag would only be noise.

The `from src.myntra.hsn_kb import signature` import is dropped from `mapper.py`,
along with the "no HSN learned for signature" flag.

`pipeline.main` drops its `hsn_by_signature` parameter, reads `normalize(p.hsn)`
per product, and **keeps `hsn_by_sku`**, which the fix-flow rebuild depends on.

### 4. Reviewing, on the attribute screen

HSN becomes a **14th field** on each per-SKU panel (today: 12 dropdowns +
`tags` = 13).

It arrives there almost for free.
[`read_filled_rows`](../../../src/myntra/preview.py) already reads *every*
template header out of the built workbook, so the panel only has to render the
`HSN` cell it is already given. Saving goes through the existing
[`write_attributes`](../../../src/myntra/attribute_entry.py), which writes by
header name — `HSN` is an ordinary template column, so no new writing machinery
is needed.

What is new:

- **Its own validator.** `hsn_source.normalize()` is reused, so the screen and
  the reader enforce one rule from one place. A non-empty value that does not
  normalise is rejected with a clear message; blank clears the cell, matching how
  every other attribute behaves.
- **Gaps are loud.** A count at the top of the screen — "N SKUs still need an
  HSN" — plus a marker on each offending panel. Myntra rejects a blank HSN at
  upload, so this must be impossible to miss while going through the panels. On
  the current export it reads 9.
- **It is not added to `user_filled_freetext`.** That list is the free-text
  escape hatch where any text is accepted; HSN is validated, so it is handled
  explicitly. Keeping it out preserves the invariant that everything in the
  free-text list is genuinely unvalidated.

### 4a. The wiring, concretely

Because HSN is in neither `columns` nor `free_columns`, it needs its own thread
through the existing machinery. It is a third, single-item category rather than a
new list, so nothing has to grow a general "third kind of attribute" concept:

- `attribute_entry.HSN_HEADER = "HSN"` — one constant, beside
  `BRAND_COLOUR_HEADER`, which is the existing precedent for a column handled
  outside the two lists.
- **Form field:** `hsn__{ordinal}`, parsed by a new `_submitted_hsn(form)`
  mirroring `_submitted_free`. Scoped by `only` in `_save_entries` exactly as the
  other two are, so a per-panel save still writes one row.
- **Validation:** `attribute_entry.validate_hsn(raw)` wraps
  `hsn_source.normalize()` and raises `AttributeValueError` on a non-empty value
  that does not normalise, so `_build_payload` keeps its existing
  "validate everything before writing anything" guarantee and the existing error
  panel renders the message unchanged. Blank → `None`, which clears the cell,
  matching every other attribute.
- **Count:** `_filled_count` counts `columns + free_columns + [HSN_HEADER]`, and
  the screen's `total` becomes `len(columns) + len(free_columns) + 1` = 14. It
  stays the single shared definition used by both the screen and the per-panel
  save, so the two cannot disagree.
- **The "N SKUs still need an HSN" banner** is computed server-side from the
  panel values at render, and re-rendered out-of-band on every save using the
  same `hx-swap-oob` pattern the per-panel filled count already uses — so
  correcting the last gap clears the banner without a reload.

### 5. Keeping the registry in step

[`pipeline.py:94`](../../../src/myntra/pipeline.py) records HSN into the SKU
registry at **build** time. An HSN corrected afterwards on the attribute panel
would leave the registry holding the stale build-time value — and the fix flow's
rebuild pins HSN *from the registry*, so a later rebuild would quietly undo the
correction.

`sku_registry` gains a narrow updater:

```python
def update_hsn(store, sku, hsn, key=REGISTRY_KEY)
```

It touches only the `hsn` field of an existing entry and is a no-op for an
unknown SKU. It deliberately does not create entries: only a completed build
earns a registry row.

It is called from `_save_entries` after `write_attributes` returns — never
before, so a rejected save cannot move the registry — and inside the existing
`_WRITE_LOCK`, for each saved entry whose payload carries an `HSN` value. The
attributes router therefore needs the settings object it does not currently
take: it gains `get_settings(request)` and `sku_registry_store(settings)`,
matching how the generate router already reaches the registry.

A save whose HSN is unchanged still calls `update_hsn`; the write is idempotent
and a no-op comparison would cost more than it saves.

### 6. What is removed

| Thing | Where |
|---|---|
| `_hsn_prescan_or_build` | `src/web/routers/generate.py` |
| `POST /generate/hsn/{job_id}` (`hsn_submit`) | `src/web/routers/generate.py` |
| `_hsn_review.html` | `src/web/templates/` |
| per-job `hsn.json` write/read | `src/web/routers/generate.py` |
| `hsn_by_signature` parameter | `mapper.map_product`, `pipeline.main` |
| `signature` import and the unresolved-HSN flag | `src/myntra/mapper.py` |
| tests exercising the removed route and the signature-driven mapper path | `tests/test_mapper.py`, `tests/test_pipeline_override.py`, `tests/web/test_generate.py` |

All three former callers of `_hsn_prescan_or_build` — the initial `POST
/generate`, `POST /generate/new-only/{job_id}`, and `POST
/generate/continue/{job_id}` — call `_start_build` directly.

### 7. What is kept

**Kept, unused, by explicit decision.** `src/myntra/hsn_kb.py`,
`settings.hsn_store` and `HSN_LOCAL_PATH` stay on disk with their tests
(`tests/test_hsn_kb.py`, `tests/test_signature.py`) still passing, so the
knowledge base remains a working fallback if the metafield approach disappoints.
Nothing imports them from the request path. A comment at the top of `hsn_kb.py`
records that it is retained deliberately and is not wired in, so a future reader
treats it as neither dead code to delete nor live code to maintain.

KB suggestions are **not** offered on the panel. The dhonkhali/katthai pair shows
the signature cannot distinguish codes that differ, so a suggestion there would
be a confident wrong answer.

**Kept and load-bearing.** The SKU registry's per-SKU `hsn` field, and
`hsn_by_sku` through `pipeline.main`. The fix flow's rebuild pins the original
HSN of already-listed SKUs from the registry; that must not regress.

### 8. Explicitly not a concern: the duplicate-guard hash

An earlier reading of this design worried that letting HSN flow into the mapped
cells would change the content hash behind the duplicate-generation guard, making
every SKU already in the registry read as "edited" on the next upload.

**It cannot.** [`sku_registry.content_hash`](../../../src/myntra/sku_registry.py)
excludes HSN from the fingerprint outright:

```python
_EXCLUDE = ("styleGroupId", "HSN")
```

HSN therefore never enters the hash regardless of what reaches the cells. The
`hsn_by_signature=None` in `scan_content_hashes` and its "with HSN unset"
docstring are belt-and-braces on top of that exclusion, not the protection
itself, and `tests/test_sku_registry.py:18-19` already pins the behaviour. No new
guard is needed. `scan_content_hashes` still passes no HSN, and its docstring is
updated to name the real reason.

## A consequence worth stating

Filling a gap on the attribute panel writes into the built workbook and the SKU
registry — **not into Shopify**. If the metafield is not also updated there, a
*fresh* generate from a later export shows that SKU blank again. The intended
workflow is to do both: fill it in the app to get the batch out, mirror it into
Shopify so the next export carries it. The "N SKUs still need an HSN" count is
what catches a slip, so the cost is a retype rather than a rejected upload.

## Testing

| Area | Test |
|---|---|
| Reader | Column present → `Product.hsn` populated; column absent (the old 61-column export) → `hsn is None`; trailing space survives to `normalize` |
| `normalize` | `"54075240"` and `"52085990 "` → 8-digit string; `""`, `None`, `"5407"`, `"6211.42.90"`, `"abc"` → `None` |
| Mapper | `hsn_override` beats `hsn`; `hsn` alone is written; neither → blank cell and **no flag** |
| Pipeline | `main` writes the export's HSN into the sheet; `hsn_by_sku` still pins a rebuild |
| Generate | `POST /generate` goes straight to the stepper with no HSN screen; `POST /generate/hsn/{id}` is gone (404); new-only and continue-anyway likewise build directly |
| Attributes | HSN renders pre-filled from the sheet; a valid edit is written; an invalid one is rejected with a message and writes nothing; blank clears; the filled total is 14 |
| Attributes | A per-panel save carrying `hsn__{other}` fields writes only the requested ordinal's HSN (the `only` scoping already proven for the other two field kinds) |
| Attributes | The "N SKUs still need an HSN" banner counts blank and malformed alike, and its out-of-band refresh reaches zero when the last gap is filled |
| Registry | `update_hsn` changes only `hsn` on an existing entry; no-op for an unknown SKU; never creates a row; not called when the save is rejected |
| Regression | `scan_content_hashes` output is unchanged for a product that now carries an HSN |

**Regression bar.** The whole existing suite stays green, with attention to
`tests/test_hsn_kb.py` and `tests/test_signature.py` — both must keep passing
against the retained-but-unwired module.

## Out of scope

- **Country of Origin from Shopify.** Same export limitation historically; filled
  from constants today. Not part of this work.
- **Retro-filling HSN for SKUs already in the registry.** Their HSN is pinned
  there and the fix-flow rebuild uses it.
- **The Shopify Admin API route.** The metafield makes it unnecessary.
- **Variant-level metafields.** They do not export; the metafield stays
  product-level.
