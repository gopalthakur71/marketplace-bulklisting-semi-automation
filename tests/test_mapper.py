from src.myntra.mapper import (validate_value, map_product, pick_colour_from_text,
                        extract_after_marker)
from src.core.models import Product, TemplateInfo

COLOUR_VOCAB = ["Red", "Blue", "Green", "Black", "Yellow", "Lavender", "Turquoise Blue", "NA"]


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


def test_extract_after_marker_keeps_only_description():
    html = ("<h3>Key Features</h3><p>Fabric: Silk</p>"
            "<h3>Product Description</h3><p>Experience the elegance.</p>")
    out = extract_after_marker(html, "Product Description")
    assert out == "<p>Experience the elegance.</p>"
    assert "Key Features" not in out
    assert "Fabric: Silk" not in out


def test_extract_after_marker_absent_returns_full():
    html = "<p>Just a description, no headings.</p>"
    assert extract_after_marker(html, "Product Description") == html


def test_pick_colour_longest_earliest_wins():
    assert pick_colour_from_text("Turquoise Blue Saree with Red Border",
                                 COLOUR_VOCAB, ["NA"]) == "Turquoise Blue"
    assert pick_colour_from_text("Jharokha Cotton Saree Black and Yellow",
                                 COLOUR_VOCAB, ["NA"]) == "Black"
    assert pick_colour_from_text("Banarasi Soft Organza Silk Saree Ivory",
                                 COLOUR_VOCAB, ["NA"]) is None


FABRIC_RULES = {
    "fabric_detection": {
        "order": ["cotton", "silk"],
        "cotton": {"Saree Fabric": "Pure Cotton", "Wash Care": "Hand Wash", "HSN": "52081120"},
        "silk": {"Saree Fabric": "Pure Silk", "Wash Care": "Dry Clean", "HSN": "50072010"},
    },
    "prominent_colour_from_name": True,
    "colour_scan_exclude": ["NA"],
}


def _template_with_rules():
    headers = ["vendorSkuCode", "MRP", "ISP", "HSN", "Prominent Colour", "brand",
               "Saree Fabric", "Wash Care"]
    return TemplateInfo(
        headers=headers, header_row=3, first_data_row=4,
        col_index_by_header={h: i + 1 for i, h in enumerate(headers)},
        vocab_by_header={"Prominent Colour": COLOUR_VOCAB,
                         "brand": ["Reebok", "Puma"],
                         "Saree Fabric": ["Pure Cotton", "Pure Silk"],
                         "Wash Care": ["Hand Wash", "Dry Clean"]},
    )


def test_cotton_fabric_block_and_colour_and_forced_brand():
    p = Product(handle="h", sku="S1", title="Lavender Pure Cotton Saree", vendor="V",
                tags="", body_html="", price=2000.0, compare_at_price=None,
                color=None, fabric=None, size=None, status="active", images=[])
    consts = {"brand": "Ijor Ethnic Partners"}
    row = map_product(p, _template_with_rules(), {}, consts, FABRIC_RULES)
    assert row.cells["HSN"] == "52081120"               # name has 'cotton'
    assert row.cells["Saree Fabric"] == "Pure Cotton"
    assert row.cells["Wash Care"] == "Hand Wash"
    assert row.cells["Prominent Colour"] == "Lavender"  # from name
    assert row.cells["brand"] == "Ijor Ethnic Partners"  # forced even if not in vocab
    assert any(f.field == "brand" for f in row.flags)    # but flagged


def test_silk_fabric_block():
    p = Product(handle="h", sku="S2", title="Banarasi Silk Saree Blue", vendor="V",
                tags="", body_html="", price=3000.0, compare_at_price=None,
                color=None, fabric=None, size=None, status="active", images=[])
    row = map_product(p, _template_with_rules(), {}, {}, FABRIC_RULES)
    assert row.cells["HSN"] == "50072010"
    assert row.cells["Saree Fabric"] == "Pure Silk"
    assert row.cells["Wash Care"] == "Dry Clean"
    assert row.cells["Prominent Colour"] == "Blue"
