import os
import re
import shutil
import tempfile

from fastapi import APIRouter, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse

from src.myntra.template_reader import read_template
from src.myntra.preview import read_filled_rows
from src.myntra.pipeline import DEFAULT_TEMPLATE_NAME
from src.web.routers.pages import get_user
from src.web.jobs import store

router = APIRouter()
TEMPLATE = os.path.join("templates", "myntra", DEFAULT_TEMPLATE_NAME)


UNREADABLE = ("That doesn't look like a Myntra listing sheet. Please upload the "
              ".xlsx this app generated — not a Shopify export, a rejection "
              "report, or an older template.")
NO_PRODUCTS = ("That file has no products in it — no row carried a vendorSkuCode. "
               "Please upload a generated Myntra sheet.")


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

        job = store.create()
        job_dir = os.path.join(RUNTIME, job.id)
        os.makedirs(job_dir, exist_ok=True)
        xlsx = os.path.join(job_dir, "myntra_filled.xlsx")
        shutil.move(staged, xlsx)
        store.finish(job.id, {"filled": xlsx, "origin": "upload",
                              "filename": file.filename, "products": len(rows)})
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    resp = HTMLResponse("")
    resp.headers["HX-Redirect"] = f"/generate/attributes/{job.id}"
    return resp


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
    """Open a fix run's corrected workbook in the editable screen.

    The corrected file only exists after apply, which is why this hangs off the
    fix *result* rather than the error listing."""
    get_user(request)
    from src.web.routers.fix import _fix_dir, _safe_fix_id
    from src.web.routers.generate import RUNTIME
    src_path = os.path.join(_fix_dir(_safe_fix_id(fix_id)), "myntra_corrected.xlsx")
    if not os.path.exists(src_path):
        raise HTTPException(status_code=404, detail="not ready")
    # Same check the upload path runs. Arriving from the Fix screen is no reason
    # to land on an empty accordion with no explanation.
    rows, error = _rows_or_error(request, src_path)
    if error is not None:
        return error
    job = store.create()
    job_dir = os.path.join(RUNTIME, job.id)
    os.makedirs(job_dir, exist_ok=True)
    xlsx = os.path.join(job_dir, "myntra_filled.xlsx")
    shutil.copyfile(src_path, xlsx)
    store.finish(job.id, {"filled": xlsx, "origin": "upload",
                          "filename": "myntra_corrected.xlsx",
                          "products": len(rows)})
    resp = HTMLResponse("")
    resp.headers["HX-Redirect"] = f"/generate/attributes/{job.id}"
    return resp
