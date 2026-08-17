import os
import re
import shutil

from fastapi import APIRouter, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse

from src.myntra.template_reader import read_template
from src.myntra.preview import read_filled_rows
from src.myntra.pipeline import DEFAULT_TEMPLATE_NAME
from src.web.routers.pages import get_user
from src.web.jobs import store

router = APIRouter()
TEMPLATE = os.path.join("templates", "myntra", DEFAULT_TEMPLATE_NAME)


def _templates():
    from src.web.main import templates
    return templates


@router.get("/preview", response_class=HTMLResponse)
def preview_form(request: Request):
    user = get_user(request)
    return _templates().TemplateResponse(request, "preview.html", {"user": user})


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
