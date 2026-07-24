import os
import tempfile

import yaml
from fastapi import APIRouter, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse

from src.myntra.template_reader import read_template
from src.myntra.preview import (read_filled_rows, reconstruct_title,
                                reconstruct_design_details, missing_attributes)
from src.web.routers.pages import get_user

router = APIRouter()
TEMPLATE = os.path.join("templates", "myntra", "Myntra-Sku-Template-2026-07-24.xlsx")
_FALLBACK_USER_FILLED = [
    "Prominent Colour", "Saree Fabric", "Blouse Fabric", "Type", "Ornamentation",
    "Border", "Pattern", "Print or Pattern Type", "Wash Care"]


def _templates():
    from src.web.main import templates
    return templates


def _user_filled():
    with open(os.path.join("config", "myntra", "rules.yaml"), encoding="utf-8") as fh:
        rules = yaml.safe_load(fh)
    return rules.get("user_filled_attributes") or _FALLBACK_USER_FILLED


@router.get("/preview", response_class=HTMLResponse)
def preview_form(request: Request):
    get_user(request)
    return _templates().TemplateResponse(request, "preview.html", {"user": get_user(request)})


@router.post("/preview", response_class=HTMLResponse)
async def preview_submit(request: Request, file: UploadFile = File(...)):
    get_user(request)
    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Please upload the filled .xlsx file")
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    with open(path, "wb") as out:
        out.write(await file.read())
    try:
        template = read_template(TEMPLATE)
        rows = read_filled_rows(path, template)
    finally:
        os.remove(path)
    user_filled = _user_filled()
    cards = [{
        "sku": attrs.get("vendorSkuCode") or attrs.get("SKUCode") or "",
        "title": reconstruct_title(attrs),
        "design_details": reconstruct_design_details(attrs),
        "specs": [(h, attrs.get(h)) for h in user_filled],
        "missing": missing_attributes(attrs, user_filled),
        "front_image": attrs.get("Front Image"),
    } for attrs in rows]
    return _templates().TemplateResponse(request, "_preview.html", {"cards": cards})
