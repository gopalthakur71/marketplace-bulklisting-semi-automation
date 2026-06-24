from src.shopify_reader import read_products

CSV = "products_export.csv"


def test_groups_into_products():
    products = read_products(CSV)
    assert len(products) == 7
    p = next(x for x in products if x.handle == "banarasi-soft-semi-katan-silk-saree-blue")
    assert p.sku == "87SAZ125BSB"
    assert p.title == "Banarasi Soft Semi Katan Silk Saree Blue"
    assert p.price == 3199.0
    assert p.compare_at_price == 3499.0


def test_images_ordered_by_position():
    products = read_products(CSV)
    p = next(x for x in products if x.handle == "banarasi-soft-semi-katan-silk-saree-blue")
    assert len(p.images) >= 2
    assert "-1.webp" in p.images[0]
    assert all("http" in u for u in p.images)
