# Fix-Error Flow — Design Spec

**Date:** 2026-07-07
**Branch:** `feat/fix-error-flow` (off `feat/hsn-knowledge-base`)
**Status:** Approved design, pre-implementation
**Scope of this spec:** Phases **A + B + C** (read both formats · plain-English explanation engine · two-button flow with instant-text fixes) **plus** the durable correction-log breadcrumb. Phase **D** (learning fixes from Listings-Report outcomes) is explicitly **out of scope** here and gets its own later spec.

---

## 1. Purpose

When a Myntra upload is rejected, the seller gets back a cryptic error file or a plain-English Listings Report. Today the app's `/fix` flow reads only the per-SKU rejection xlsx and explains errors from a hand-authored dictionary that covers a fraction of real wordings.

This feature makes the `/fix` flow:

1. **Accept all three real error/report formats** Myntra produces.
2. **Explain every error in plain English** — hand-authored rules first, then a self-learning dictionary, then a paid Gemini call (explain-only) that teaches the dictionary, then raw-text fallback.
3. **Correct only "instant-text" errors** (values available immediately as text — brand, pincode, address, colour, price) by regenerating a corrected upload file, while **explaining but never touching** anything that needs real work (images, quality, cropping).
4. **Preserve the human gate** — the seller chooses *Proceed with fix & download* or *Do not make any changes*.
5. **Drop a durable breadcrumb** of what was fixed, so a future Phase D can learn which fixes worked.

### Guiding invariants (non-negotiable)

- **The LLM explains; it never fixes or guesses.** Gemini output is display text only. It can never change the corrected file or supply a value.
- **Auto-fixes come only from human-authored `error_rules.yaml` rules.** Learned/Gemini entries are always `explain_only`.
- **Code flags, human decides.** Every correction is behind the Proceed/Do-not-change gate.
- **The app fixes only what a user can provide instantly as text.** Image/quality problems are explained, never auto-corrected — by explicit product decision.

---

## 2. The three input formats

| # | Type | Extension | Fingerprint columns (presence, not order) | Contains full product data? |
|---|---|---|---|---|
| A | Per-SKU rejection (`SKU_VALIDATION_FAILED`) | `.xlsx` | a sheet whose header row has both `STATUS` and `SYSTEM ERROR MESSAGE` (+ the 82 product columns) | **Yes** — correct in place |
| A′ | File-level rejection (`SHEET_VALIDATION_FAILED`) | `.csv` | `ROW NO`, `STATUS`, `SYSTEM ERROR MESSAGE` | No — identifiers/message only |
| B | Listings Report (`MDirect`) | `.csv` | `style status`, `seller sku code`, `onhold reason` (no `SYSTEM ERROR MESSAGE`) | No — identifiers/status/reason only |

**Cryptic vs plain:** A and A′ carry cryptic/technical messages → go through the explanation engine. B's `onhold reason` is already plain English → passed straight through (no Gemini).

**Real fixtures on branch** (`errors/myntra/`):
- A: `wLf4susb_file.xlsx` (HSN mismatch, duplicate, one-size mapping, size-label).
- A′: `Output file error.csv`, `error-*.csv` (StyleGroupId count; brand-code null).
- B: `TZAly58W_2026-07-03_MDirect_Listings_Report_87065.csv` (image + address rejections, `P`=live / `PMR`=rejected).

---

## 3. Architecture & data flow

```
Upload to /fix
  → [1] Detect & Read   3 formats → one normalized ErrorItem list
  → [2] Explain         plain English (translate cryptic; pass-through Listings Report)
  → [3] Classify        correctable (instant-text) vs explain-only
  → [4] Review screen    two groups + human gate (Proceed / Do not change)
  → [5] Regenerate       corrected file (correctable SKUs only) + correction-log breadcrumb
```

### Normalized record

```
ErrorItem = {
  sku: str | None,          # None for file-level (A′) errors that reject the whole sheet
  style_id: str | None,
  source_type: "sku_xlsx" | "sheet_csv" | "listings_report",
  scope: "sku" | "sheet",   # "sheet" = applies to the whole file, not one SKU
  raw_reason: str,          # one clause / one onhold reason
  cells: dict | None,       # full product row when the source carries it (A only)
}
```
Cryptic messages are split into clauses (on `;`, as the current reader already does); each clause becomes its own `ErrorItem` and is explained independently.

### Module map

| Module | New / Extend | Purpose |
|---|---|---|
| `src/myntra/error_sources.py` | **new** (absorbs `error_reader.py`) | first gate (extension) → parse → **fingerprint detect** → 3 readers → normalized `ErrorItem` list |
| `src/myntra/signature.py` | **new** | error clause → normalized signature (+ captured values) |
| `src/myntra/explanation_store.py` | **new** | JSON learned dictionary, atomic writes |
| `src/myntra/gemini_client.py` | **new** | explain-only LLM call + fallback |
| `src/myntra/explainer.py` | **new** | orchestrates YAML → learned store → Gemini → raw |
| `src/myntra/correction_log.py` | **new** | append the Phase-D breadcrumb |
| `src/myntra/corrector.py` | extend | bucket routing + Surface-B data-source resolver + brand auto-fix |
| `src/web/routers/fix.py` | extend | accept `.csv`, run pipeline, render |
| `src/web/templates/_fix_review.html`, `_fix_result.html` | extend | two groups + gate |

---

## 4. Detection (step 1)

Two gates:

1. **Extension gate** — accept `.xlsx` and `.csv`; reject others immediately.
2. **Content fingerprint** — open the file and match columns against the three fingerprints in §2 (by **column presence**, not sheet name or column order — this also fixes the current hardcoded `sheet="Sarees"` fragility).

`unknown format` has two precise meanings:
- **Won't parse** → "Couldn't read this file."
- **Parses but matches no fingerprint** (e.g. a Shopify export or unrelated sheet) → "This doesn't look like a Myntra rejection or Listings Report — please upload the rejection file or the downloaded Listings Report."

---

## 5. Explanation engine (step 2)

Runs per clause, **cryptic sources only** (Listings Report reasons pass through unchanged).

**Lookup order — first hit wins, cheapest first:**

1. **YAML rule** (`error_rules.yaml`, substring match, as `classify()` works today) → explanation **+ action**. The only source that can carry an auto-fix.
2. **Learned store** (match on normalized signature) → cached plain-English explanation, `action: explain_only`.
3. **Gemini** (explain-only) → explanation → **write into learned store** keyed by signature → serve. Called at most **once per error type, ever**.
4. **Fallback** (Gemini off/down/error) → short retry with backoff → show the **raw message**.

### Signature normalization (`signature.py`)

- Strip only obvious variable data: digit runs → `<NUM>`, SKU/article codes → `<SKU>`, URLs → `<URL>`; lowercase; collapse whitespace. **Keep every English word.**
- Capture stripped values so a stored explanation can be a **template** that slots real numbers back in.
- Example: `Seller Sku Code 169SDE326SFSF ... style id 43427259` → `seller sku code <SKU> ... style id <NUM>`.
- **Tuned against the real fixture files** — same error for two SKUs must collapse to one signature; two different errors must stay distinct.

### Gemini client (`gemini_client.py`) — guardrails

- Sends **only** the error text (`STATUS` + `SYSTEM ERROR MESSAGE`), never the product row → manufacturer/packer name, address, and pincode never leave the machine.
- **Explain-only prompt**: "Explain this Myntra rejection in plain English. Do not suggest or invent fixes or values."
- Paid-tier key via existing config; model = Gemini Flash.

---

## 6. Correction engine (steps 3 & 5)

### Buckets (driven by YAML `action`)

| Bucket | Action | Behaviour |
|---|---|---|
| **Auto-fill** | `auto_fix` | Fill from `config/myntra/constants.yaml` — brand → `"Ijor Ethnic Partners"`, pincode/address → saved constants. No user input. |
| **Ask-user** | `manual_choice` | Text box; user types the value once; validated against Myntra vocab before writing (as `correct()` already does). |
| **Explain-only** | `explain_only` / learned / Gemini | Images, quality, cropping, pixelation, anything not instant-text. **App writes nothing**; shows "fix these first, then re-upload." |

Only buckets 1 & 2 change the file, and both originate from human-authored YAML rules.

### Data-source resolver (regeneration)

On *Proceed*, full product data per correctable SKU is resolved by surface:
- **Surface A** (`sku_xlsx`): data is in the uploaded file → correct in place.
- **Surface B / A′** (`listings_report`, `sheet_csv`): look up each rejected SKU in **build records (`sku_registry.py`)**, falling back to the **Shopify export (`shopify_reader.py`)** → rebuild rows → apply fix → regenerate.
- SKU resolvable in neither source → reported "couldn't rebuild — data not found," never silently dropped.

**Sheet-scoped (A′) corrections:** a file-level error (e.g. brand-code null) is not tied to one SKU — the fix (e.g. the correct registered brand) applies **sheet-wide**. The app rebuilds the **whole sheet** for that build from `sku_registry.py` / the Shopify export with the corrected value, rather than editing one row. Sheet-scoped issues that are not instant-text (e.g. StyleGroupId count) stay explain-only (re-generate guidance).

### Output of a Proceed run

- A regenerated `myntra_corrected.xlsx` containing **only correctable SKUs**, fixes applied.
- Explain-only SKUs listed separately ("you must fix these yourself first"), **excluded** from the file.
- One **correction-log** record appended per corrected SKU.
- Result screen summary: *fixed & written* · *needs your manual work* · *couldn't rebuild*.

---

## 7. Persistent state

Two new JSON files (rest already exists). All follow the SKU-registry pattern: env-configurable path, atomic write (temp → `os.replace`) + lock, human-readable.

**1. Learned explanation store** (`explanation_store.py`):
```json
{ "seller sku code <SKU> is already registered ... style id <NUM>":
    { "explanation_template": "This SKU is already live on Myntra under style {NUM}...",
      "count": 3, "first_seen": "2026-07-07" } }
```
Written only when Gemini explains something new. Human-editable. Promotable into `error_rules.yaml`; as it fills, Gemini calls fall toward zero and can be switched off.

**2. Correction log** (`correction_log.py`) — append-only, the **Phase-D breadcrumb**:
```json
[ { "timestamp": "2026-07-07T10:30:00", "fix_id": "ab12…", "sku": "169SDE326SFSF",
    "signature": "seller sku code <SKU> is already registered ...",
    "changes": { "brand": ["", "Ijor Ethnic Partners"], "pincode": ["", "121006"] } } ]
```
**Not read by anything in this build** — it just accumulates so Phase D can later join it against a Listings Report and learn which fixes worked.

**Unchanged / existing:** `error_rules.yaml` (only source of auto-fix actions), `config/myntra/constants.yaml` (fill values), `sku_registry.py` + Shopify export (Surface-B data). Per-session `RUNTIME/fix-<id>/` stays ephemeral.

**Deliberately omitted (YAGNI):** a `promoted` flag on learned entries; archiving old Listings Reports for Phase D.

---

## 8. Error handling / graceful degradation

The review screen must always render:

- **Gemini unavailable** (no key, timeout, rate-limit, network): retry w/ backoff → raw message. Logged.
- **Unknown format**: the two clear messages from §4.
- **SKU can't be rebuilt** (Surface B): reported per-SKU, never silently dropped.
- **Bad user answer** (fails vocab check): re-prompted (existing `correct()` behaviour).
- **Corrupt/half-written store**: atomic writes prevent it; malformed JSON is caught and treated as empty (log + continue).
- **Malformed learned template** (missing `{NUM}`): fall back to plain explanation without interpolation.

---

## 9. Config

Following the existing env/SSM pattern (`HSN_LOCAL_PATH` style):

- `GEMINI_API_KEY` — paid tier, `.env` locally / SSM on EC2 (git-ignored).
- `GEMINI_MODEL` — default Gemini Flash.
- `EXPLANATION_STORE_PATH`, `CORRECTION_LOG_PATH` — default alongside the SKU registry.
- `EXPLAIN_WITH_GEMINI` — master on/off switch; off = pure-dictionary/offline. Lets Gemini be retired once the dictionary is full enough.

---

## 10. Testing (TDD, mirrors `tests/test_error_reader.py`)

- `signature.py` — same error + different SKUs → one signature; different errors → distinct (real fixtures).
- `explanation_store.py` — write/read/atomic-replace; corrupt file → treated as empty.
- `explainer.py` — lookup order (YAML > store > Gemini), Gemini **mocked** (no live calls).
- `gemini_client.py` — payload contains **only** the two error columns (assert no product data); fallback on failure.
- `error_sources.py` — each of the 3 formats → correct normalized list (fixtures: `wLf4susb`, the CSVs, the Listings Report); unknown/corrupt → correct errors.
- `corrector.py` — bucket routing; Surface-B data resolution; image errors never written; correction-log record written.
- End-to-end per surface: upload → explained review → Proceed → corrected file excludes explain-only SKUs.

---

## 11. Out of scope (Phase D — later spec)

- Reading the Listings Report for **success** detection (`P` vs `PMR`).
- Joining the correction log against outcomes.
- Suggesting learned fixes for Tier-2 (judgment) errors.

This build only **writes** the breadcrumb that Phase D will consume.
