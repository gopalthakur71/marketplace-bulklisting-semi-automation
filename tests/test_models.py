from src.models import Product, Flag, MappedRow, ImageResult, TemplateInfo


def test_product_defaults_images_list():
    p = Product(handle="h", sku="S1", title="T", vendor="V", tags="", body_html="",
                price=10.0, compare_at_price=None, color=None, fabric=None,
                size=None, status="active", images=[])
    assert p.images == []
    assert p.sku == "S1"


def test_mapped_row_holds_cells_and_flags():
    f = Flag(sku="S1", field="Saree Fabric", reason="not in vocab", value="silk")
    r = MappedRow(sku="S1", cells={"MRP": "3499"}, flags=[f], blanks=["Occasion"])
    assert r.cells["MRP"] == "3499"
    assert r.flags[0].field == "Saree Fabric"
    assert "Occasion" in r.blanks
