# Fix Review Screen — Two Actions + Manual-Fix Rebuild — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Fix review screen's "you must fix these yourself" group a button that rebuilds a ready-to-upload Myntra sheet for *only* those SKUs (pinning the original HSN/styleGroupId), and relabel the correctable group's button to "Fix & download now".

**Architecture:** Pure web-layer change. One `<form>` on `_fix_review.html` gains a second named submit button (`action=manual`) plus guidance copy; `src/web/routers/fix.py` branches on the posted `action` and, for `manual`, scopes the existing `regenerate_surface_b` rebuild to the explain-only SKUs. No new endpoints, templates, or pipeline changes. Registry pinning of HSN/styleGroupId is already handled inside `regenerate_surface_b`.

**Tech Stack:** FastAPI, htmx, Jinja2, pytest, Starlette `TestClient`.

**Spec:** `docs/superpowers/specs/2026-07-08-fix-manual-rebuild-two-actions-design.md`

## Global Constraints

- **Build off `main`** on a new branch `feat/fix-manual-rebuild`. `main` already has commit 161c69c (Surface-B export fix) which this reuses.
- **TDD**: write the failing test first, watch it fail, then implement. Never `required` on a hidden input ([[web-ui-gotchas]]).
- **htmx swaps only on 2xx** — every user-facing outcome must return HTTP 200 (the existing try/except wrapper in `fix_apply` already guarantees this).
- **Reuse, do not reinvent**: `_save_export`, `_export_prompt_panel`, `_error_panel`, `regenerate_surface_b(skus, settings, fix_dir, csv_path=)`, `_fix_result.html` all exist from 161c69c — call them, don't duplicate.
- Full suite must stay green: `python -m pytest -q` (currently 166 passing).
- Existing action value for the correctable path is the default `"fix"`; the manual path is `"manual"`.

## File Structure

- **Modify** `src/web/routers/fix.py` — `fix_upload` widens `needs_export`; `_fix_apply` branches on the posted `action` and scopes the rebuild to explain-only SKUs for `action=manual`.
- **Modify** `src/web/templates/_fix_review.html` — relabel the correctable button and give it `name="action" value="fix"`; add the manual-fix guidance block + `name="action" value="manual"` button.
- **Modify** `tests/web/test_fix.py` — new unit/route tests.
- **Modify** `tests/web/test_fix_e2e.py` — optional real-pipeline manual-rebuild e2e.

---

### Task 1: Widen `needs_export` and render the manual-fix button + guidance

Make the export input and the new "Download listing file" button appear whenever there are explain-only SKUs, and give the correctable button its `action=fix` name/value + new label.

**Files:**
- Modify: `src/web/routers/fix.py` (the `needs_export` line in `fix_upload`, ~line 110)
- Modify: `src/web/templates/_fix_review.html`
- Test: `tests/web/test_fix.py`

**Interfaces:**
- Consumes: `fix_upload` already computes `correctable` / `explain_only` lists and passes `needs_export` to `_fix_review.html`.
- Produces: template renders (a) an export file input `name="products_export"` and (b) a submit button `name="action" value="manual"` labeled "Download listing file", plus guidance text, whenever `explain_only` is non-empty. The correctable button becomes `name="action" value="fix"` labeled "Fix & download now".

- [ ] **Step 1: Write the failing test**

Add to `tests/web/test_fix.py`. Use the existing `_client()`, `_items()` helpers. `_items()` yields one correctable (`78SAZ`) and one explain-only (`IMG1`) `sku_xlsx` item, so this also proves the export input now shows for `sku_xlsx` when explain-only exists.

```python
def test_upload_explain_only_shows_manual_download_button_and_guidance(monkeypatch):
    """The explain-only group must offer a 'Download listing file' button (action=manual),
    guidance copy, and the shared products_export input — even for a sku_xlsx rejection."""
    client = _client()
    monkeypatch.setattr(fixmod, "detect_format", lambda p: ("sku_xlsx", ""))
    monkeypatch.setattr(fixmod, "read_error_file", lambda p, rules: _items())
    r = client.post("/fix", files={"file": ("rej.xlsx", b"x",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 200
    assert 'value="manual"' in r.text
    assert "Download listing file" in r.text
    assert "re-export just these SKUs" in r.text          # guidance copy
    assert 'name="products_export"' in r.text              # shared export input now shown
    assert 'value="fix"' in r.text                         # correctable button carries action=fix
    assert "Fix &amp; download now" in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/web/test_fix.py::test_upload_explain_only_shows_manual_download_button_and_guidance -v`
Expected: FAIL — `'value="manual"' not in r.text` (button doesn't exist yet); `needs_export` is False for `sku_xlsx` so no export input.

- [ ] **Step 3: Widen `needs_export` in `fix_upload`**

In `src/web/routers/fix.py`, replace the `needs_export` assignment (currently:
`needs_export = source_type in ("sheet_csv", "listings_report") and bool(correctable)`):

```python
    # Either a Surface-B correctable fix OR a manual-fix rebuild (of the
    # explain-only SKUs) re-runs the pipeline, which needs the Shopify export.
    needs_export = (
        (source_type in ("sheet_csv", "listings_report") and bool(correctable))
        or bool(explain_only)
    )
```

- [ ] **Step 4: Update the template**

In `src/web/templates/_fix_review.html`:

Relabel the correctable button (inside `{% if correctable %}` in `.actions`):
```html
      <button class="btn" type="submit" name="action" value="fix">Fix &amp; download now →</button>
```

Add the guidance + manual button at the end of the explain-only block, immediately before the closing `{% endif %}` of `{% if explain_only %}` (after the `{% endfor %}` that renders the explain-only cards):
```html
    <div class="card need">
      <p><strong>These need a real fix the app can't do (photos, image quality, resolution).</strong></p>
      <p>Fix them in <strong>Shopify first</strong> — re-shoot and upload the corrected photos onto the
         product — then <strong>re-export just these SKUs</strong> from Shopify and drop that file in the
         box below.</p>
      <p>Click <strong>Download listing file</strong> and the app rebuilds a ready-to-upload Myntra sheet
         for these SKUs with your new images, keeping the <strong>same HSN and style group</strong> as your
         first attempt so Myntra won't reject them again.</p>
      <button class="btn" type="submit" name="action" value="manual">Download listing file for these SKUs →</button>
    </div>
```

(The `needs_export` export-input block and the `hx-encoding` on the `<form>` already exist from 161c69c and now render because `needs_export` is true whenever `explain_only` is present.)

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/web/test_fix.py::test_upload_explain_only_shows_manual_download_button_and_guidance -v`
Expected: PASS

- [ ] **Step 6: Guard the existing correctable label test**

Run: `python -m pytest tests/web/test_fix.py -q`
Expected: PASS. If `test_upload_groups_correctable_and_explain_only` asserted the old `"Proceed"` label, update that assertion to `"Fix &amp; download now"` (search the test file). The label lives only in the template now.

- [ ] **Step 7: Commit**

```bash
git add src/web/routers/fix.py src/web/templates/_fix_review.html tests/web/test_fix.py
git commit -m "feat(web): Fix screen offers a manual-fix rebuild button + guidance"
```

---

### Task 2: `action=manual` rebuilds only the explain-only SKUs

Branch `_fix_apply` on the posted `action`. For `manual`, rebuild the explain-only SKUs via `regenerate_surface_b` with the uploaded export; keep `fix` behavior identical.

**Files:**
- Modify: `src/web/routers/fix.py` (`_fix_apply`, ~lines 132-179)
- Test: `tests/web/test_fix.py`

**Interfaces:**
- Consumes: `_load_issues(fix_dir) -> (source_type, issues)`; each issue has `.sku` and `.action` (explain-only issues have `action == "explain_only"`). `_save_export(upload, fix_dir) -> csv_path|None`, `_export_prompt_panel()`, `regenerate_surface_b(skus, settings, fix_dir, csv_path=)` — all already exist. The form field carrying intent is `action` (default `"fix"`).
- Produces: for `action=manual`, a `_fix_result.html` render whose `summary.fixed` contains exactly the explain-only SKUs (or `could_not_rebuild` for unknown ones); `regenerate_surface_b` invoked with `skus == explain_only_skus` and a real `csv_path`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/web/test_fix.py`. Reuse `_lr_correctable()` (yields correctable `LR1`) and add a local helper that mixes a correctable and an explain-only SKU. `_client(raise_server=...)` already exists.

```python
def _lr_mixed():
    """One correctable (LR1, auto_fix pincode) + one explain-only (IMGX, no rule match)."""
    from src.myntra.error_sources import ErrorItem
    return [
        ErrorItem(sku="LR1", style_id=None, source_type="listings_report", scope="sku",
                  raw_reason="Pincode is missing", cells={}),
        ErrorItem(sku="IMGX", style_id=None, source_type="listings_report", scope="sku",
                  raw_reason="Image resolution is too low and totally unmatched by any rule", cells={}),
    ]


def test_apply_manual_rebuilds_only_explain_only_skus(monkeypatch):
    client = _client()
    monkeypatch.setattr(fixmod, "detect_format", lambda p: ("listings_report", ""))
    monkeypatch.setattr(fixmod, "read_error_file", lambda p, rules: _lr_mixed())

    captured = {}

    def fake_regen(skus, settings, fix_dir, csv_path=None):
        captured["skus"] = list(skus)
        with open(csv_path, "rb") as fh:
            captured["bytes"] = fh.read()
        return {"written": 1, "file": None, "fixed": list(skus), "could_not_rebuild": [],
                "dropped": [], "rejected": {}, "changed": {}, "manual_needed": []}

    monkeypatch.setattr(fixmod, "regenerate_surface_b", fake_regen)

    up = client.post("/fix", files={"file": ("rej.csv", b"x", "text/csv")})
    fix_id = up.headers["x-fix-id"]
    r = client.post(f"/fix/apply/{fix_id}",
                    data={"action": "manual"},
                    files={"products_export": ("products_export.csv", b"Handle\nabc\n", "text/csv")})
    assert r.status_code == 200
    assert captured["skus"] == ["IMGX"]            # only the explain-only SKU, NOT LR1
    assert captured["bytes"] == b"Handle\nabc\n"


def test_apply_manual_without_export_prompts_and_does_not_rebuild(monkeypatch):
    client = _client()
    monkeypatch.setattr(fixmod, "detect_format", lambda p: ("listings_report", ""))
    monkeypatch.setattr(fixmod, "read_error_file", lambda p, rules: _lr_mixed())

    called = {"regen": False}

    def fake_regen(skus, settings, fix_dir, csv_path=None):
        called["regen"] = True
        return {}

    monkeypatch.setattr(fixmod, "regenerate_surface_b", fake_regen)

    up = client.post("/fix", files={"file": ("rej.csv", b"x", "text/csv")})
    fix_id = up.headers["x-fix-id"]
    r = client.post(f"/fix/apply/{fix_id}", data={"action": "manual"})
    assert r.status_code == 200
    assert called["regen"] is False
    assert "products export" in r.text.lower()


def test_apply_fix_action_still_scopes_to_correctable_only(monkeypatch):
    """Regression: the default action=fix must rebuild only correctable SKUs, never
    the explain-only ones."""
    client = _client()
    monkeypatch.setattr(fixmod, "detect_format", lambda p: ("listings_report", ""))
    monkeypatch.setattr(fixmod, "read_error_file", lambda p, rules: _lr_mixed())

    captured = {}

    def fake_regen(skus, settings, fix_dir, csv_path=None):
        captured["skus"] = list(skus)
        return {"written": 1, "file": None, "fixed": list(skus), "could_not_rebuild": [],
                "dropped": [], "rejected": {}, "changed": {}, "manual_needed": []}

    monkeypatch.setattr(fixmod, "regenerate_surface_b", fake_regen)

    up = client.post("/fix", files={"file": ("rej.csv", b"x", "text/csv")})
    fix_id = up.headers["x-fix-id"]
    r = client.post(f"/fix/apply/{fix_id}",
                    data={"action": "fix"},
                    files={"products_export": ("products_export.csv", b"Handle\nabc\n", "text/csv")})
    assert r.status_code == 200
    assert captured["skus"] == ["LR1"]             # only correctable, IMGX excluded
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/web/test_fix.py -k "manual or fix_action_still" -v`
Expected: FAIL — `test_apply_manual_rebuilds_only_explain_only_skus` will rebuild `["LR1"]` (current code always uses the correctable set) instead of `["IMGX"]`.

- [ ] **Step 3: Read the `action` field and branch in `_fix_apply`**

In `src/web/routers/fix.py`, in `_fix_apply`, capture `action` while iterating the form. The loop currently sets `answers`, `submitted_drops`, `export_upload`; add:

```python
    answers, submitted_drops, export_upload = {}, set(), None
    action = "fix"
    for key, value in form.items():
        if key == "action":
            action = str(value) or "fix"
        elif key == "products_export":
            export_upload = value
        elif key.startswith("answer__") and str(value).strip():
            _, sku, field = key.split("__", 2)
            answers.setdefault(sku, {})[field] = value
        elif key.startswith("drop__"):
            submitted_drops.add(key.split("__", 1)[1])
```

Then, immediately after `out_path = os.path.join(fix_dir, "myntra_corrected.xlsx")` and BEFORE the `if source_type == "sku_xlsx":` block, insert the manual branch:

```python
    if action == "manual":
        # Rebuild ONLY the explain-only SKUs into a fresh Myntra sheet, pinning the
        # original HSN/styleGroupId (regenerate_surface_b reads the registry). This
        # is the "I fixed the photo in Shopify, re-export, give me a sheet" path.
        skus = sorted({i.sku for i in issues
                       if i.sku and i.action == "explain_only"})
        if not skus:
            summary = {"written": 0, "file": None, "fixed": [], "could_not_rebuild": [],
                       "dropped": [], "rejected": {}, "changed": {}, "manual_needed": []}
            return _templates().TemplateResponse(request, "_fix_result.html",
                                                 {"summary": summary, "fix_id": fix_id})
        csv_path = _save_export(export_upload, fix_dir)
        if csv_path is None:
            return _export_prompt_panel()
        summary = regenerate_surface_b(skus, settings, fix_dir, csv_path=csv_path)
        if summary.get("file") and os.path.exists(summary["file"]):
            shutil.copyfile(summary["file"], out_path)
        return _templates().TemplateResponse(request, "_fix_result.html",
                                             {"summary": summary, "fix_id": fix_id})
```

The existing `if source_type == "sku_xlsx": ... else: ...` block below is unchanged and handles `action == "fix"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/web/test_fix.py -k "manual or fix_action_still" -v`
Expected: PASS (all three).

- [ ] **Step 5: Run the whole fix test file**

Run: `python -m pytest tests/web/test_fix.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/web/routers/fix.py tests/web/test_fix.py
git commit -m "feat(web): action=manual rebuilds only the explain-only SKUs from the export"
```

---

### Task 3: Real-pipeline manual-rebuild e2e (regression guard)

Add a non-monkeypatched end-to-end that drives the real pipeline for the manual path, mirroring `test_surface_b_real_rebuild_end_to_end`. Closes the same coverage gap for the new action.

**Files:**
- Modify: `tests/web/test_fix_e2e.py`
- Test: itself

**Interfaces:**
- Consumes: `_client(tmp_path)`, `_fake_image_bytes()` (both exist in `test_fix_e2e.py`); fixture `tests/fixtures/products_export.csv` (SKUs `TST001`, `TST002`). The monkeypatch recipe from `test_surface_b_real_rebuild_end_to_end` (stub `pipe.process_images` fetch, `s3.upload_images`, `corrector.read_registry`/`sku_registry_store`).
- Produces: a passing e2e proving `action=manual` yields a downloadable, valid `.xlsx` for an explain-only SKU.

- [ ] **Step 1: Write the failing test**

The rejection reason must NOT match any auto_fix rule (so the SKU is explain-only). Use an image reason that no rule matches for `listings_report`; recall `listings_report` reasons that don't match a rule become `explain_only` (plain pass-through). `TST001` exists in the fixture export.

```python
def test_manual_rebuild_real_pipeline_end_to_end(monkeypatch, tmp_path):
    """action=manual drives the REAL pipeline from an uploaded export for an
    explain-only SKU and downloads a valid xlsx. Only image fetch + S3 are stubbed."""
    import src.myntra.pipeline as pipe
    import src.myntra.corrector as corrector
    import src.core.s3_upload as s3
    from src.core.images import process_images as real_process_images

    img = _fake_image_bytes()
    monkeypatch.setattr(pipe, "process_images",
                        lambda p, specs, out_dir: real_process_images(
                            p, specs, out_dir, fetch=lambda url: img))
    monkeypatch.setattr(s3, "upload_images", lambda *a, **k: [])
    monkeypatch.setattr(corrector, "read_registry", lambda store: {})
    monkeypatch.setattr(corrector, "sku_registry_store", lambda s: object())

    client = _client(tmp_path)
    # A reason that matches NO configured rule -> explain_only (plain) for listings_report.
    listings = (b'"style status","seller sku code","onhold reason","style id"\r\n'
                b'"PMR","TST001","image resolution is too low, no rule matches this wording","43214808"\r\n')
    up = client.post("/fix", files={"file": ("MDirect_Listings_Report.csv", listings, "text/csv")})
    assert up.status_code == 200
    assert "Download listing file" in up.text          # manual button rendered
    fix_id = up.headers["x-fix-id"]

    with open("tests/fixtures/products_export.csv", "rb") as fh:
        export_bytes = fh.read()
    r = client.post(f"/fix/apply/{fix_id}",
                    data={"action": "manual"},
                    files={"products_export": ("products_export.csv", export_bytes, "text/csv")})
    assert r.status_code == 200
    assert "Download corrected xlsx" in r.text

    dl = client.get(f"/fix/download/{fix_id}")
    assert dl.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(dl.content))
    ws = wb["Sarees"]
    assert ws.cell(row=4, column=3).value not in (None, "")   # TST001 rebuilt into the sheet
```

- [ ] **Step 2: Run to verify it fails (or passes) for the right reason**

Run: `python -m pytest tests/web/test_fix_e2e.py::test_manual_rebuild_real_pipeline_end_to_end -v`
Expected: PASS once Tasks 1-2 are in (the production code already supports it). If it FAILS, confirm the reason string matches no rule in `config/myntra/error_rules.yaml` (it must be explain-only) and that `TST001` is in the fixture export.

- [ ] **Step 3: Run the whole e2e file**

Run: `python -m pytest tests/web/test_fix_e2e.py -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/web/test_fix_e2e.py
git commit -m "test(web): real-pipeline e2e for the manual-fix rebuild path"
```

---

### Task 4: Full suite + finish

**Files:** none (verification + integration).

- [ ] **Step 1: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS (≥ 166 + the new tests).

- [ ] **Step 2: Manual smoke (optional, app running)**

Drive `/fix` in the browser: upload a Listings Report with an image-rejection SKU → confirm the "Download listing file" button + guidance render, the export input is required, and clicking it (with an export attached) downloads a sheet. Confirm "Fix & download now" still works for correctable SKUs.

- [ ] **Step 3: Merge + deploy**

Per [[ci-cd-monitoring-hands-off]] and [[ec2-deploy-stage1-done]]: `git checkout main && git merge --ff-only feat/fix-manual-rebuild && git push origin main`. EC2 must be running for the auto-deploy. Report the push is triggered; Gopal watches CI/CD himself.

---

## Self-Review

**Spec coverage:**
- Decision (a) two buttons/labels → Task 1 (labels + `action` values). ✅
- Decision (b) manual rebuilds explain-only via `regenerate_surface_b` → Task 2. ✅
- Decision (c) shared export input → Task 1 (`needs_export` widened; existing input reused). ✅
- Decision (d) clicked button's `name="action"` value → Tasks 1 (buttons) + 2 (read + branch). ✅
- Decision (e) guidance message → Task 1 copy. ✅
- Decision (f) no new image path → nothing added; images from export only. ✅
- §3 `needs_export` formula → Task 1 Step 3 verbatim. ✅
- §3 empty explain-only short-circuit → Task 2 Step 3. ✅
- Error handling reuse (prompt/panel) → Task 2 (calls `_export_prompt_panel`; wrapper untouched). ✅
- Testing list → Tasks 1-3 cover each bullet (button+guidance+input render; manual scoping + bytes; manual-no-export prompt; empty short-circuit; fix-still-correctable regression; needs_export when explain-only; real e2e). ✅

**Placeholder scan:** No TBD/TODO; every code step shows full code and exact commands. ✅

**Type consistency:** `action` (str, default `"fix"`), `regenerate_surface_b(skus, settings, fix_dir, csv_path=)`, `_save_export`, `_export_prompt_panel`, `_fix_result.html` context `{summary, fix_id}` — all match 161c69c and are used consistently across tasks. ✅
