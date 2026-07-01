import csv as csvmod
import os
import shutil

from fastapi import APIRouter, Request, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from src.myntra.groupid_ledger import reserve, confirm
from src.myntra.pipeline import main as pipeline_main  # noqa: F401 (patched in tests)
from src.web.jobs import store
from src.web.routers.pages import get_user, get_settings
from src.web.settings import ledger_store

router = APIRouter()
RUNTIME = os.path.join(os.path.dirname(os.path.dirname(__file__)), "runtime")


def count_products(path):
    """Number of distinct Shopify products = rows with a non-empty Handle (header excluded)."""
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csvmod.DictReader(fh)
        handles = {r.get("Handle") for r in reader if r.get("Handle")}
    return len(handles) or 1


def _templates():
    from src.web.main import templates
    return templates


@router.get("/generate", response_class=HTMLResponse)
def generate_form(request: Request):
    get_user(request)
    settings = get_settings(request)
    from src.myntra.groupid_ledger import read_ledger
    next_id = read_ledger(ledger_store(settings))["next_style_group_id"]
    return _templates().TemplateResponse(
        request, "generate.html", {"user": get_user(request), "next_id": next_id})


@router.post("/generate", response_class=HTMLResponse)
def generate_submit(request: Request, file: UploadFile = File(...)):
    get_user(request)
    settings = get_settings(request)
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a .csv file")

    job = store.create()
    job_dir = os.path.join(RUNTIME, job.id)
    os.makedirs(job_dir, exist_ok=True)
    csv_path = os.path.join(job_dir, "products_export.csv")
    with open(csv_path, "wb") as out:
        shutil.copyfileobj(file.file, out)

    count = count_products(csv_path)
    start, batch_id = reserve(ledger_store(settings), count, "myntra_filled.xlsx")
    job.batch_id = batch_id
    job.range = [start, start + count - 1]

    _spawn(job.id, csv_path, job_dir, start, settings)

    resp = _templates().TemplateResponse(
        request, "_stepper.html", {"job": job, "count": count})
    resp.headers["x-job-id"] = job.id
    return resp


def _spawn(job_id, csv_path, job_dir, start, settings):
    import threading
    threading.Thread(target=_run_generate,
                     args=(job_id, csv_path, job_dir, start, settings), daemon=True).start()


def _run_generate(job_id, csv_path, job_dir, start, settings):
    try:
        store.set_step(job_id, "Ingest CSV", "active")
        res = pipeline_main(csv_path=csv_path, out_dir=job_dir, style_group_id_start=start)
        for name in ["Ingest CSV", "Map attributes", "Images → S3", "Fill & validate", "Ready"]:
            store.set_step(job_id, name, "done")
        store.set_step(job_id, "Images → S3", "done", count=res.get("uploaded"))
        store.finish(job_id, res)
    except Exception as exc:  # surface failure to the UI
        store.fail(job_id, f"{type(exc).__name__}: {exc}")


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_status(request: Request, job_id: str):
    get_user(request)
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    if job.status == "running":
        count = sum(1 for _ in [s for s in job.steps if s["state"] == "done"]) or ""
        return _templates().TemplateResponse(
            request, "_stepper.html", {"job": job, "count": count})
    report = ""
    if job.result and os.path.exists(job.result.get("report", "")):
        with open(job.result["report"], encoding="utf-8") as fh:
            report = fh.read()
    return _templates().TemplateResponse(
        request, "_result.html", {"job": job, "report": report})


@router.get("/generate/download/{job_id}")
def download(request: Request, job_id: str):
    get_user(request)
    job = store.get(job_id)
    if not job or not job.result:
        raise HTTPException(status_code=404, detail="not ready")
    return FileResponse(job.result["filled"], filename="myntra_filled.xlsx")


@router.post("/generate/confirm/{job_id}", response_class=HTMLResponse)
def confirm_upload(request: Request, job_id: str):
    get_user(request)
    settings = get_settings(request)
    job = store.get(job_id)
    if not job or not job.batch_id:
        raise HTTPException(status_code=404, detail="unknown job")
    new_next = confirm(ledger_store(settings), job.batch_id)
    return HTMLResponse(
        f'<p class="ok mono">✓ Confirmed. Ledger advanced to {new_next}.</p>')
