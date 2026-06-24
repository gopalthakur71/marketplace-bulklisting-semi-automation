import os
import warnings

import openpyxl

from src.template_reader import read_template
from src.models import MappedRow, ImageResult
from src.fill import fill_template

TEMPLATE = "Myntra-Sku-Template-2026-06-16.xlsx"


def test_fill_writes_rows(tmp_path):
    warnings.filterwarnings("ignore")
    t = read_template(TEMPLATE)
    r1 = MappedRow(sku="S1", cells={"vendorSkuCode": "S1", "vendorArticleName": "Blue Saree",
                                    "MRP": "3499", "ISP": "3199", "articleType": "Sarees"})
    img = ImageResult(sku="S1", jpgs=["S1_1.jpg"], passed=["/x/S1_1.jpg"], failed=[])
    out = tmp_path / "filled.xlsx"
    fill_template(TEMPLATE, t, [(r1, img)], str(out))
    assert os.path.exists(out)
    wb = openpyxl.load_workbook(out)
    ws = wb["Sarees"]
    row = t.first_data_row
    sku_col = t.col_index_by_header["vendorSkuCode"]
    name_col = t.col_index_by_header["vendorArticleName"]
    front_col = t.col_index_by_header["Front Image"]
    assert ws.cell(row=row, column=sku_col).value == "S1"
    assert ws.cell(row=row, column=name_col).value == "Blue Saree"
    assert ws.cell(row=row, column=front_col).value == "S1_1.jpg"
