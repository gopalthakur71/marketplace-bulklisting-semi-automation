# tests/test_corrector.py
import openpyxl
from src.myntra.error_reader import load_rules, read_errors
from src.myntra.template_reader import read_template
from src.myntra.corrector import plan_corrections, correct

TEMPLATE = "templates/myntra/Myntra-Sku-Template-2026-06-16.xlsx"
IMG = "https://ijorethnicpartners.s3.ap-south-1.amazonaws.com/myntra"
# correct(row_errors, template, template_path, constants, answers, drops, out_path)


def _make_resub(path, rows):
    """rows = list of dict(status, message, cells={header: value})."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sarees"
    headers = ["STATUS", "SYSTEM ERROR MESSAGE", "styleGroupId", "vendorSkuCode",
               "brand", "Prominent Colour", "Brand Colour (Remarks)", "Front Image"]
    for c, h in enumerate(headers, start=1):
        ws.cell(row=3, column=c, value=h)
    r = 4
    for row in rows:
        ws.cell(row=r, column=1, value=row["status"])
        ws.cell(row=r, column=2, value=row["message"])
        for c, h in enumerate(headers, start=1):
            if h in ("STATUS", "SYSTEM ERROR MESSAGE"):
                continue
            ws.cell(row=r, column=c, value=row["cells"].get(h))
        r += 1
    wb.save(path)


def test_plan_marks_drop_and_manual(tmp_path):
    p = tmp_path / "resub.xlsx"
    _make_resub(p, [
        {"status": "SKU_VALIDATION_FAILED",
         "message": "Brand Colour (Remarks) cannot be null",
         "cells": {"styleGroupId": "11", "vendorSkuCode": "78SAZ125BSI",
                   "brand": "Ijor Ethnic Partners", "Front Image": f"{IMG}/78SAZ125BSI/1.jpg"}},
        {"status": "SKU_VALIDATION_FAILED",
         "message": "Seller Sku Code 165SDE226RSG is already registered",
         "cells": {"styleGroupId": "12", "vendorSkuCode": "165SDE226RSG",
                   "brand": "Ijor Ethnic Partners", "Front Image": f"{IMG}/165SDE226RSG/1.jpg"}},
    ])
    rules = load_rules()
    errs = read_errors(str(p), rules)
    plan = plan_corrections(errs)
    assert plan["drop"] == ["165SDE226RSG"]
    assert plan["manual"][0]["sku"] == "78SAZ125BSI"
    assert plan["manual"][0]["field"] == "Prominent Colour"


def test_correct_drops_and_applies_answer(tmp_path):
    p = tmp_path / "resub.xlsx"
    _make_resub(p, [
        {"status": "SKU_VALIDATION_FAILED",
         "message": "Brand Colour (Remarks) cannot be null",
         "cells": {"styleGroupId": "11", "vendorSkuCode": "78SAZ125BSI",
                   "brand": "Ijor Ethnic Partners", "Front Image": f"{IMG}/78SAZ125BSI/1.jpg"}},
        {"status": "SKU_VALIDATION_FAILED",
         "message": "Seller Sku Code 165SDE226RSG is already registered",
         "cells": {"styleGroupId": "12", "vendorSkuCode": "165SDE226RSG",
                   "brand": "Ijor Ethnic Partners", "Front Image": f"{IMG}/165SDE226RSG/1.jpg"}},
    ])
    rules = load_rules()
    errs = read_errors(str(p), rules)
    template = read_template(TEMPLATE)
    out = tmp_path / "corrected.xlsx"
    summary = correct(
        errs, template, TEMPLATE, constants={},
        answers={"78SAZ125BSI": {"Prominent Colour": "White"}},
        drops={"165SDE226RSG"}, out_path=str(out),
    )
    assert summary["written"] == 1
    assert summary["dropped"] == ["165SDE226RSG"]
    ws = openpyxl.load_workbook(out)["Sarees"]
    hdr = {ws.cell(3, c).value: c for c in range(1, ws.max_column + 1)}
    # only the kept SKU is written, with the chosen colour and its image URL
    assert ws.cell(4, hdr["vendorSkuCode"]).value == "78SAZ125BSI"
    assert ws.cell(4, hdr["Prominent Colour"]).value == "White"
    assert ws.cell(4, hdr["Front Image"]).value == f"{IMG}/78SAZ125BSI/1.jpg"
    assert ws.cell(5, hdr["vendorSkuCode"]).value in (None, "")  # dropped SKU not written
