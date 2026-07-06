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


def test_hsn_by_signature_written_to_sheet(tmp_path):
    warnings.filterwarnings("ignore")
    out = tmp_path / "out"
    # fixture signatures: saree|cotton, saree|silk (fabric metafield = cotton/silk)
    main(
        template_path="templates/myntra/Myntra-Sku-Template-2026-06-16.xlsx",
        csv_path="tests/fixtures/products_export.csv",
        out_dir=str(out),
        config_dir="config/myntra",
        fetch=_fake_fetch(),
        upload=False,
        hsn_by_signature={"saree|cotton": "52081120", "saree|silk": "50072010"},
    )
    ws = openpyxl.load_workbook(out / "myntra_filled.xlsx")["Sarees"]
    hdr = {ws.cell(3, c).value: c for c in range(1, ws.max_column + 1)}
    # HSN is in NUMERIC_HEADERS -> written as an integer cell
    assert ws.cell(4, hdr["HSN"]).value == 52081120
    assert ws.cell(5, hdr["HSN"]).value == 50072010


def test_no_hsn_map_leaves_hsn_blank(tmp_path):
    warnings.filterwarnings("ignore")
    out = tmp_path / "out"
    main(
        template_path="templates/myntra/Myntra-Sku-Template-2026-06-16.xlsx",
        csv_path="tests/fixtures/products_export.csv",
        out_dir=str(out),
        config_dir="config/myntra",
        fetch=_fake_fetch(),
        upload=False,
    )
    ws = openpyxl.load_workbook(out / "myntra_filled.xlsx")["Sarees"]
    hdr = {ws.cell(3, c).value: c for c in range(1, ws.max_column + 1)}
    # CLI path (no map): HSN no longer filled by the fabric block
    assert ws.cell(4, hdr["HSN"]).value in (None, "")
