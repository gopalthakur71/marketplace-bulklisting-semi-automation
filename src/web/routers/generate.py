import csv as csvmod
import json
import os
import re
import shutil

import yaml
from fastapi import APIRouter, Request, UploadFile, File, HTTPException, Form
from fastapi.responses import FileResponse, HTMLResponse

from src.core.shopify_reader import read_products
from src.myntra.groupid_ledger import reserve, confirm, unconfirm, read_ledger
from src.myntra.hsn_kb import signature, read_kb, suggest, learn
from src.myntra.pipeline import main as pipeline_main  # noqa: F401 (patched in tests)
from src.web.jobs import store
from src.web.routers.pages import get_user, get_settings
from src.web.settings import ledger_store, hsn_store

router = APIRouter()
RUNTIME = os.path.join(os.path.dirname(os.path.dirname(__file__)), "runtime")
CONFIG_DIR = "config/myntra"


def _safe_job_id(job_id: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise HTTPException(status_code=404, detail="unknown job")
    return job_id


def _load_yaml(name):
    with open(os.path.join(CONFIG_DIR, name), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


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

    # Pre-scan: which category|fabric signatures does this batch contain, and what
    # does the KB already know? HSN is absent from the export, so we always ask.
    constants = _load_yaml("constants.yaml")
    rules = _load_yaml("rules.yaml")
    category = constants.get("articleType", "")
    fabric_keywords = (rules.get("fabric_detection") or {}).get("order") or []
    kb = read_kb(hsn_store(settings))
    grouped = {}
    for p in read_products(csv_path):
        grouped.setdefault(signature(p, category, fabric_keywords), []).append(p.title)

    if not grouped:                      # empty CSV / no products → nothing to ask
        return _start_build(request, job, csv_path, job_dir, count, settings)

    signatures = [{"signature": sig, "examples": names[:5], "suggestions": suggest(kb, sig)}
                  for sig, names in grouped.items()]
    with open(os.path.join(job_dir, "hsn.json"), "w", encoding="utf-8") as fh:
        json.dump({"csv_path": csv_path, "count": count, "signatures": signatures}, fh)
    job.status = "awaiting_hsn"

    resp = _templates().TemplateResponse(
        request, "_hsn_review.html", {"job_id": job.id, "signatures": signatures})
    resp.headers["x-job-id"] = job.id
    return resp


def _start_build(request, job, csv_path, job_dir, count, settings, hsn_by_signature=None):
    start, batch_id = reserve(ledger_store(settings), count, "myntra_filled.xlsx")
    job.batch_id = batch_id
    job.range = [start, start + count - 1]
    job.status = "running"
    _spawn(job.id, csv_path, job_dir, start, settings, hsn_by_signature)
    resp = _templates().TemplateResponse(
        request, "_stepper.html", {"job": job, "count": count})
    resp.headers["x-job-id"] = job.id
    return resp


@router.post("/generate/hsn/{job_id}", response_class=HTMLResponse)
async def hsn_submit(request: Request, job_id: str):
    get_user(request)
    settings = get_settings(request)
    job_id = _safe_job_id(job_id)
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="unknown job")
    job_dir = os.path.join(RUNTIME, job_id)
    hsn_path = os.path.join(job_dir, "hsn.json")
    if not os.path.exists(hsn_path):
        raise HTTPException(status_code=404, detail="session expired, please re-upload")
    with open(hsn_path, encoding="utf-8") as fh:
        data = json.load(fh)
    signatures = data["signatures"]

    form = await request.form()
    values = [str(form.get(f"hsn__{i}", "")).strip() for i in range(len(signatures))]
    if any(not re.fullmatch(r"\d{8}", v) for v in values):
        return _templates().TemplateResponse(
            request, "_hsn_review.html",
            {"job_id": job_id, "signatures": signatures, "values": values,
             "error": "Each HSN must be exactly 8 digits."})

    hsn_by_signature = {}
    for i, s in enumerate(signatures):
        example = s["examples"][0] if s["examples"] else None
        learn(hsn_store(settings), s["signature"], values[i], example_name=example)
        hsn_by_signature[s["signature"]] = values[i]

    return _start_build(request, job, data["csv_path"], job_dir,
                        data["count"], settings, hsn_by_signature)


def _spawn(job_id, csv_path, job_dir, start, settings, hsn_by_signature=None):
    import threading
    threading.Thread(
        target=_run_generate,
        args=(job_id, csv_path, job_dir, start, settings, hsn_by_signature),
        daemon=True).start()


def _run_generate(job_id, csv_path, job_dir, start, settings, hsn_by_signature=None):
    try:
        store.set_step(job_id, "Ingest CSV", "active")
        res = pipeline_main(csv_path=csv_path, out_dir=job_dir,
                            style_group_id_start=start,
                            hsn_by_signature=hsn_by_signature)
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
    return _templates().TemplateResponse(
        request, "_confirmed.html", {"job": job, "new_next": new_next})


@router.post("/generate/unconfirm/{job_id}", response_class=HTMLResponse)
def unconfirm_upload(request: Request, job_id: str):
    get_user(request)
    settings = get_settings(request)
    job = store.get(job_id)
    if not job or not job.batch_id:
        raise HTTPException(status_code=404, detail="unknown job")
    try:
        unconfirm(ledger_store(settings), job.batch_id)
    except (ValueError, KeyError) as exc:
        # Guard tripped (a later batch was confirmed) — stay confirmed, show why.
        led = read_ledger(ledger_store(settings))
        return _templates().TemplateResponse(
            request, "_confirmed.html",
            {"job": job, "new_next": led["next_style_group_id"], "error": str(exc)})
    return _templates().TemplateResponse(request, "_mark_upload.html", {"job": job})


@router.post("/generate/style-start", response_class=HTMLResponse)
def style_start_set(request: Request, last_used: int = Form(...)):
    get_user(request)
    settings = get_settings(request)
    from src.myntra.groupid_ledger import set_next
    res = set_next(ledger_store(settings), last_used)
    return _templates().TemplateResponse(
        request, "_style_start.html", {"next_id": res["next"], "warn": res["warn"]})


@router.post("/generate/style-start/undo", response_class=HTMLResponse)
def style_start_undo(request: Request):
    get_user(request)
    settings = get_settings(request)
    from src.myntra.groupid_ledger import undo_set_next, read_ledger as _rl
    try:
        next_id = undo_set_next(ledger_store(settings))
    except ValueError:
        next_id = _rl(ledger_store(settings))["next_style_group_id"]
    return _templates().TemplateResponse(
        request, "_style_start.html", {"next_id": next_id, "undone": True})
