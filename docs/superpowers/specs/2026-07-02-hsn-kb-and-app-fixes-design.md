# HSN Knowledge Base + App-Fix Backlog — Design

**Date:** 2026-07-02
**Status:** Approved design, pending spec review
**Scope:** One feature (HSN knowledge base) plus three backlog fixes flagged from real Generate/Fix runs. All ship on one branch `feat/hsn-knowledge-base`.

---

## Why

1. **HSN is mandatory in the Myntra sheet but absent from the Shopify export.** Today Gopal generates the sheet, then fills the 8-digit HSN codes by hand before uploading. The current app only knows two hardcoded codes (cotton→`52081120`, silk→`50072010`) in `config/myntra/rules.yaml`; everything else is manual. HSN depends on category + fabric + finer attributes (knitted/woven, embellishment, blend) that aren't cleanly in the data, so it can't be computed deterministically — but it *can* be learned from the user once and reused.

2. **Three small fixes** observed during real use are bundled in (see `memory/app-fix-backlog.md`).

---

## Part A — HSN Knowledge Base

### A.1 Core model

The app **learns HSN from the user once per signature and reuses it forever**. The unit of knowledge is a **signature** = normalized `category | fabric` (e.g. `saree|pure silk`). The knowledge base (KB) maps each signature to the HSN code(s) used for it, plus the product names behind each code so a suggestion can be sanity-checked.

This **replaces** the two hardcoded HSN codes in `rules.yaml`; those two become the KB's initial seed. The KB becomes the single source of truth for HSN. The existing `fabric_detection` block keeps owning **Saree Fabric / Blouse Fabric / Wash Care** — only `HSN` is removed from it.

A signature may hold **multiple** codes, because the finer attributes that split HSN aren't in the export. When more than one code exists for a signature, all are offered as suggestions.

### A.2 Storage — reuse the ledger pattern

A JSON store shaped exactly like `src/myntra/groupid_ledger.py`, in a **new** `src/myntra/hsn_kb.py`.

- **Prod:** key `state/hsn_kb.json` in the same S3 bucket, via the existing `S3JsonStore`.
- **Dev/offline:** a **separate** local file path. The current `LocalJsonStore` (in `src/web/settings.py`) **ignores the key and writes one file per path**, so the KB must not share the ledger's path. Add a new env var `HSN_LOCAL_PATH` and a `Settings.hsn_local_path` field; add an `hsn_store(settings)` helper mirroring `ledger_store(settings)`.

**File shape:**
```json
{
  "classifications": {
    "saree|pure silk": [
      {"hsn": "50072010", "examples": ["Banarasi Silk Saree Blue"], "count": 4, "last_used": "2026-07-02"}
    ],
    "saree|pure cotton": [
      {"hsn": "52081120", "examples": ["Lavender Pure Cotton Saree"], "count": 2, "last_used": "2026-07-02"}
    ]
  }
}
```

**Seed:** on first read (empty/absent file), initialise from the two current `rules.yaml` codes so nothing regresses.

**`hsn_kb.py` public functions:**
- `signature(product, category)` → normalized `"category|fabric"` string (see A.3).
- `read_kb(store)` → dict, seeding an empty KB.
- `suggest(kb, signature)` → list of stored entries for that signature (may be empty), most-used first.
- `learn(store, signature, hsn, example_name)` → upsert: bump `count`, refresh `last_used`, append `example_name` (cap the examples list, e.g. 5), create the entry/code if new.

### A.3 Signature derivation

One shared `signature()` function, used by **both** the pre-scan (generate router) and the mapper, so they always agree.

- **category** — from the `articleType` constant (today `"Sarees"` → token `saree`). Generalizes when kurtas/lehengas are added.
- **fabric** — from the Shopify `fabric` metafield (`product.fabric`); if blank, fall back to the first `fabric_detection` keyword found in the title. If still unknown → fabric token `unknown`.
- **normalization** — lowercase, strip, collapse internal whitespace; join `category|fabric`.

### A.4 Flow — pause inside Generate ("gather, then ask")

The current Generate flow (`src/web/routers/generate.py`) runs straight through to a file. New shape adds one pause:

- **Phase A — pre-scan (on CSV upload):** read products, compute the set of unique signatures in the batch, look each up in the KB. The job enters a **new `awaiting_hsn` state** instead of building. Persist the pre-scan result (signatures, example product names per signature, KB suggestions) in the job dir, mirroring how the fix flow persists `rows.json`.
- **Review screen:** one row per signature in the batch. Each row shows:
  - the signature (category + fabric) and the product name(s) in this batch under it;
  - an **empty** required input for the 8-digit HSN (**no pre-fill**);
  - **suggestion chips** — the stored code(s) for that signature; clicking a chip fills the input. Each chip shows the past product name(s) that used it, so a suggestion can be judged before accepting.
- **Phase B — build (on submit):** validate each code is exactly 8 digits; `learn()` each into the KB; then run the existing pipeline with a `signature→hsn` map injected. Mapper fills HSN from that map. File builds and downloads exactly as today.

**Always ask** — known signatures are still shown (with suggestions), never silently auto-filled; the user always gives the go. If the batch has **zero** signatures needing HSN (e.g. empty CSV), skip the review screen and build directly.

### A.5 Mapper integration

`src/myntra/mapper.py`:
- Step 5 (`fabric_detection`): stop writing `HSN` from the fabric block (drop the `HSN` key from `rules.yaml`'s cotton/silk blocks — they become the KB seed instead).
- New step: set `HSN` from the injected `signature→hsn` map (`map_product` gains an optional `hsn_by_signature` argument, threaded through `pipeline.main`). If a signature is still unresolved at build time, **flag** it via the existing `Flag` mechanism rather than guessing.
- `HSN` is already in `NUMERIC_HEADERS` (`src/myntra/fill.py`), so the 8-digit code is written as a numeric cell.

### A.6 Edge cases

- **8-digit validation** on submit; reject non-8-digit input with an inline error (re-render the review screen, keep entered values).
- **Overriding a suggestion** with a different code → `learn()` records it as an additional code for that signature (both remain as future suggestions).
- **Unknown fabric** → signature `category|unknown`; still asked, still learned.
- **Concurrent generate runs** are already single-user; no locking beyond the existing store's read-modify-write (acceptable given usage).

---

## Part B — Backlog fixes

### B.1 Country Of Origin auto-fill

**Problem:** the template has **five** Country-Of-Origin columns (`Country Of Origin`, `Country Of Origin2`…`Country Of Origin5`). The `constants.yaml` value `Country Of Origin: India` fills only the base column; the other four are vocab-controlled and get flagged `[BLANK]` every run.

**Fix:** declarative rule in `rules.yaml`:
```yaml
replicate_constant_across_numbered: ["Country Of Origin"]
```
In `mapper.py`, after constants are applied, for each base name in that list find every template header matching `^<base>\d+$` and set it to the same value as the base constant, via `_set_forced` (so it's vocab-validated to `India`, same flag-don't-guess path). Only `Country Of Origin` has this trap among current mandatory constants (verified: `Theme`/`Additional Image` are unrelated); the rule is list-driven so future numbered fields are one config line.

### B.2 Undo for "Mark upload successful"

**Problem:** after confirming, a mistaken confirm (upload didn't actually succeed) can't be reverted.

**Fix:** new `unconfirm(store, batch_id)` in `groupid_ledger.py`:
- revert the batch `status` to `"pending"` and roll `next_style_group_id` back to the batch's `range[0]`.
- **Guard:** only the **most-recently confirmed** batch may be undone — allowed only when `next_style_group_id == batch.range[1] + 1`. Otherwise a later batch already consumed IDs past this range, and rolling back would reissue them; raise `ValueError` and surface a clear message ("can't undo — a later batch was already confirmed").

New route `POST /generate/unconfirm/{job_id}` in `generate.py`. The confirm response HTML gains an **Undo** button (htmx swap); undoing swaps back to the "Mark upload successful" button.

### B.3 "Verify the file yourself" notice

**Fix:** in `_result.html`, near the Download button, add a **bold** reminder:

> **⚠ Please verify the downloaded file yourself and make any changes you need before uploading to Myntra.**

Pure copy; final wording confirmable with Gopal.

---

## Testing

- **`hsn_kb.py`:** `signature()` normalization (fabric from metafield vs title fallback vs unknown); `read_kb` seeding; `suggest` ordering; `learn` upsert (count/examples/last_used).
- **Mapper:** HSN set from injected `hsn_by_signature`; unresolved signature → flag; COO replication across `Country Of Origin2…5`; HSN no longer set by the fabric block.
- **Ledger:** `unconfirm` happy path; guard rejects undo when a later batch was confirmed.
- **Web (mirror `tests/web/test_generate.py` + fix-flow tests):** pre-scan → `awaiting_hsn` → review screen lists signatures + suggestions; submit with 8-digit codes → KB learned → file builds; invalid (non-8-digit) code re-renders with error; undo route reverts ledger; result screen shows the verify notice.

---

## Preview / rollout

- Built on branch `feat/hsn-knowledge-base` (CI/CD deploys **only** on `main`, so the branch is safe).
- Previewed locally with `uvicorn src.web.main:app --reload`, env `AUTH_DISABLED=1`, `LEDGER_LOCAL_PATH=…/ledger.json`, `HSN_LOCAL_PATH=…/hsn_kb.json` → `http://localhost:8000/generate`.
- Merge to `main` only after local verification; CI/CD then deploys to EC2, verified via the SSH tunnel.

## Out of scope

- Standalone HSN-manager/edit page (curate-up-front UI) — not needed now; the review screen is the only entry point.
- Fuzzy title-keyword matching for signatures — deferred; category+fabric is the agreed key.
- Multi-marketplace generalization of the KB.
