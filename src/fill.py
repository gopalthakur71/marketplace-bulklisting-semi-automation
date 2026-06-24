import os
import warnings

import openpyxl

IMAGE_COLUMNS = ["Front Image", "Side Image", "Back Image", "Detail Angle",
                 "Look Shot Image", "Additional Image 1", "Additional Image 2"]


def fill_template(template_path, template, rows, out_path):
    warnings.filterwarnings("ignore")
    wb = openpyxl.load_workbook(template_path)
    ws = wb["Sarees"]

    r = template.first_data_row
    for mapped, images in rows:
        for header, value in mapped.cells.items():
            col = template.col_index_by_header.get(header)
            if col:
                ws.cell(row=r, column=col, value=value)
        passing_basenames = [os.path.basename(p) for p in images.passed]
        for header, basename in zip(IMAGE_COLUMNS, passing_basenames):
            col = template.col_index_by_header.get(header)
            if col:
                ws.cell(row=r, column=col, value=basename)
        r += 1

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    wb.save(out_path)
