import os
import re
import shutil
import tempfile

from fastapi import APIRouter, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse

from src.myntra.corrector import regenerate_surface_b
from src.myntra.template_reader import read_template
from src.myntra.preview import read_filled_rows
from src.myntra.pipeline import DEFAULT_TEMPLATE_NAME
from src.web.routers.pages import get_user, get_settings
from src.web.jobs import store

router = APIRouter()
TEMPLATE = os.path.join("templates", "myntra", DEFAULT_TEMPLATE_NAME)


UNREADABLE = ("That doesn't look like a Myntra listing sheet. Please upload the "
              ".xlsx this app generated — not a Shopify export, a rejection "
              "report, or an older template.")
NO_PRODUCTS = ("That file has no products in it — no row carried a vendorSkuCode. "
               "Please upload a generated Myntra sheet.")
NO_IMAGE_REJECTIONS = ("Nothing in that rejection file was turned down for its "
                       "photos, so there is nothing to replace here.")
REBUILD_EMPTY = ("We couldn't rebuild any of those SKUs. The Shopify products "
                 "export you uploaded doesn't appear to contain them — re-export "
                 "those SKUs from Shopify and try again.")


def _templates():
    from src.web.main import templates
    return templates


def _read_error(request, message):
    return _templates().TemplateResponse(request, "_preview_error.html",
                                         {"message": message})


def _rows_or_error(request, path):
    """(rows, None) if `path` is a readable Myntra sheet with products in it,
    else (None, response). Every caller must check the file BEFORE adopting it:
    a wrong file is an ordinary mistake, and an uncaught raise here becomes a 500
    that htmx silently drops, leaving the owner staring at a screen that did
    nothing."""
    try:
        rows = read_filled_rows(path, read_template(TEMPLATE))
    except Exception:  # noqa: BLE001 - any unreadable file is the same user mistake
        return None, _read_error(request, UNREADABLE)
    if not rows:
        return None, _read_error(request, NO_PRODUCTS)
    return rows, None


def _adopt(src_path, filename, products, move=False):
    """Register `src_path` as a finished upload-origin job and return its id. This
    is the whole adoption mechanism the Fill-attributes screen needs; nothing
    downstream cares whether the workbook was uploaded, built or rebuilt."""
    from src.web.routers.generate import RUNTIME
    job = store.create()
    job_dir = os.path.join(RUNTIME, job.id)
    os.makedirs(job_dir, exist_ok=True)
    xlsx = os.path.join(job_dir, "myntra_filled.xlsx")
    (shutil.move if move else shutil.copyfile)(src_path, xlsx)
    store.finish(job.id, {"filled": xlsx, "origin": "upload",
                          "filename": filename, "products": products})
    return job.id


def _to_attributes(job_id):
    resp = HTMLResponse("")
    resp.headers["HX-Redirect"] = f"/generate/attributes/{job_id}"
    return resp


@router.get("/preview", response_class=HTMLResponse)
def preview_form(request: Request):
    user = get_user(request)
    return _templates().TemplateResponse(request, "preview.html", {"user": user})


@router.post("/preview", response_class=HTMLResponse)
async def preview_submit(request: Request, file: UploadFile = File(...)):
    """Adopt the uploaded workbook as a job, so the Fill-attributes screen can edit
    it. The job store is the only thing that screen needs; nothing downstream cares
    that this workbook was uploaded rather than built.

    The sheet is checked in a staging directory BEFORE a job exists. Creating the
    job first would mean any parse failure — a renamed CSV, last year's template —
    orphaned both the job and its copy of the file for the life of the process,
    while htmx showed the owner nothing at all."""
    get_user(request)
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Please upload the filled .xlsx file")
    from src.web.routers.generate import RUNTIME
    os.makedirs(RUNTIME, exist_ok=True)
    staging = tempfile.mkdtemp(prefix="staged-", dir=RUNTIME)
    try:
        staged = os.path.join(staging, "myntra_filled.xlsx")
        with open(staged, "wb") as out:
            shutil.copyfileobj(file.file, out)
        rows, error = _rows_or_error(request, staged)
        if error is not None:
            return error

        job_id = _adopt(staged, file.filename, len(rows), move=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return _to_attributes(job_id)


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
    resp = HTMLResponse("")
    resp.headers["HX-Redirect"] = "/preview"
    return resp


@router.post("/preview/adopt-fix/{fix_id}", response_class=HTMLResponse)
def preview_adopt_fix(request: Request, fix_id: str):
    """Open the photo-rejected SKUs in the editable screen.

    NOT the fix run's corrected workbook. That file holds the SKUs the app could
    correct by itself, and an image rejection is explain_only by definition — so
    the corrected sheet excludes exactly the products this button names. The sheet
    is rebuilt here instead, from the persisted issues plus the Shopify export, so
    it carries the rejected SKUs and nothing else."""
    get_user(request)
    from src.web.routers import fix as fixmod
    fix_dir = fixmod._fix_dir(fixmod._safe_fix_id(fix_id))
    _, issues = fixmod._load_issues(fix_dir)  # 404 if the session is gone
    skus = sorted({i.sku for i in issues
                   if i.sku and i.action == "explain_only" and i.category == "image"})
    if not skus:
        return _read_error(request, NO_IMAGE_REJECTIONS)

    # Rebuilding re-runs the listing pipeline, which needs the export the SKUs came
    # from. The per-SKU xlsx path never asks for one, so this is a routine miss, not
    # a failure: point the owner at the upload box the Fix screen already shows.
    csv_path = os.path.join(fix_dir, "products_export.csv")
    if not os.path.exists(csv_path):
        return fixmod._export_prompt_panel()
    out_dir = os.path.join(fix_dir, "replace-images")
    os.makedirs(out_dir, exist_ok=True)
    try:
        summary = regenerate_surface_b(skus, get_settings(request), out_dir,
                                       csv_path=csv_path)
    except Exception as exc:  # noqa: BLE001 - htmx drops a 5xx, so the button would look dead
        return fixmod._error_panel(exc)

    built, missing = summary.get("file"), summary.get("could_not_rebuild") or []
    rows, error, job_id = None, None, None
    if built and os.path.exists(built):
        rows, error = _rows_or_error(request, built)
        if rows:
            job_id = _adopt(built, "myntra_rejected_images.xlsx", len(rows))
    if missing:
        # A SKU absent from the export would otherwise vanish from the sheet in
        # silence — the same "the file excludes what the button promised" failure
        # this route exists to fix. Name them, and only then offer the rest.
        return _templates().TemplateResponse(request, "_adopt_fix_partial.html",
                                             {"missing": missing, "job_id": job_id,
                                              "rebuilt": len(rows or [])})
    if error is not None:
        return error
    if job_id is None:
        return _read_error(request, REBUILD_EMPTY)
    return _to_attributes(job_id)
