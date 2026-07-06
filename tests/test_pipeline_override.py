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


def test_hsn_override_wins_in_mapper():
    from src.myntra.mapper import map_product
    from src.core.models import Product, TemplateInfo
    headers = ["SKUCode", "HSN"]
    tmpl = TemplateInfo(headers=headers, header_row=3, first_data_row=4,
                        col_index_by_header={h: i + 1 for i, h in enumerate(headers)},
                        vocab_by_header={})
    p = Product(handle="h", sku="S1", title="T", vendor="", tags="", body_html="",
                price=1.0, compare_at_price=None, color=None, fabric="Pure Silk",
                size=None, status="active", images=[])
    row = map_product(p, tmpl, {}, {"articleType": "Sarees"}, {},
                      hsn_by_signature={"saree|pure silk": "50072010"},
                      hsn_override="99999999")
    assert row.cells["HSN"] == "99999999"


def test_scan_content_hashes_pairs_sku_and_hash():
    from src.myntra.pipeline import scan_content_hashes
    pairs = scan_content_hashes("tests/fixtures/products_export.csv",
                                template_path="templates/myntra/Myntra-Sku-Template-2026-06-16.xlsx")
    assert len(pairs) == 2
    skus = [s for s, _ in pairs]
    assert len(set(skus)) == 2                 # distinct SKUs
    assert all(len(h) == 40 for _, h in pairs)  # sha1 hex


def test_pipeline_pins_id_hsn_and_returns_records(tmp_path):
    warnings.filterwarnings("ignore")
    from src.myntra.pipeline import main, scan_content_hashes
    pairs = dict(scan_content_hashes("tests/fixtures/products_export.csv",
                 template_path="templates/myntra/Myntra-Sku-Template-2026-06-16.xlsx"))
    sku0 = list(pairs)[0]
    res = main(
        template_path="templates/myntra/Myntra-Sku-Template-2026-06-16.xlsx",
        csv_path="tests/fixtures/products_export.csv",
        out_dir=str(tmp_path / "out"), config_dir="config/myntra",
        fetch=_fake_fetch(), upload=False,
        only_skus={sku0},
        style_group_id_by_sku={sku0: 77},
        hsn_by_sku={sku0: "63079090"},
    )
    assert res["products"] == 1                         # filtered to one SKU
    rec = res["records"][0]
    assert rec["sku"] == sku0
    assert rec["style_group_id"] == 77
    assert rec["hsn"] == "63079090"
    assert rec["content_hash"] == pairs[sku0]           # excludes id+HSN → matches scan
    ws = openpyxl.load_workbook(tmp_path / "out" / "myntra_filled.xlsx")["Sarees"]
    hdr = {ws.cell(3, c).value: c for c in range(1, ws.max_column + 1)}
    assert ws.cell(4, hdr["styleGroupId"]).value == 77
    assert ws.cell(4, hdr["HSN"]).value == 63079090
