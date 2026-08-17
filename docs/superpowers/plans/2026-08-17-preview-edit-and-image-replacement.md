# Editable Preview + Product Image Replacement — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the owner re-open any filled Myntra workbook in the app to edit attributes, and replace product images that Myntra rejected, without regenerating from Shopify.

**Architecture:** An uploaded workbook is *adopted as a job* — written into a runtime job directory and registered in the in-memory job store as an already-finished job. Every existing downstream surface (attribute panels, vocabulary dropdowns, live preview card, per-panel save, registry pinning, download) then works unchanged, because none of them ever cared where the workbook came from. Image replacement is a new pure module that reuses `src/core/images.py` for conversion/validation and `src/core/s3_upload.py` for hosting, writing the resulting URLs through the existing `write_attributes` (which already re-applies the inline-strings fix Myntra requires).

**Tech Stack:** Python 3.12 · FastAPI · Jinja2 · htmx (vendored) · openpyxl · Pillow · boto3 · pytest

**Spec:** `docs/superpowers/specs/2026-08-17-preview-edit-and-image-replacement-design.md`

## Global Constraints

- **Never run the full test suite.** It is ~323 tests and 60+ minutes. Run only the test files named in the task you are on.
- **`shared_to_inline` must be re-applied after ANY openpyxl re-save of a built workbook.** openpyxl recreates shared strings and Myntra's parser cannot resolve them — the file is rejected with no local symptom. Route every workbook write through `attribute_entry.write_attributes`, which already does this.
- **Never name a Jinja context dict key `values`.** `p.values` resolves to the built-in `dict.values` method, not the key, and fails silently. The panel dict uses `chosen` for this reason.
- **Dropdown options come strictly from the Myntra template vocabulary.** No invented values. This plan does not change vocabulary handling.
- **Image specs are fixed by `config/myntra/image_specs.yaml`:** `min_width: 700`, `min_height: 700`, `max_bytes: 10485760`, `quality: 90`, `max_images: 7`.
- **The SKU is never editable in the app.** Out of scope by the owner's decision.
- **htmx out-of-band swaps only work on top-level elements of a response fragment.** A nested `hx-swap-oob` is silently ignored.
- Commit after every task. Work on branch `feat/preview-edit-image-replace`.

---

# Phase 1 — Editable Preview (adoption + Clear)

Delivers the owner's original request on its own: re-open any filled sheet and edit it.

---

### Task 1: Adopt an uploaded workbook as a job

**Files:**
- Modify: `src/web/routers/preview.py:28-44` (replace `preview_submit`)
- Create: `src/web/templates/_preview_error.html`
- Test: `tests/web/test_preview.py`

**Interfaces:**
- Consumes: `src.web.jobs.store` (`create()`, `finish(job_id, result)`), `src.web.routers.generate.RUNTIME`
- Produces: a finished job whose `result` is `{"filled": <path>, "origin": "upload", "filename": <original name>, "products": <int>}`. Tasks 2, 3, 4 and 8 all read `result["origin"]` and `result["filled"]`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/web/test_preview.py`:

```python
import src.web.routers.generate as gen
from src.web.jobs import store

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_upload_adopts_the_workbook_as_an_editable_job(tmp_path, monkeypatch):
    """The uploaded file becomes a job, so every Fill-attributes surface works on
    it unchanged. Without this the sheet is only viewable, never editable."""
    monkeypatch.setattr(gen, "RUNTIME", str(tmp_path / "runtime"))
    out = _filled(tmp_path)
    client = _client(tmp_path)
    with open(out, "rb") as fh:
        r = client.post("/preview", files={"file": ("mysheet.xlsx", fh.read(), XLSX)})
    assert r.status_code == 200
    target = r.headers["hx-redirect"]
    assert target.startswith("/generate/attributes/")
    job = store.get(target.rsplit("/", 1)[1])
    assert job.result["origin"] == "upload"
    assert job.result["filename"] == "mysheet.xlsx"
    assert client.get(target).status_code == 200


def test_upload_with_no_sku_rows_is_rejected_and_creates_no_job(tmp_path, monkeypatch):
    """The bare template has no data rows. Adopting it would present an empty
    accordion with no explanation; the user needs to know it was the wrong file."""
    monkeypatch.setattr(gen, "RUNTIME", str(tmp_path / "runtime"))
    before = len(store._jobs)
    with open(V13, "rb") as fh:
        r = _client(tmp_path).post(
            "/preview", files={"file": ("template.xlsx", fh.read(), XLSX)})
    assert r.status_code == 200
    assert "hx-redirect" not in r.headers
    assert "no products" in r.text.lower()
    assert len(store._jobs) == before
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/web/test_preview.py -q -k "adopts or no_sku_rows"`
Expected: FAIL — `KeyError: 'hx-redirect'`

- [ ] **Step 3: Write the implementation**

Replace `preview_submit` in `src/web/routers/preview.py`:

```python
@router.post("/preview", response_class=HTMLResponse)
async def preview_submit(request: Request, file: UploadFile = File(...)):
    """Adopt the uploaded workbook as a job, so the Fill-attributes screen can edit
    it. The job store is the only thing that screen needs; nothing downstream cares
    that this workbook was uploaded rather than built."""
    get_user(request)
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Please upload the filled .xlsx file")
    from src.web.routers.generate import RUNTIME
    job = store.create()
    job_dir = os.path.join(RUNTIME, job.id)
    os.makedirs(job_dir, exist_ok=True)
    xlsx = os.path.join(job_dir, "myntra_filled.xlsx")
    with open(xlsx, "wb") as out:
        shutil.copyfileobj(file.file, out)

    template = read_template(TEMPLATE)
    rows = read_filled_rows(xlsx, template)
    if not rows:
        # Wrong file (a bare template, a Shopify CSV renamed, last year's format).
        # Drop it rather than present an empty accordion with no explanation.
        store.drop(job.id)
        shutil.rmtree(job_dir, ignore_errors=True)
        return _templates().TemplateResponse(request, "_preview_error.html", {
            "message": "That file has no products in it — no row carried a "
                       "vendorSkuCode. Please upload a generated Myntra sheet."})

    store.finish(job.id, {"filled": xlsx, "origin": "upload",
                          "filename": file.filename, "products": len(rows)})
    resp = HTMLResponse("")
    resp.headers["HX-Redirect"] = f"/generate/attributes/{job.id}"
    return resp
```

Add the imports at the top of the file:

```python
import shutil

from src.web.jobs import store
```

Create `src/web/templates/_preview_error.html`:

```html
<div class="panel">
  <h3 class="flag">⚠ Couldn't read that file</h3>
  <p>{{ message }}</p>
</div>
```

Add `drop` to `JobStore` in `src/web/jobs.py` — this task needs it to discard the
job it just created, and Task 4's Clear reuses it:

```python
    def drop(self, job_id):
        """Forget a job entirely. Used by an upload that turned out unreadable and
        by the Preview screen's Clear."""
        with self._lock:
            return self._jobs.pop(job_id, None)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/web/test_preview.py -q`
Expected: PASS (all tests in the file, including the pre-existing nav and card tests)

- [ ] **Step 5: Commit**

```bash
git add src/web/routers/preview.py src/web/templates/_preview_error.html src/web/jobs.py tests/web/test_preview.py
git commit -m "feat(preview): adopt an uploaded workbook as an editable job"
```

---

### Task 2: Panel photo falls back to the sheet's own Front Image

**Files:**
- Modify: `src/web/routers/attributes.py:99-124` (`_panels`)
- Test: `tests/web/test_attributes.py`

**Interfaces:**
- Consumes: `result["filled"]` workbook rows from Task 1
- Produces: `_panel_image(product, attrs) -> str | None`, used by `_panels` and unchanged by later tasks

- [ ] **Step 1: Write the failing test**

Add to `tests/web/test_attributes.py`:

```python
def test_panel_photo_falls_back_to_the_sheet_front_image(tmp_path, monkeypatch):
    """An adopted upload has no Shopify export, so the only photo available is the
    URL already in the sheet. Without this every uploaded sheet shows 'no photo'."""
    warnings.filterwarnings("ignore")
    monkeypatch.setattr(gen, "RUNTIME", str(tmp_path / "runtime"))
    job = store.create()
    job_dir = os.path.join(gen.RUNTIME, job.id)
    os.makedirs(job_dir, exist_ok=True)
    t = read_template(V13)
    row = MappedRow(sku="S1", cells={
        "vendorSkuCode": "S1", "brand": "Ijor",
        "Front Image": "https://cdn.example/S1-front.jpg"})
    xlsx = os.path.join(job_dir, "myntra_filled.xlsx")
    fill_template(V13, t, [(row, ImageResult(sku="S1"))], xlsx)
    job.status = "done"
    job.result = {"filled": xlsx, "origin": "upload", "filename": "s.xlsx"}

    r = _client(tmp_path).get(f"/generate/attributes/{job.id}")
    assert r.status_code == 200
    assert "https://cdn.example/S1-front.jpg" in r.text


def test_panel_photo_ignores_a_non_url_front_image(tmp_path, monkeypatch):
    """fill.py falls back to bare local filenames when S3 is off. Rendering one as
    an <img src> would show a broken image; the placeholder is honest."""
    warnings.filterwarnings("ignore")
    monkeypatch.setattr(gen, "RUNTIME", str(tmp_path / "runtime"))
    job = store.create()
    job_dir = os.path.join(gen.RUNTIME, job.id)
    os.makedirs(job_dir, exist_ok=True)
    t = read_template(V13)
    row = MappedRow(sku="S1", cells={
        "vendorSkuCode": "S1", "brand": "Ijor", "Front Image": "1.jpg"})
    xlsx = os.path.join(job_dir, "myntra_filled.xlsx")
    fill_template(V13, t, [(row, ImageResult(sku="S1"))], xlsx)
    job.status = "done"
    job.result = {"filled": xlsx, "origin": "upload", "filename": "s.xlsx"}

    r = _client(tmp_path).get(f"/generate/attributes/{job.id}")
    assert "no photo" in r.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/web/test_attributes.py -q -k "front_image"`
Expected: FAIL — the first test fails because the panel renders "no photo"

- [ ] **Step 3: Write the implementation**

In `src/web/routers/attributes.py`, add above `_panels`:

```python
def _panel_image(product, attrs):
    """The panel photo: the Shopify export's first image when the job has an
    export, else the sheet's own Front Image.

    Only a real URL is used. fill.py falls back to bare local basenames when S3
    hosting is off, and rendering one as an <img src> would show a broken image
    rather than the honest 'no photo' placeholder."""
    if product and product.images:
        return product.images[0]
    front = str(attrs.get("Front Image") or "").strip()
    return front if front.startswith(("http://", "https://")) else None
```

Then in `_panels`, replace the `"image"` entry:

```python
            "image": _panel_image(p, attrs),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/web/test_attributes.py -q -k "front_image or photo"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/web/routers/attributes.py tests/web/test_attributes.py
git commit -m "feat(attributes): fall back to the sheet's Front Image for the panel photo"
```

---

### Task 3: Origin-aware screen chrome

**Files:**
- Modify: `src/web/routers/attributes.py:127-141` (`attributes_form`)
- Modify: `src/web/templates/attributes.html:3-9`
- Test: `tests/web/test_preview.py`

**Interfaces:**
- Consumes: `result["origin"]`, `result["filename"]` from Task 1
- Produces: template context keys `origin` (str) and `filename` (str), also used by Task 4's Clear button

- [ ] **Step 1: Write the failing test**

Add to `tests/web/test_preview.py`:

```python
def test_uploaded_session_names_the_file_being_edited(tmp_path, monkeypatch):
    """Two sheets look identical on screen. The owner must be able to tell which
    copy he is editing before he saves into it."""
    monkeypatch.setattr(gen, "RUNTIME", str(tmp_path / "runtime"))
    out = _filled(tmp_path)
    client = _client(tmp_path)
    with open(out, "rb") as fh:
        r = client.post("/preview", files={"file": ("august-batch.xlsx", fh.read(), XLSX)})
    page = client.get(r.headers["hx-redirect"]).text
    assert "Preview &amp; edit" in page or "Preview & edit" in page
    assert "august-batch.xlsx" in page
    assert "Fill attributes" not in page
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/web/test_preview.py -q -k "names_the_file"`
Expected: FAIL — page still says "Fill attributes"

- [ ] **Step 3: Write the implementation**

In `attributes_form`, add the two context keys:

```python
    return _templates().TemplateResponse(request, "attributes.html", {
        "user": user, "job_id": job.id, "columns": columns,
        "free_columns": free_columns,
        "free_hints": freetext_hints(free_columns),
        "vocab": attribute_vocab(template, columns),
        "panels": panels,
        "origin": job.result.get("origin", "generate"),
        "filename": job.result.get("filename", ""),
        "edited": job.result.get("edited", False),
        "hsn_gaps": _hsn_gaps_in(panels),
        "total": _total(columns, free_columns)})
```

`edited` is passed explicitly rather than left undefined: `_clear_button.html`
(Task 4) branches on it, and a template that silently depends on a missing name
breaks the moment someone adds strict-undefined mode.

In `src/web/templates/attributes.html`, replace lines 3-9 (the heading and hint):

```html
<div class="panel">
  {% if origin == "upload" %}
  <h2>Preview &amp; edit — <span class="mono">{{ filename }}</span></h2>
  <p class="hint">You are editing the app's copy of your uploaded file. Change
    anything below, then download when you're done — nothing is kept after the
    app restarts.</p>
  {% else %}
  <h2>Fill attributes</h2>
  <p class="hint">Myntra builds the public title and Design Details from these attributes.
    Pick them per product — the preview updates as you choose. Anything you leave on
    “— choose —” stays blank in the file and still has its Excel dropdown.</p>
  {% endif %}
  <p class="flag mono"><strong>⚠ Title &amp; Design Details are auto-generated by Myntra —
    the preview is our best reconstruction, not guaranteed word-for-word.</strong></p>
```

`filename` is user-controlled text; Jinja autoescape (already on) handles it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/web/test_preview.py tests/web/test_attributes.py -q`
Expected: PASS — the generate-origin tests in `test_attributes.py` confirm the old heading is unchanged

- [ ] **Step 5: Commit**

```bash
git add src/web/routers/attributes.py src/web/templates/attributes.html tests/web/test_preview.py
git commit -m "feat(attributes): name the uploaded file being edited"
```

---

### Task 4: Clear

**Files:**
- Modify: `src/web/jobs.py` (add `drop`, if not already added in Task 1)
- Modify: `src/web/routers/preview.py` (add the clear route)
- Modify: `src/web/templates/attributes.html` (the Clear button)
- Create: `src/web/templates/_preview_upload.html` (the bare upload form, extracted so Clear can swap it back)
- Modify: `src/web/templates/preview.html` (include the extracted form)
- Test: `tests/web/test_preview.py`

**Interfaces:**
- Consumes: `store.drop(job_id)`, `generate.RUNTIME`, `origin` from Task 3
- Produces: `POST /preview/clear/{job_id}` returning the empty upload form

- [ ] **Step 1: Write the failing tests**

```python
def test_clear_forgets_the_job_and_removes_its_directory(tmp_path, monkeypatch):
    """Clear is how the owner moves to the next file. A job left behind would keep
    the uploaded sheet on disk for the life of the process."""
    monkeypatch.setattr(gen, "RUNTIME", str(tmp_path / "runtime"))
    out = _filled(tmp_path)
    client = _client(tmp_path)
    with open(out, "rb") as fh:
        r = client.post("/preview", files={"file": ("s.xlsx", fh.read(), XLSX)})
    job_id = r.headers["hx-redirect"].rsplit("/", 1)[1]
    job_dir = os.path.join(gen.RUNTIME, job_id)
    assert os.path.isdir(job_dir)

    c = client.post(f"/preview/clear/{job_id}")
    assert c.status_code == 200
    assert store.get(job_id) is None
    assert not os.path.exists(job_dir)
    assert 'type="file"' in c.text          # the bare upload form came back


def test_clear_on_an_unknown_job_is_not_an_error(tmp_path, monkeypatch):
    """Double-click, or a Clear after a restart. Neither should show a 404 page."""
    monkeypatch.setattr(gen, "RUNTIME", str(tmp_path / "runtime"))
    r = _client(tmp_path).post("/preview/clear/" + "a" * 32)
    assert r.status_code == 200
    assert 'type="file"' in r.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/web/test_preview.py -q -k "clear"`
Expected: FAIL — 404, the route does not exist

- [ ] **Step 3: Write the implementation**

`src/web/jobs.py` — add to `JobStore` (skip if Task 1 already added it):

```python
    def drop(self, job_id):
        """Forget a job entirely. Used by an upload that turned out unreadable and
        by the Preview screen's Clear."""
        with self._lock:
            return self._jobs.pop(job_id, None)
```

Extract `src/web/templates/_preview_upload.html`:

```html
<form hx-post="/preview" hx-target="#preview-out" hx-swap="innerHTML"
      hx-encoding="multipart/form-data">
  <input type="file" name="file" accept=".xlsx" required>
  <button class="btn" type="submit">Open for preview &amp; edit →</button>
</form>
```

`src/web/templates/preview.html` becomes:

```html
{% extends "base.html" %}
{% block content %}
<div class="panel">
  <h2>Preview &amp; edit a filled sheet</h2>
  <p class="hint">Upload a Myntra sheet you have already generated or filled. You
    can check every listing and change any attribute, then download the corrected
    file.</p>
  <div id="preview-out">{% include "_preview_upload.html" %}</div>
</div>
{% endblock %}
```

Note the swap target now *contains* the form, so Clear can replace it with a fresh one.

`src/web/routers/preview.py` — add:

```python
@router.post("/preview/clear/{job_id}", response_class=HTMLResponse)
def preview_clear(request: Request, job_id: str):
    """Discard the uploaded copy and hand back an empty upload box.

    An unknown job is not an error: a double-click, or a Clear after a restart,
    should land on the same empty form rather than a 404 page."""
    get_user(request)
    from src.web.routers.generate import RUNTIME
    if re.fullmatch(r"[0-9a-f]{32}", job_id):
        store.drop(job_id)
        shutil.rmtree(os.path.join(RUNTIME, job_id), ignore_errors=True)
    return _templates().TemplateResponse(request, "_preview_upload.html", {})
```

Add `import re` at the top of `preview.py`.

In `src/web/templates/attributes.html`, inside the `{% if origin == "upload" %}` branch, add the button:

```html
  <p><a class="btn" href="/generate/download/{{ job_id }}">⬇ Download xlsx</a>
    <span id="clear-slot">{% include "_clear_button.html" %}</span></p>
```

Create `src/web/templates/_clear_button.html`:

```html
<button class="btn" type="button"
        hx-post="/preview/clear/{{ job_id }}"
        hx-target="body" hx-swap="innerHTML"
        {% if edited %}hx-confirm="You've saved edits to this sheet. Download it first, or discard them?"{% endif %}>
  ✕ Clear &amp; upload another
</button>
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/web/test_preview.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/web/jobs.py src/web/routers/preview.py src/web/templates/ tests/web/test_preview.py
git commit -m "feat(preview): clear the uploaded sheet and start again"
```

---

### Task 5: Clear asks once after a save

**Files:**
- Modify: `src/web/routers/attributes.py` (`_save_entries`, both save routes)
- Modify: `src/web/templates/_attr_saved.html`, `src/web/templates/_attr_panel_saved.html`
- Test: `tests/web/test_preview.py`

**Interfaces:**
- Consumes: `_clear_button.html` and its `edited` flag (Task 4), `result["origin"]` (Task 1)
- Produces: `job.result["edited"] = True` after any successful save on an upload-origin job

- [ ] **Step 1: Write the failing test**

```python
def test_clear_asks_for_confirmation_once_edits_are_saved(tmp_path, monkeypatch):
    """The server copy is the only copy of a save that hasn't been downloaded.
    Before the first save Clear stays instant — that is the common flow."""
    monkeypatch.setattr(gen, "RUNTIME", str(tmp_path / "runtime"))
    out = _filled(tmp_path)
    client = _client(tmp_path)
    with open(out, "rb") as fh:
        r = client.post("/preview", files={"file": ("s.xlsx", fh.read(), XLSX)})
    target = r.headers["hx-redirect"]
    job_id = target.rsplit("/", 1)[1]
    assert "hx-confirm" not in client.get(target).text

    saved = client.post(f"/generate/attributes/{job_id}/one",
                        data={"ordinal": "0", "sku__0": "S1", "attr__0__0": ""})
    assert saved.status_code == 200
    assert "hx-confirm" in saved.text
    assert 'id="clear-slot"' in saved.text
```

The SKU in `_filled` is `S1`; keep it in step with that helper.

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/web/test_preview.py -q -k "asks_for_confirmation"`
Expected: FAIL — no `hx-confirm` in the save response

- [ ] **Step 3: Write the implementation**

In `_save_entries`, immediately after the successful `write_attributes` block (inside the `with _WRITE_LOCK:` body, after the registry updates), mark the job:

```python
            # The server copy now differs from the file the owner uploaded, and he
            # may not have downloaded it. Clear asks before discarding it.
            if job.result.get("origin") == "upload":
                job.result["edited"] = True
```

Both save routes then pass the flag to their templates. In `attributes_save`:

```python
    return _templates().TemplateResponse(
        request, "_attr_saved.html",
        {"job_id": job.id, "saved": len(payload), "hsn_gaps": hsn_gaps,
         "origin": job.result.get("origin", "generate"),
         "edited": job.result.get("edited", False)})
```

In `attributes_save_one`, add the same three keys (`job_id`, `origin`, `edited`) to the success branch's context.

Append to **both** `_attr_saved.html` and `_attr_panel_saved.html`, at top level of the fragment (not nested — a nested `hx-swap-oob` is silently ignored):

```html
{% if origin == "upload" and not error %}
<span id="clear-slot" hx-swap-oob="true">{% include "_clear_button.html" %}</span>
{% endif %}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/web/test_preview.py tests/web/test_attributes.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/web/routers/attributes.py src/web/templates/ tests/web/test_preview.py
git commit -m "feat(preview): confirm before clearing unsaved-to-disk edits"
```

**Phase 1 checkpoint.** Start the app and confirm by hand: Preview → upload a filled sheet → panels appear with photos and pre-selected dropdowns → change one → Save this SKU → Download → open in Excel and confirm the value and the live dropdowns. Then Clear and upload another.

---

# Phase 2 — Image replacement

---

### Task 6: The replacement-image module (pure, no web, no S3)

**Files:**
- Create: `src/myntra/image_replace.py`
- Test: `tests/test_image_replace.py`

**Interfaces:**
- Consumes: `src.core.images.flatten_to_jpg`, `src.core.images.validate_image`, `src.myntra.fill.IMAGE_COLUMNS`
- Produces:
  - `replacement_key(sku: str, slot: int, data: bytes) -> str`
  - `prepare(sku: str, slot: int, data: bytes, specs: dict, out_dir: str) -> tuple[str | None, str | None, str | None]` returning `(local_path, key, reason)`
  - `load_specs(config_dir: str = "config/myntra") -> dict`
  - `class ImageConfigError(Exception)`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_image_replace.py`:

```python
import io
import os

from PIL import Image

from src.myntra.image_replace import prepare, replacement_key


def _png(w, h):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "red").save(buf, "PNG")
    return buf.getvalue()


def test_replacement_key_changes_with_the_photo():
    """The whole feature turns on this. The build path writes {sku}/{n}.jpg; re-using
    that key hands Myntra a URL identical to the one it already rejected, which it
    may never re-fetch. A different photo must produce a different URL."""
    a, b = _png(800, 800), _png(800, 801)
    assert replacement_key("S1", 1, a) != replacement_key("S1", 1, b)


def test_replacement_key_is_stable_for_the_same_photo():
    """Re-uploading the same file twice must not litter the bucket."""
    a = _png(800, 800)
    assert replacement_key("S1", 1, a) == replacement_key("S1", 1, a)
    assert replacement_key("S1", 1, a).startswith("S1/1-")
    assert replacement_key("S1", 1, a).endswith(".jpg")


def test_prepare_rejects_an_undersized_photo(tmp_path):
    """Myntra's floor is 700x700. Catching it here means one clear message instead
    of a whole-file rejection days later."""
    specs = {"min_width": 700, "min_height": 700, "max_bytes": 10485760, "quality": 90}
    path, key, reason = prepare("S1", 1, _png(500, 500), specs, str(tmp_path))
    assert path is None and key is None
    assert "500x500" in reason and "700x700" in reason


def test_prepare_converts_and_keeps_a_valid_photo(tmp_path):
    specs = {"min_width": 700, "min_height": 700, "max_bytes": 10485760, "quality": 90}
    path, key, reason = prepare("S1", 3, _png(800, 800), specs, str(tmp_path))
    assert reason is None
    assert key.startswith("S1/3-") and key.endswith(".jpg")
    assert os.path.exists(path)
    with Image.open(path) as im:
        assert im.format == "JPEG"


def test_prepare_reports_a_file_that_is_not_an_image(tmp_path):
    """A PDF or a .txt renamed to .jpg fails its own slot, never the request."""
    specs = {"min_width": 700, "min_height": 700, "max_bytes": 10485760, "quality": 90}
    path, key, reason = prepare("S1", 1, b"not an image", specs, str(tmp_path))
    assert path is None
    assert "convert error" in reason
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_image_replace.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.myntra.image_replace'`

- [ ] **Step 3: Write the implementation**

Create `src/myntra/image_replace.py`:

```python
"""Replace a product's images from files the owner supplies, after Myntra rejects
a photo.

Separate from core/images.py because the source differs: that module fetches a
product's images from Shopify URLs during a build. This one takes bytes uploaded
in the browser. What the two share — flattening to JPG and validating against the
Myntra specs — is imported, not duplicated."""
import hashlib
import io
import os

import yaml
from PIL import Image

from src.core.images import flatten_to_jpg, validate_image


class ImageConfigError(Exception):
    """Image hosting is not configured, so no public URL can be produced."""


def load_specs(config_dir="config/myntra"):
    with open(os.path.join(config_dir, "image_specs.yaml"), encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def replacement_key(sku, slot, data):
    """S3 key tail for a replacement image: {sku}/{slot}-{hash}.jpg.

    Hashing the file's own bytes is what makes replacement work at all. The build
    path writes {sku}/{slot}.jpg; writing a replacement there would overwrite the
    object and leave Myntra with a URL byte-identical to the one it already
    rejected — which it may never re-fetch, so the new photo would never be seen.
    A different photo therefore yields a different URL, while re-uploading the
    same file twice stays idempotent instead of littering the bucket."""
    digest = hashlib.sha256(data).hexdigest()[:8]
    return f"{sku}/{slot}-{digest}.jpg"


def prepare(sku, slot, data, specs, out_dir):
    """Convert one uploaded file to a validated JPG on disk.

    Returns (local_path, key, None) on success, or (None, None, reason) when the
    photo cannot be used. Never raises on bad input: a corrupt upload is a
    per-slot message the owner can act on, not a failed request that loses the
    other slots he supplied in the same click."""
    key = replacement_key(sku, slot, data)
    out_path = os.path.join(out_dir, key.replace("/", os.sep))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    try:
        with Image.open(io.BytesIO(data)) as im:
            flatten_to_jpg(im, specs.get("quality", 90), out_path)
    except Exception as exc:
        return None, None, f"convert error: {exc}"
    reason = validate_image(out_path, specs)
    if reason:
        return None, None, reason
    return out_path, key, None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_image_replace.py -q`
Expected: PASS (5 tests, fast — no workbook is loaded)

- [ ] **Step 5: Commit**

```bash
git add src/myntra/image_replace.py tests/test_image_replace.py
git commit -m "feat(images): content-addressed replacement keys and per-slot validation"
```

---

### Task 7: Hosting the replacement, with a hard config guard

**Files:**
- Modify: `src/myntra/image_replace.py` (add `host`)
- Test: `tests/test_image_replace.py`

**Interfaces:**
- Consumes: `src.core.s3_upload.upload_images`, `prepare`'s `(local_path, key)` pairs
- Produces: `host(prepared: list[tuple[str, str]], specs: dict, out_dir: str, client=None) -> list[str]`

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from src.myntra.image_replace import ImageConfigError, host


class _FakeS3:
    def __init__(self):
        self.calls = []

    def upload_file(self, path, bucket, key, ExtraArgs=None):
        self.calls.append((path, bucket, key, ExtraArgs))


def test_host_returns_public_urls_matching_the_uploaded_keys(tmp_path):
    specs = {"public_base_url": "https://cdn.example/myntra", "s3_bucket": "b",
             "s3_prefix": "myntra", "s3_upload": True}
    p = tmp_path / "S1" / "1-abcd1234.jpg"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"x")
    client = _FakeS3()
    urls = host([(str(p), "S1/1-abcd1234.jpg")], specs, str(tmp_path), client=client)
    assert urls == ["https://cdn.example/myntra/S1/1-abcd1234.jpg"]
    assert client.calls[0][1:3] == ("b", "myntra/S1/1-abcd1234.jpg")


def test_host_refuses_when_hosting_is_not_configured(tmp_path):
    """Without a public base URL there is no URL to write. Writing a local path into
    a column Myntra reads as a URL fails at upload with a message pointing nowhere
    near here, so fail loudly and early instead."""
    with pytest.raises(ImageConfigError):
        host([("/tmp/x.jpg", "S1/1-a.jpg")],
             {"public_base_url": "", "s3_bucket": "b", "s3_upload": True},
             str(tmp_path), client=_FakeS3())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_image_replace.py -q -k "host"`
Expected: FAIL — `ImportError: cannot import name 'host'`

- [ ] **Step 3: Write the implementation**

Append to `src/myntra/image_replace.py`:

```python
def host(prepared, specs, out_dir, client=None):
    """Upload prepared JPGs and return their public URLs, in the order given.

    `prepared` is [(local_path, key)] from prepare(). The S3 key is derived by
    upload_images from each path relative to out_dir, which is exactly `key` —
    the same mirroring the build path relies on, so the URL written into the sheet
    always matches the object that was uploaded."""
    base = (specs.get("public_base_url") or "").rstrip("/")
    bucket = specs.get("s3_bucket")
    if not base or not bucket or not specs.get("s3_upload"):
        raise ImageConfigError(
            "Image hosting is not configured — set public_base_url, s3_bucket and "
            "s3_upload in config/myntra/image_specs.yaml. Without it there is no "
            "public URL for Myntra to fetch the new photo from.")
    from src.core.s3_upload import upload_images
    upload_images([p for p, _ in prepared], bucket, specs.get("s3_prefix", ""),
                  base_dir=out_dir, region=specs.get("s3_region"), client=client)
    return [f"{base}/{key}" for _, key in prepared]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_image_replace.py -q`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/myntra/image_replace.py tests/test_image_replace.py
git commit -m "feat(images): host replacement images and refuse when S3 is unconfigured"
```

---

### Task 8: The image slots in each panel

**Files:**
- Modify: `src/web/routers/attributes.py` (`_panels` — add `image_slots`)
- Create: `src/web/templates/_attr_images.html`
- Modify: `src/web/templates/_attr_panel.html` (include it in `.attr-footer`)
- Modify: `src/web/static/app.css` (thumbnail sizing)
- Test: `tests/web/test_attributes.py`

**Interfaces:**
- Consumes: `IMAGE_COLUMNS` from `src.myntra.fill`, `_panel_image`'s URL test from Task 2
- Produces: panel key `image_slots: [{"header": str, "url": str | None, "slot": int}]`; form field names `img__{ordinal}__{slot}` consumed by Task 9

- [ ] **Step 1: Write the failing test**

```python
def test_panel_offers_a_file_input_for_every_myntra_image_slot(tmp_path, monkeypatch):
    """Myntra rejects one specific shot ('front image is pixelated'), so each slot
    needs its own picker — replacing all seven is the same path, just more files."""
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    r = _client(tmp_path).get(f"/generate/attributes/{job.id}")
    assert r.status_code == 200
    for slot in range(1, 8):
        assert f'name="img__0__{slot}"' in r.text
    assert "Front Image" in r.text
    assert "Additional Image 2" in r.text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/web/test_attributes.py -q -k "image_slot"`
Expected: FAIL — no `img__0__1` in the page

- [ ] **Step 3: Write the implementation**

In `src/web/routers/attributes.py`, import the column list:

```python
from src.myntra.fill import IMAGE_COLUMNS
```

Add above `_panels`:

```python
def _image_slots(attrs):
    """One entry per Myntra image column, numbered from 1 to match the {slot} in
    the replacement key. Only real URLs become thumbnails, for the same reason as
    _panel_image."""
    slots = []
    for slot, header in enumerate(IMAGE_COLUMNS, start=1):
        url = str(attrs.get(header) or "").strip()
        slots.append({"header": header, "slot": slot,
                      "url": url if url.startswith(("http://", "https://")) else None})
    return slots
```

Add to the panel dict built in `_panels`:

```python
            "image_slots": _image_slots(attrs),
```

Create `src/web/templates/_attr_images.html`:

```html
<details class="attr-images">
  <summary>Product images — replace</summary>
  <p class="hint">Choose a file for any slot you want to change. Slots you leave
    empty are untouched. Minimum 700×700, max 10 MB.</p>
  {% for s in p.image_slots %}
  <label class="hint attr-slot">
    <span class="mono">{{ s.header }}</span>
    {% if s.url %}<img class="slot-thumb" src="{{ s.url }}" alt="{{ s.header }}" loading="lazy">
    {% else %}<span class="hint">empty</span>{% endif %}
    <input type="file" name="img__{{ p.ordinal }}__{{ s.slot }}" accept="image/*">
  </label>
  {% endfor %}
  <button class="btn" type="button"
          hx-post="/generate/attributes/{{ job_id }}/images"
          hx-encoding="multipart/form-data"
          hx-include="closest .attr-panel"
          hx-vals='{"ordinal": {{ p.ordinal }}}'
          hx-target="#img-out-{{ p.ordinal }}"
          hx-swap="innerHTML"
          hx-disabled-elt="this">Upload new images</button>
  <span class="attr-save-out" id="img-out-{{ p.ordinal }}"></span>
</details>
```

In `src/web/templates/_attr_panel.html`, inside `.attr-footer`, immediately before the "Save this SKU" button:

```html
    {% include "_attr_images.html" %}
```

In `src/web/static/app.css`:

```css
.attr-slot { display: flex; align-items: center; gap: .6rem; margin: .3rem 0; }
.attr-slot .mono { min-width: 11rem; }
.slot-thumb { width: 48px; height: 48px; object-fit: cover; border-radius: 4px; }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/web/test_attributes.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/web/routers/attributes.py src/web/templates/ src/web/static/app.css tests/web/test_attributes.py
git commit -m "feat(images): per-slot replacement pickers in each attribute panel"
```

---

### Task 9: Saving replacement images into the workbook

**Files:**
- Modify: `src/web/routers/attributes.py` (new route)
- Create: `src/web/templates/_attr_images_saved.html`
- Test: `tests/web/test_attributes.py`

**Interfaces:**
- Consumes: `prepare`, `host`, `load_specs`, `ImageConfigError` (Tasks 6-7); form fields `img__{ordinal}__{slot}` (Task 8); `write_attributes`, `_WRITE_LOCK`, `_requested_ordinal`
- Produces: `POST /generate/attributes/{job_id}/images`

- [ ] **Step 1: Write the failing tests**

```python
import io

from PIL import Image

import src.web.routers.attributes as attrs_router


def _png_bytes(w, h):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "blue").save(buf, "PNG")
    return buf.getvalue()


def test_replacing_an_image_writes_its_url_into_the_sheet(tmp_path, monkeypatch):
    """The point of the feature: a new photo must reach the workbook as a URL
    Myntra can fetch."""
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    monkeypatch.setattr(attrs_router, "load_specs", lambda: {
        "min_width": 700, "min_height": 700, "max_bytes": 10485760, "quality": 90,
        "public_base_url": "https://cdn.example/myntra", "s3_bucket": "b",
        "s3_prefix": "myntra", "s3_upload": True})
    monkeypatch.setattr(attrs_router, "host",
                        lambda prepared, specs, out_dir: [
                            f"https://cdn.example/myntra/{k}" for _, k in prepared])

    r = _client(tmp_path).post(
        f"/generate/attributes/{job.id}/images",
        data={"ordinal": "0", "sku__0": "S1"},
        files={"img__0__1": ("new.png", _png_bytes(800, 800), "image/png")})
    assert r.status_code == 200
    assert "Front Image" in r.text

    from src.myntra.preview import read_filled_rows
    from src.myntra.template_reader import read_template
    rows = read_filled_rows(job.result["filled"], read_template(V13))
    assert rows[0]["Front Image"].startswith("https://cdn.example/myntra/S1/1-")


def test_an_undersized_replacement_fails_only_its_own_slot(tmp_path, monkeypatch):
    """A bad photo in one slot must not discard the good photo supplied alongside it."""
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    monkeypatch.setattr(attrs_router, "load_specs", lambda: {
        "min_width": 700, "min_height": 700, "max_bytes": 10485760, "quality": 90,
        "public_base_url": "https://cdn.example/myntra", "s3_bucket": "b",
        "s3_prefix": "myntra", "s3_upload": True})
    monkeypatch.setattr(attrs_router, "host",
                        lambda prepared, specs, out_dir: [
                            f"https://cdn.example/myntra/{k}" for _, k in prepared])

    r = _client(tmp_path).post(
        f"/generate/attributes/{job.id}/images",
        data={"ordinal": "0", "sku__0": "S1"},
        files={"img__0__1": ("small.png", _png_bytes(300, 300), "image/png"),
               "img__0__2": ("good.png", _png_bytes(800, 800), "image/png")})
    assert "300x300" in r.text
    from src.myntra.preview import read_filled_rows
    from src.myntra.template_reader import read_template
    rows = read_filled_rows(job.result["filled"], read_template(V13))
    assert rows[0]["Side Image"].startswith("https://cdn.example/myntra/S1/2-")


def test_unconfigured_hosting_reports_instead_of_writing_a_local_path(tmp_path, monkeypatch):
    job = _job(tmp_path, monkeypatch, skus=("S1",))
    monkeypatch.setattr(attrs_router, "load_specs", lambda: {
        "min_width": 700, "min_height": 700, "max_bytes": 10485760, "quality": 90,
        "public_base_url": "", "s3_bucket": "", "s3_upload": False})
    r = _client(tmp_path).post(
        f"/generate/attributes/{job.id}/images",
        data={"ordinal": "0", "sku__0": "S1"},
        files={"img__0__1": ("new.png", _png_bytes(800, 800), "image/png")})
    assert r.status_code == 200
    assert "not configured" in r.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/web/test_attributes.py -q -k "replacing or undersized or unconfigured"`
Expected: FAIL — 404, the route does not exist

- [ ] **Step 3: Write the implementation**

Import at the top of `src/web/routers/attributes.py`:

```python
from src.myntra.image_replace import ImageConfigError, host, load_specs, prepare
```

Add the route:

```python
@router.post("/generate/attributes/{job_id}/images", response_class=HTMLResponse)
async def attributes_save_images(request: Request, job_id: str):
    """Replace one panel's product images from uploaded files.

    Outcomes are reported per slot: a photo Myntra would reject fails its own slot
    and the slots supplied alongside it still land, so one bad file in a batch of
    seven does not throw the other six away."""
    get_user(request)
    job, job_dir, xlsx, _csv = job_files(job_id)
    form = await request.form()
    ordinal = _requested_ordinal(form)
    if ordinal is None:
        return _templates().TemplateResponse(
            request, "_attr_images_saved.html",
            {"error": "Nothing to upload — please reload the screen and try again."})
    sku = str(form.get(f"sku__{ordinal}") or "").strip()
    specs = load_specs()
    out_dir = os.path.join(job_dir, "replacements")

    failed, prepared = [], []
    for slot, header in enumerate(IMAGE_COLUMNS, start=1):
        upload = form.get(f"img__{ordinal}__{slot}")
        if not hasattr(upload, "read"):          # slot left empty
            continue
        data = await upload.read()
        if not data:
            continue
        path, key, reason = prepare(sku, slot, data, specs, out_dir)
        if reason:
            failed.append({"header": header, "reason": reason})
        else:
            prepared.append((path, key, header))

    if not prepared and not failed:
        return _templates().TemplateResponse(
            request, "_attr_images_saved.html",
            {"error": "No image files were chosen."})

    saved = []
    if prepared:
        try:
            urls = host([(p, k) for p, k, _ in prepared], specs, out_dir)
        except ImageConfigError as exc:
            return _templates().TemplateResponse(
                request, "_attr_images_saved.html", {"error": str(exc)})
        values = {h: url for (_p, _k, h), url in zip(prepared, urls)}
        # write_attributes, not a bare openpyxl save: it verifies the row still
        # holds this SKU and re-applies shared_to_inline, which Myntra requires.
        with _WRITE_LOCK:
            write_attributes(xlsx, read_template(TEMPLATE),
                             [{"ordinal": ordinal, "sku": sku, "values": values}])
            if job.result.get("origin") == "upload":
                job.result["edited"] = True
        saved = [{"header": h, "url": u} for (_p, _k, h), u in zip(prepared, urls)]

    return _templates().TemplateResponse(
        request, "_attr_images_saved.html",
        {"saved": saved, "failed": failed,
         "origin": job.result.get("origin", "generate"),
         "edited": job.result.get("edited", False), "job_id": job.id})
```

Create `src/web/templates/_attr_images_saved.html`:

```html
{% if error %}
<p class="flag mono">⚠ {{ error }}</p>
{% else %}
{% if saved %}
<p class="ok mono">✅ Replaced: {% for s in saved %}{{ s.header }}{% if not loop.last %}, {% endif %}{% endfor %}</p>
{% endif %}
{% for f in failed %}
<p class="flag mono">⚠ {{ f.header }} — {{ f.reason }}</p>
{% endfor %}
{% endif %}
{% if origin == "upload" and not error %}
<span id="clear-slot" hx-swap-oob="true">{% include "_clear_button.html" %}</span>
{% endif %}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/web/test_attributes.py tests/test_image_replace.py -q`
Expected: PASS

- [ ] **Step 5: Verify the Myntra inline-strings guarantee still holds**

Run: `python -m pytest tests/test_inline_strings.py -q`
Expected: PASS — `test_write_attributes_keeps_strings_inline` confirms no `t="s"` survives an image write

- [ ] **Step 6: Commit**

```bash
git add src/web/routers/attributes.py src/web/templates/_attr_images_saved.html tests/web/test_attributes.py
git commit -m "feat(images): save replacement image URLs into the workbook"
```

**Phase 2 checkpoint.** With real AWS credentials, replace one image on a real sheet, download it, and confirm in Excel that the Front Image URL changed and carries the hash suffix. Open the URL in a browser to confirm the object is public.

---

# Phase 3 — Fix-errors entry point and docs

---

### Task 10: "Replace images" from a fix result

**Files:**
- Modify: `src/web/routers/fix.py:236-257` (add `category` to `manual_needed`)
- Modify: `src/web/templates/_fix_result.html`
- Modify: `src/web/routers/preview.py` (adopt the corrected file)
- Test: `tests/web/test_fix.py`

**Interfaces:**
- Consumes: `_fix_dir(fix_id)/myntra_corrected.xlsx`, the adoption mechanism from Task 1
- Produces: `POST /preview/adopt-fix/{fix_id}` → `HX-Redirect` to the attributes screen

- [ ] **Step 1: Write the failing test**

```python
def test_image_rejections_offer_the_replacement_screen(tmp_path, monkeypatch):
    """error_rules.yaml diagnoses 'pixelated' correctly and then dead-ends. The fix
    now sits next to the diagnosis."""
    summary = {"written": 1, "file": None, "fixed": [], "could_not_rebuild": [],
               "dropped": [], "rejected": {}, "changed": {},
               "manual_needed": [{"sku": "S1", "category": "image",
                                  "explanation": "The image resolution is too low."}]}
    from src.web.main import templates
    html = templates.get_template("_fix_result.html").render(
        summary=summary, fix_id="a" * 32, request=None)
    assert "/preview/adopt-fix/" in html
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/web/test_fix.py -q -k "image_rejections"`
Expected: FAIL — no adopt-fix link in the rendered result

- [ ] **Step 3: Write the implementation**

In `src/web/routers/fix.py`, both places that build `manual_needed` (the early-return
at ~line 239 and the fallback at ~line 255) gain the category, so the template can
tell an image rejection from any other manual fix:

```python
                           "manual_needed": [{"sku": i.sku, "category": i.category,
                                              "explanation": i.explanation}
                                             for i in issues if i.action == "explain_only"]}
```

```python
        summary["manual_needed"] = summary.get("manual_needed") or [
            {"sku": i.sku, "category": i.category, "explanation": i.explanation}
            for i in issues if i.action == "explain_only"]
```

In `src/web/templates/_fix_result.html`, after the `manual_needed` list:

```html
    {% set image_skus = summary.manual_needed | selectattr("category", "equalto", "image") | list %}
    {% if image_skus %}
    <button class="btn" type="button"
            hx-post="/preview/adopt-fix/{{ fix_id }}"
            hx-swap="none">🖼 Replace images for {{ image_skus | length }} SKU(s) →</button>
    <p class="hint">Opens the corrected sheet so you can upload new photos for the
      rejected products, then download it again.</p>
    {% endif %}
```

In `src/web/routers/preview.py`:

```python
@router.post("/preview/adopt-fix/{fix_id}", response_class=HTMLResponse)
def preview_adopt_fix(request: Request, fix_id: str):
    """Open a fix run's corrected workbook in the editable screen.

    The corrected file only exists after apply, which is why this hangs off the
    fix *result* rather than the error listing."""
    get_user(request)
    from src.web.routers.fix import _fix_dir, _safe_fix_id
    from src.web.routers.generate import RUNTIME
    src_path = os.path.join(_fix_dir(_safe_fix_id(fix_id)), "myntra_corrected.xlsx")
    if not os.path.exists(src_path):
        raise HTTPException(status_code=404, detail="not ready")
    job = store.create()
    job_dir = os.path.join(RUNTIME, job.id)
    os.makedirs(job_dir, exist_ok=True)
    xlsx = os.path.join(job_dir, "myntra_filled.xlsx")
    shutil.copyfile(src_path, xlsx)
    store.finish(job.id, {"filled": xlsx, "origin": "upload",
                          "filename": "myntra_corrected.xlsx"})
    resp = HTMLResponse("")
    resp.headers["HX-Redirect"] = f"/generate/attributes/{job.id}"
    return resp
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/web/test_fix.py tests/web/test_preview.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/web/routers/fix.py src/web/routers/preview.py src/web/templates/_fix_result.html tests/web/test_fix.py
git commit -m "feat(fix): open the corrected sheet to replace rejected images"
```

---

### Task 11: Documentation

**Files:**
- Modify: `docs/APP-FEATURES-GUIDE.md` (Screen 3)
- Modify: `docs/ARCHITECTURE.md` (the adoption mechanism + `image_replace` module)
- Modify: `AGENTS.md` (flow list)
- Create: `docs/journal/2026-08-17.md`

- [ ] **Step 1: Rewrite Screen 3 in `docs/APP-FEATURES-GUIDE.md`**

Cover, in plain English: Preview is now upload-and-edit, not read-only; every attribute
can be changed and saved into your copy; images can be replaced per slot; Clear starts
the next file; nothing survives a restart, so download before you leave. Include the
hazard from spec §10.2 verbatim:

> If you change a SKU by hand in Excel, change it in **all three** columns —
> `vendorSkuCode`, `SKUCode` and `vendorArticleNumber` — or Myntra receives an
> inconsistent identity for that product.

- [ ] **Step 2: Update `docs/ARCHITECTURE.md`**

Add a row for `src/myntra/image_replace.py`, and describe adoption in one paragraph:
an uploaded or corrected workbook is registered in the job store as a finished job, so
every Fill-attributes surface operates on it unchanged. Note that `result["origin"]`
distinguishes an adopted job from a generated one.

- [ ] **Step 3: Update the flow list in `AGENTS.md`**

- [ ] **Step 4: Write `docs/journal/2026-08-17.md`**

Record: what shipped, the content-addressed key and why it exists, and that SKU editing
was deliberately left out.

- [ ] **Step 5: Commit**

```bash
git add docs/ AGENTS.md
git commit -m "docs: editable preview and image replacement"
```

---

## Verification before calling this done

- [ ] `python -m pytest tests/web/test_preview.py tests/web/test_attributes.py tests/web/test_fix.py tests/test_image_replace.py -q` — all pass
- [ ] `python -m pytest tests/test_inline_strings.py -q` — the Myntra inline-strings guarantee still holds
- [ ] Manual: upload a filled sheet, change an attribute, replace an image, download, open in Excel — the value changed, the image URL changed and carries a hash suffix, and the dropdowns are still live
- [ ] Report honestly which of the above were actually run
