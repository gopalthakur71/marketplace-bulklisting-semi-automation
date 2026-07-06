import pandas as pd

from src.core.models import Product

COLOR_COL = "Color (product.metafields.shopify.color-pattern)"
FABRIC_COL = "Fabric (product.metafields.shopify.fabric)"
SIZE_COL = "Size (product.metafields.shopify.size)"


def _first(series):
    """First non-null value in a column, or None."""
    nn = series.dropna()
    return nn.iloc[0] if len(nn) else None


def read_products(path):
    df = pd.read_csv(path, dtype=str)
    for col in ("Variant Price", "Variant Compare At Price", "Image Position"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    products = []
    for handle, grp in df.groupby("Handle", sort=False):
        # Image columns are always present in a real Shopify export, but guard so
        # an image-less CSV (e.g. the Generate pre-scan on a trimmed file) still reads.
        seen, urls = set(), []
        if "Image Src" in grp.columns:
            imgs = grp.dropna(subset=["Image Src"])
            if "Image Position" in imgs.columns:
                imgs = imgs.sort_values("Image Position")
            for u in imgs["Image Src"].tolist():
                if u not in seen:
                    seen.add(u)
                    urls.append(u)

        def fv(col):
            return _first(grp[col]) if col in grp.columns else None

        price = _first(grp["Variant Price"]) if "Variant Price" in grp else None
        cap = _first(grp["Variant Compare At Price"]) if "Variant Compare At Price" in grp else None

        products.append(Product(
            handle=handle,
            sku=fv("Variant SKU") or "",
            title=fv("Title") or "",
            vendor=fv("Vendor") or "",
            tags=fv("Tags") or "",
            body_html=fv("Body (HTML)") or "",
            price=float(price) if price is not None else None,
            compare_at_price=float(cap) if cap is not None else None,
            color=fv(COLOR_COL),
            fabric=fv(FABRIC_COL),
            size=fv(SIZE_COL),
            status=fv("Status"),
            images=urls,
        ))
    return products
