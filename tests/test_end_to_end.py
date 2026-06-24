import io
import os
import warnings

import openpyxl
from PIL import Image

from run import main


def _fake_fetch_factory():
    buf = io.BytesIO()
    Image.new("RGBA", (1000, 1200), (200, 30, 30, 255)).save(buf, "PNG")
    data = buf.getvalue()
    return lambda url: data


def test_full_pipeline(tmp_path):
    warnings.filterwarnings("ignore")
    out_dir = tmp_path / "output"
    result = main(
        template_path="Myntra-Sku-Template-2026-06-16.xlsx",
        csv_path="products_export.csv",
        out_dir=str(out_dir),
        config_dir="config",
        fetch=_fake_fetch_factory(),
    )
    assert result["products"] == 7
    assert os.path.exists(result["filled"])
    assert os.path.exists(result["report"])
    wb = openpyxl.load_workbook(result["filled"])
    ws = wb["Sarees"]
    assert ws.cell(row=4, column=3).value not in (None, "")
    assert any(f.endswith(".jpg") for f in os.listdir(out_dir / "images"))
    # Front Image column (74) holds a CDN URL, not a local filename
    front = ws.cell(row=4, column=74).value
    assert front and front.startswith("http")
