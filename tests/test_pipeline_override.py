import io
import warnings
import openpyxl
from PIL import Image
from src.myntra.pipeline import main


def _fake_fetch():
    buf = io.BytesIO()
    Image.new("RGB", (1000, 1000), (10, 20, 30)).save(buf, "PNG")
    data = buf.getvalue()
    return lambda url: data


def test_style_group_id_start_override(tmp_path):
    warnings.filterwarnings("ignore")
    out = tmp_path / "out"
    main(
        template_path="templates/myntra/Myntra-Sku-Template-2026-06-16.xlsx",
        csv_path="tests/fixtures/products_export.csv",
        out_dir=str(out),
        config_dir="config/myntra",
        fetch=_fake_fetch(),
        upload=False,
        style_group_id_start=100,
    )
    ws = openpyxl.load_workbook(out / "myntra_filled.xlsx")["Sarees"]
    hdr = {ws.cell(3, c).value: c for c in range(1, ws.max_column + 1)}
    # fixture has 2 products -> styleGroupIds 100, 101
    assert ws.cell(4, hdr["styleGroupId"]).value == 100
    assert ws.cell(5, hdr["styleGroupId"]).value == 101
