import os
import pickle
import shutil
import uuid

import yaml
from fastapi import APIRouter, Request, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from src.myntra.corrector import correct
from src.myntra.error_reader import load_rules, read_errors
from src.myntra.mapper import validate_value  # noqa: F401 (correct() uses it internally)
from src.myntra.template_reader import read_template
from src.web.routers.pages import get_user

router = APIRouter()
RUNTIME = os.path.join(os.path.dirname(os.path.dirname(__file__)), "runtime")
CONSTANTS = os.path.join("config", "myntra", "constants.yaml")
TEMPLATE = os.path.join("templates", "myntra", "Myntra-Sku-Template-2026-06-16.xlsx")


def _templates():
    from src.web.main import templates
    return templates


def _resolve_template_path():
    return TEMPLATE


def _load_constants():
    with open(CONSTANTS, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@router.get("/fix", response_class=HTMLResponse)
def fix_form(request: Request):
    get_user(request)
    return _templates().TemplateResponse("fix.html", {"request": request, "user": get_user(request)})


@router.post("/fix", response_class=HTMLResponse)
def fix_upload(request: Request, file: UploadFile = File(...)):
    get_user(request)
    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Please upload the Myntra .xlsx file")
    fix_id = uuid.uuid4().hex
    fix_dir = os.path.join(RUNTIME, "fix-" + fix_id)
    os.makedirs(fix_dir, exist_ok=True)
    err_path = os.path.join(fix_dir, "rejection.xlsx")
    with open(err_path, "wb") as out:
        shutil.copyfileobj(file.file, out)

    rows = read_errors(err_path, load_rules())
    with open(os.path.join(fix_dir, "rows.pkl"), "wb") as fh:
        pickle.dump(rows, fh)

    resp = _templates().TemplateResponse(
        "_fix_review.html", {"request": request, "rows": rows, "fix_id": fix_id})
    resp.headers["x-fix-id"] = fix_id
    return resp


@router.post("/fix/apply/{fix_id}", response_class=HTMLResponse)
async def fix_apply(request: Request, fix_id: str):
    get_user(request)
    fix_dir = os.path.join(RUNTIME, "fix-" + fix_id)
    rows_pkl = os.path.join(fix_dir, "rows.pkl")
    if not os.path.exists(rows_pkl):
        raise HTTPException(status_code=404, detail="session expired, please re-upload")
    with open(rows_pkl, "rb") as fh:
        rows = pickle.load(fh)

    form = await request.form()
    answers, drops = {}, set()
    for key, value in form.items():
        if key.startswith("answer__") and str(value).strip():
            _, sku, field = key.split("__", 2)
            answers.setdefault(sku, {})[field] = value
        elif key.startswith("drop__"):
            drops.add(key.split("__", 1)[1])

    template = read_template(_resolve_template_path())
    out_path = os.path.join(fix_dir, "myntra_corrected.xlsx")
    summary = correct(rows, template, _resolve_template_path(), _load_constants(),
                      answers, drops, out_path)
    return _templates().TemplateResponse(
        "_fix_result.html", {"request": request, "summary": summary, "fix_id": fix_id})


@router.get("/fix/download/{fix_id}")
def fix_download(request: Request, fix_id: str):
    get_user(request)
    path = os.path.join(RUNTIME, "fix-" + fix_id, "myntra_corrected.xlsx")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="not ready")
    return FileResponse(path, filename="myntra_corrected.xlsx")
