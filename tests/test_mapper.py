from src.mapper import validate_value, map_product
from src.models import Product, TemplateInfo


def _template():
    headers = ["vendorSkuCode", "vendorArticleName", "SKUCode", "vendorArticleNumber",
               "productDisplayName", "MRP", "ISP", "tags", "Product Details",
               "Prominent Colour", "Saree Fabric", "articleType", "Country Of Origin",
               "brand", "Occasion"]
    return TemplateInfo(
        headers=headers, header_row=3, first_data_row=4,
        col_index_by_header={h: i + 1 for i, h in enumerate(headers)},
        vocab_by_header={
            "Prominent Colour": ["Red", "Blue", "Green"],
            "Saree Fabric": ["Pure Silk", "Art Silk"],
            "articleType": ["Sarees"],
            "Country Of Origin": ["India"],
            "Occasion": ["Party", "Festive"],
        },
    )


def test_validate_value_canonicalizes_case():
    assert validate_value(" blue ", ["Red", "Blue"]) == "Blue"
    assert validate_value("silk", ["Pure Silk", "Art Silk"]) is None


def test_map_product_fills_identity_and_pricing():
    p = Product(handle="h", sku="S1", title="Blue Saree", vendor="Ijor Ethnic",
                tags="Saree, Silk", body_html="<p>nice</p>", price=3199.0,
                compare_at_price=3499.0, color="Blue", fabric="silk",
                size=None, status="active", images=[])
    cmap = {"title": "vendorArticleName", "sku": "vendorSkuCode", "tags": "tags",
            "body_html": "Product Details", "color": "Prominent Colour", "fabric": "Saree Fabric"}
    consts = {"articleType": "Sarees", "Country Of Origin": "India", "brand": "Ijor"}
    row = map_product(p, _template(), cmap, consts)
    assert row.cells["vendorSkuCode"] == "S1"
    assert row.cells["MRP"] == "3499"
    assert row.cells["ISP"] == "3199"
    assert row.cells["Prominent Colour"] == "Blue"
    assert row.cells["articleType"] == "Sarees"
    assert row.cells["Country Of Origin"] == "India"
    assert "Saree Fabric" not in row.cells
    assert any(f.field == "Saree Fabric" for f in row.flags)
    assert "Occasion" in row.blanks
