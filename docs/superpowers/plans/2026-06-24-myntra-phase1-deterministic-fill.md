# Myntra Phase 1 — Deterministic Template Fill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One command turns the real Shopify saree CSV export + the Myntra DIY saree template into a filled `output/myntra_filled.xlsx` (Sarees sheet) plus Myntra-compliant JPG images, deterministically.

**Architecture:** Six focused modules under `src/`, wired by `run.py`. `template_reader` reads Sarees headers (row 3) and parses the 37 Excel x14 data-validations from the raw sheet XML to build an exact `{header -> allowed values}` vocab map (openpyxl drops these). `shopify_reader` groups CSV rows by `Handle`, forward-fills product fields, and collects the ordered image gallery. `mapper` applies `config/column_map.yaml` and validates each value against vocab (flag, never guess). `images` downloads/flattens/converts to JPG q90. `fill` writes rows into the Sarees sheet from row 4. `report` emits `report.txt`.

**Tech Stack:** Python 3.12, pandas, openpyxl, Pillow, PyYAML, stdlib `zipfile`/`re` (x14 parsing), `requests` (image download), pytest.

## Global Constraints

- Python 3.12; libraries already installed: pandas, openpyxl, Pillow (PIL), PyYAML. Add `requests` and `pytest` (`pip install requests pytest`).
- The LLM/model decides NOTHING. All mapping, pricing, validation is code.
- Myntra Sarees sheet: headers on **row 3**, first data row **row 4**, 80 columns. Image columns are indexes **74–80** (`Front Image, Side Image, Back Image, Detail Angle, Look Shot Image, Additional Image 1, Additional Image 2`).
- Never silently write an invalid value. If a mapped value is not a valid vocab value, **leave the cell blank and record a flag** in the report.
- Vocab match is case-insensitive + whitespace-trimmed; on match, write the **canonical** vocab spelling. No fuzzy/substring guessing.
- Myntra-only attribute fields with no Shopify source are left blank and flagged (Phase 2 fills them).
- Images: input WebP/PNG/JPG → output **JPG only**, JPEG quality 90. Flatten transparency onto white via `Image.alpha_composite` BEFORE `convert('RGB')`. Naming `SKU_1.jpg`, `SKU_2.jpg`, ... per product in `output/images/`.
- Dropdown preservation on write is DEFERRED (owner decision). Phase 1 fills values correctly first; output may lose x14 validations for now.
- Input files live in project root today: `products_export.csv`, `Myntra-Sku-Template-2026-06-16.xlsx`. Paths are configurable; default to these.
- Real data facts: CSV has 7 products / 59 rows, one variant each (`Default Title`); gallery images are `.webp` ordered by `Image Position`; product fields populated only on the group's first row (forward-fill). Verified x14 validation→column→masterdata-range map is in the spec.

---

## File Structure

- `src/__init__.py` — package marker
- `src/models.py` — dataclasses: `Product`, `TemplateInfo`, `MappedRow`, `Flag`, `ImageResult`
- `src/template_reader.py` — read headers + parse x14 vocab
- `src/shopify_reader.py` — CSV → `list[Product]`
- `src/mapper.py` — column_map + pricing + vocab validation → `MappedRow`
- `src/images.py` — download/flatten/convert/validate/name
- `src/fill.py` — write rows into Sarees sheet, save xlsx
- `src/report.py` — write `report.txt`
- `run.py` — orchestrator
- `config/column_map.yaml`, `config/constants.yaml`, `config/image_specs.yaml`
- `tests/conftest.py` + `tests/test_*.py`
- `README.md`

---

## Task 1: Project scaffold, deps, models

**Files:**
- Create: `src/__init__.py` (empty), `src/models.py`, `requirements.txt`
- Test: `tests/test_models.py`, `tests/conftest.py` (empty for now)

**Interfaces:**
- Produces: dataclasses used everywhere.
  - `Product(handle:str, sku:str, title:str, vendor:str, tags:str, body_html:str, price:float|None, compare_at_price:float|None, color:str|None, fabric:str|None, size:str|None, status:str|None, images:list[str])`
  - `Flag(sku:str, field:str, reason:str, value:str|None)`
  - `MappedRow(sku:str, cells:dict[str,str], flags:list[Flag], blanks:list[str])`  (`cells` keyed by Sarees header name)
  - `ImageResult(sku:str, jpgs:list[str], passed:list[str], failed:list[tuple[str,str]])`  (`failed` = (filename, reason))
  - `TemplateInfo(headers:list[str], header_row:int, first_data_row:int, col_index_by_header:dict[str,int], vocab_by_header:dict[str,list[str]])`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.models'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/models.py
from dataclasses import dataclass, field

@dataclass
class Product:
    handle: str
    sku: str
    title: str
    vendor: str
    tags: str
    body_html: str
    price: float | None
    compare_at_price: float | None
    color: str | None
    fabric: str | None
    size: str | None
    status: str | None
    images: list[str] = field(default_factory=list)

@dataclass
class Flag:
    sku: str
    field: str
    reason: str
    value: str | None = None

@dataclass
class MappedRow:
    sku: str
    cells: dict[str, str] = field(default_factory=dict)
    flags: list[Flag] = field(default_factory=list)
    blanks: list[str] = field(default_factory=list)

@dataclass
class ImageResult:
    sku: str
    jpgs: list[str] = field(default_factory=list)
    passed: list[str] = field(default_factory=list)
    failed: list[tuple] = field(default_factory=list)

@dataclass
class TemplateInfo:
    headers: list[str]
    header_row: int
    first_data_row: int
    col_index_by_header: dict[str, int]
    vocab_by_header: dict[str, list[str]]
```

Also create `src/__init__.py` (empty) and `requirements.txt`:

```
pandas
openpyxl
Pillow
PyYAML
requests
pytest
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pip install -r requirements.txt && python -m pytest tests/test_models.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/__init__.py src/models.py requirements.txt tests/test_models.py tests/conftest.py
git commit -m "feat: project scaffold and data models"
```

---

## Task 2: template_reader — headers + x14 vocab extraction

**Files:**
- Create: `src/template_reader.py`
- Test: `tests/test_template_reader.py`

**Interfaces:**
- Consumes: `TemplateInfo` from `src/models.py`.
- Produces: `read_template(path:str) -> TemplateInfo`. Header row detected as the row (1-based) where `styleId` appears in column 1 (row 3 in the real file); `first_data_row = header_row + 1`. `vocab_by_header` maps Sarees header name → list of allowed values resolved from masterdata ranges in the x14 validations.

- [ ] **Step 1: Write the failing test** (uses the real template file in repo root)

```python
# tests/test_template_reader.py
from src.template_reader import read_template

TEMPLATE = "Myntra-Sku-Template-2026-06-16.xlsx"

def test_headers_and_data_row():
    t = read_template(TEMPLATE)
    assert t.headers[0] == "styleId"
    assert t.header_row == 3
    assert t.first_data_row == 4
    assert t.col_index_by_header["brand"] == 6
    assert t.col_index_by_header["Front Image"] == 74

def test_vocab_extracted_from_x14():
    t = read_template(TEMPLATE)
    # Occasion vocab includes 'Party' and 'Festive'
    occ = t.vocab_by_header["Occasion"]
    assert "Party" in occ and "Festive" in occ
    # Country Of Origin includes India
    assert "India" in t.vocab_by_header["Country Of Origin"]
    # articleType single value
    assert t.vocab_by_header["articleType"] == ["Sarees"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_template_reader.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.template_reader'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/template_reader.py
import re
import zipfile
import warnings
import openpyxl
from openpyxl.utils import column_index_from_string
from src.models import TemplateInfo

SHEET_SARETES_NAME = "Sarees"
MASTERDATA_NAME = "masterdata"

def _find_sheet_xml_name(xlsx_path, sheet_title):
    """Return the worksheets/sheetN.xml path for a given sheet title."""
    wb = openpyxl.load_workbook(xlsx_path, read_only=True)
    # openpyxl keeps sheet order matching workbook.xml; map by index.
    idx = wb.sheetnames.index(sheet_title)  # 0-based
    wb.close()
    return f"xl/worksheets/sheet{idx + 1}.xml"

def _parse_x14_validations(xlsx_path, sheet_xml_name):
    """Return list of (col_index, masterdata_col_index, first_row, last_row)."""
    with zipfile.ZipFile(xlsx_path) as z:
        xml = z.read(sheet_xml_name).decode("utf-8")
    out = []
    for block in re.findall(r"<x14:dataValidation\b.*?</x14:dataValidation>", xml, re.S):
        fm = re.search(r"<xm:f>(.*?)</xm:f>", block, re.S)
        sq = re.search(r"<xm:sqref>(.*?)</xm:sqref>", block, re.S)
        if not (fm and sq):
            continue
        col_letter = re.match(r"([A-Z]+)", sq.group(1)).group(1)
        col_index = column_index_from_string(col_letter)
        m = re.search(r"masterdata!\$([A-Z]+)\$(\d+):\$([A-Z]+)\$(\d+)", fm.group(1))
        if not m:
            continue
        md_col = column_index_from_string(m.group(1))
        out.append((col_index, md_col, int(m.group(2)), int(m.group(4))))
    return out

def read_template(path):
    warnings.filterwarnings("ignore")
    wb = openpyxl.load_workbook(path)
    ws = wb[SHEET_SARETES_NAME]
    md = wb[MASTERDATA_NAME]

    # detect header row: row whose column 1 == 'styleId'
    header_row = next(r for r in range(1, 11) if ws.cell(row=r, column=1).value == "styleId")
    max_col = ws.max_column
    headers = [ws.cell(row=header_row, column=c).value for c in range(1, max_col + 1)]
    col_index_by_header = {h: i + 1 for i, h in enumerate(headers) if h not in (None, "")}

    sheet_xml = _find_sheet_xml_name(path, SHEET_SARETES_NAME)
    validations = _parse_x14_validations(path, sheet_xml)

    vocab_by_header = {}
    for col_index, md_col, r0, r1 in validations:
        header = headers[col_index - 1] if col_index - 1 < len(headers) else None
        if not header:
            continue
        values = []
        for r in range(r0, r1 + 1):
            v = md.cell(row=r, column=md_col).value
            if v not in (None, ""):
                values.append(str(v).strip())
        vocab_by_header[header] = values

    wb.close()
    return TemplateInfo(
        headers=[h for h in headers if h not in (None, "")],
        header_row=header_row,
        first_data_row=header_row + 1,
        col_index_by_header=col_index_by_header,
        vocab_by_header=vocab_by_header,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_template_reader.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/template_reader.py tests/test_template_reader.py
git commit -m "feat: read Myntra template headers and x14 dropdown vocab"
```

---

## Task 3: shopify_reader — group + forward-fill + image gallery

**Files:**
- Create: `src/shopify_reader.py`
- Test: `tests/test_shopify_reader.py`

**Interfaces:**
- Consumes: `Product` from `src/models.py`.
- Produces: `read_products(path:str) -> list[Product]`. One `Product` per `Handle`. Product-level fields taken from the group's first non-null occurrence (forward-fill). `images` = list of `Image Src` URLs ordered by `Image Position` (ascending, deduped, non-null).

- [ ] **Step 1: Write the failing test** (uses real CSV in repo root)

```python
# tests/test_shopify_reader.py
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
    assert p.images[0].endswith("Banarasi_Soft_Semi_Katan_Silk_Saree_Blue-1.webp?v=1771660804") \
        or "-1.webp" in p.images[0]
    assert all("http" in u for u in p.images)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_shopify_reader.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.shopify_reader'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/shopify_reader.py
import pandas as pd
from src.models import Product

COLOR_COL = "Color (product.metafields.shopify.color-pattern)"
FABRIC_COL = "Fabric (product.metafields.shopify.fabric)"
SIZE_COL = "Size (product.metafields.shopify.size)"

def _first(series):
    """First non-null value in a column, or None."""
    nn = series.dropna()
    return nn.iloc[0] if len(nn) else None

def read_products(path):
    df = pd.read_csv(path, dtype=str)
    # numeric parse for prices
    for col in ("Variant Price", "Variant Compare At Price", "Image Position"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    products = []
    for handle, grp in df.groupby("Handle", sort=False):
        # ordered, deduped image urls by Image Position
        imgs = grp[["Image Src", "Image Position"]].dropna(subset=["Image Src"])
        imgs = imgs.sort_values("Image Position")
        seen, urls = set(), []
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_shopify_reader.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/shopify_reader.py tests/test_shopify_reader.py
git commit -m "feat: read and group Shopify products with image gallery"
```

---

## Task 4: config files (column_map, constants, image_specs)

**Files:**
- Create: `config/column_map.yaml`, `config/constants.yaml`, `config/image_specs.yaml`
- Test: `tests/test_config_loads.py`

**Interfaces:**
- Produces: three YAML files consumed by `mapper` and `images`.
  - `column_map.yaml`: `{ "<Shopify field key>": "<Myntra header>" }` where field key is one of the `Product` attribute names (`title`, `sku`, `tags`, `body_html`, `color`, `fabric`).
  - `constants.yaml`: `{ "<Myntra header>": "<constant value>" }`.
  - `image_specs.yaml`: `{ min_width, min_height, max_bytes, quality, max_images }`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_loads.py
import yaml

def test_column_map_has_core_fields():
    m = yaml.safe_load(open("config/column_map.yaml"))
    assert m["title"] == "vendorArticleName"
    assert m["sku"] == "vendorSkuCode"
    assert m["color"] == "Prominent Colour"
    assert m["fabric"] == "Saree Fabric"

def test_constants_and_specs():
    c = yaml.safe_load(open("config/constants.yaml"))
    assert c["articleType"] == "Sarees"
    assert c["Country Of Origin"] == "India"
    s = yaml.safe_load(open("config/image_specs.yaml"))
    assert s["quality"] == 90
    assert s["max_images"] == 7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config_loads.py -v`
Expected: FAIL with `FileNotFoundError: config/column_map.yaml`

- [ ] **Step 3: Write the config files**

```yaml
# config/column_map.yaml
# Shopify Product field (left) -> Myntra Sarees header (right). Direct copies only.
title: vendorArticleName
sku: vendorSkuCode
tags: tags
body_html: Product Details
color: Prominent Colour
fabric: Saree Fabric
```

```yaml
# config/constants.yaml
# Myntra header -> fixed value applied to every row.
articleType: Sarees
Country Of Origin: India
brand: Ijor
```

```yaml
# config/image_specs.yaml
min_width: 700
min_height: 700
max_bytes: 10485760   # 10 MB
quality: 90
max_images: 7         # image columns 74-80
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config_loads.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add config/column_map.yaml config/constants.yaml config/image_specs.yaml tests/test_config_loads.py
git commit -m "feat: add column map, constants, and image spec config"
```

---

## Task 5: mapper — column map + pricing + vocab validation

**Files:**
- Create: `src/mapper.py`
- Test: `tests/test_mapper.py`

**Interfaces:**
- Consumes: `Product`, `TemplateInfo`, `MappedRow`, `Flag` from models; `read_template` output.
- Produces:
  - `validate_value(value:str, vocab:list[str]) -> str|None` — returns canonical vocab spelling on case-insensitive trimmed match, else `None`.
  - `map_product(product:Product, template:TemplateInfo, column_map:dict, constants:dict) -> MappedRow`.
  - Pricing rule (deterministic): `MRP = compare_at_price if compare_at_price else price`; `ISP = price`. Both formatted as integer-or-decimal string. `SKUCode` and `vendorArticleNumber` also set from sku; `productDisplayName` set from title.
  - Validation: for any target header present in `template.vocab_by_header`, the value must validate; on failure → blank cell + `Flag`. Headers listed in `template.vocab_by_header` that receive no value AND are core saree attributes are recorded in `blanks`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mapper.py
from src.mapper import validate_value, map_product
from src.models import Product, TemplateInfo

def _template():
    return TemplateInfo(
        headers=["vendorSkuCode","vendorArticleName","SKUCode","vendorArticleNumber",
                 "productDisplayName","MRP","ISP","tags","Product Details",
                 "Prominent Colour","Saree Fabric","articleType","Country Of Origin","brand","Occasion"],
        header_row=3, first_data_row=4,
        col_index_by_header={h:i+1 for i,h in enumerate(
            ["vendorSkuCode","vendorArticleName","SKUCode","vendorArticleNumber",
             "productDisplayName","MRP","ISP","tags","Product Details",
             "Prominent Colour","Saree Fabric","articleType","Country Of Origin","brand","Occasion"])},
        vocab_by_header={
            "Prominent Colour": ["Red","Blue","Green"],
            "Saree Fabric": ["Pure Silk","Art Silk"],
            "articleType": ["Sarees"],
            "Country Of Origin": ["India"],
            "Occasion": ["Party","Festive"],
        },
    )

def test_validate_value_canonicalizes_case():
    assert validate_value(" blue ", ["Red","Blue"]) == "Blue"
    assert validate_value("silk", ["Pure Silk","Art Silk"]) is None

def test_map_product_fills_identity_and_pricing():
    p = Product(handle="h", sku="S1", title="Blue Saree", vendor="Ijor Ethnic",
                tags="Saree, Silk", body_html="<p>nice</p>", price=3199.0,
                compare_at_price=3499.0, color="Blue", fabric="silk",
                size=None, status="active", images=[])
    cmap = {"title":"vendorArticleName","sku":"vendorSkuCode","tags":"tags",
            "body_html":"Product Details","color":"Prominent Colour","fabric":"Saree Fabric"}
    consts = {"articleType":"Sarees","Country Of Origin":"India","brand":"Ijor"}
    row = map_product(p, _template(), cmap, consts)
    assert row.cells["vendorSkuCode"] == "S1"
    assert row.cells["MRP"] == "3499"
    assert row.cells["ISP"] == "3199"
    assert row.cells["Prominent Colour"] == "Blue"     # canonicalized, valid
    assert row.cells["articleType"] == "Sarees"
    assert row.cells["Country Of Origin"] == "India"
    # 'silk' is not a valid Saree Fabric -> blank + flag
    assert "Saree Fabric" not in row.cells
    assert any(f.field == "Saree Fabric" for f in row.flags)
    # Occasion has no source -> recorded as blank
    assert "Occasion" in row.blanks
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mapper.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.mapper'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/mapper.py
from src.models import MappedRow, Flag

def _fmt_num(x):
    """Format a float as integer string when whole, else trimmed decimal."""
    if x is None:
        return None
    if float(x).is_integer():
        return str(int(x))
    return ("%f" % x).rstrip("0").rstrip(".")

def validate_value(value, vocab):
    if value is None:
        return None
    key = str(value).strip().lower()
    for v in vocab:
        if str(v).strip().lower() == key:
            return v
    return None

# Shopify Product attribute -> raw value getter
def _shopify_value(product, field_key):
    return getattr(product, field_key, None)

def _set(row, template, header, value):
    """Set a cell, validating against vocab if the header is vocab-controlled."""
    if value is None or str(value).strip() == "":
        return
    if header in template.vocab_by_header:
        canon = validate_value(value, template.vocab_by_header[header])
        if canon is None:
            row.flags.append(Flag(sku=row.sku, field=header,
                                  reason="value not in Myntra dropdown", value=str(value)))
            return
        row.cells[header] = str(canon)
    else:
        row.cells[header] = str(value)

def map_product(product, template, column_map, constants):
    row = MappedRow(sku=product.sku)

    # 1. constants
    for header, val in constants.items():
        _set(row, template, header, val)

    # 2. direct column-map copies
    for field_key, header in column_map.items():
        _set(row, template, header, _shopify_value(product, field_key))

    # 3. derived identity duplicates
    _set(row, template, "SKUCode", product.sku)
    _set(row, template, "vendorArticleNumber", product.sku)
    _set(row, template, "productDisplayName", product.title)

    # 4. deterministic pricing
    mrp = product.compare_at_price if product.compare_at_price else product.price
    _set(row, template, "MRP", _fmt_num(mrp))
    _set(row, template, "ISP", _fmt_num(product.price))

    # 5. record vocab-controlled headers left blank (manual / Phase 2 fill)
    for header in template.vocab_by_header:
        if header not in row.cells:
            row.blanks.append(header)

    return row
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_mapper.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/mapper.py tests/test_mapper.py
git commit -m "feat: map Shopify products to Myntra cells with vocab validation"
```

---

## Task 6: images — flatten transparency + convert to JPG

**Files:**
- Create: `src/images.py`
- Test: `tests/test_images.py`

**Interfaces:**
- Consumes: `Product`, `ImageResult` from models; `image_specs.yaml`.
- Produces:
  - `flatten_to_jpg(img:PIL.Image.Image, quality:int, out_path:str) -> None` — composites onto white if it has alpha, saves JPEG.
  - `validate_image(path:str, specs:dict) -> str|None` — returns failure reason or `None` if OK.
  - `process_images(product:Product, specs:dict, out_dir:str, fetch=<callable>) -> ImageResult` — for each url (up to `max_images`), fetch bytes via `fetch(url)->bytes`, open with PIL, flatten→JPG named `SKU_n.jpg`, validate, populate `ImageResult`. `fetch` is injected so tests don't hit the network.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_images.py
import os
from PIL import Image
from src.images import flatten_to_jpg, validate_image, process_images
from src.models import Product

def test_flatten_transparency_not_black(tmp_path):
    # fully transparent RGBA image; must become white, not black, as JPG
    img = Image.new("RGBA", (800, 800), (0, 0, 0, 0))
    out = tmp_path / "t.jpg"
    flatten_to_jpg(img, 90, str(out))
    jpg = Image.open(out).convert("RGB")
    assert jpg.getpixel((400, 400)) == (255, 255, 255)
    assert out.suffix == ".jpg"

def test_validate_min_dimensions(tmp_path):
    small = tmp_path / "s.jpg"
    Image.new("RGB", (100, 100), (255, 0, 0)).save(small, "JPEG")
    specs = {"min_width": 700, "min_height": 700, "max_bytes": 10485760}
    reason = validate_image(str(small), specs)
    assert reason is not None and "dimension" in reason.lower()

def test_process_images_names_and_counts(tmp_path):
    # build an in-memory PNG with alpha and serve it via fake fetch
    import io
    buf = io.BytesIO()
    Image.new("RGBA", (1000, 1000), (10, 20, 30, 255)).save(buf, "PNG")
    data = buf.getvalue()
    def fake_fetch(url): return data
    p = Product(handle="h", sku="S1", title="t", vendor="v", tags="", body_html="",
                price=1.0, compare_at_price=None, color=None, fabric=None,
                size=None, status="active", images=["u1", "u2"])
    specs = {"min_width":700,"min_height":700,"max_bytes":10485760,"quality":90,"max_images":7}
    res = process_images(p, specs, str(tmp_path), fetch=fake_fetch)
    assert os.path.basename(res.jpgs[0]) == "S1_1.jpg"
    assert os.path.basename(res.jpgs[1]) == "S1_2.jpg"
    assert len(res.passed) == 2
    assert res.failed == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_images.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.images'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/images.py
import io
import os
from PIL import Image
from src.models import ImageResult

def _http_fetch(url):
    import requests
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.content

def flatten_to_jpg(img, quality, out_path):
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, rgba)
    img = img.convert("RGB")
    img.save(out_path, "JPEG", quality=quality)

def validate_image(path, specs):
    size = os.path.getsize(path)
    if size > specs["max_bytes"]:
        return f"file size {size} exceeds max {specs['max_bytes']}"
    with Image.open(path) as im:
        w, h = im.size
    if w < specs["min_width"] or h < specs["min_height"]:
        return f"dimensions {w}x{h} below minimum {specs['min_width']}x{specs['min_height']}"
    return None

def process_images(product, specs, out_dir, fetch=_http_fetch):
    os.makedirs(out_dir, exist_ok=True)
    res = ImageResult(sku=product.sku)
    max_images = specs.get("max_images", 7)
    quality = specs.get("quality", 90)
    for i, url in enumerate(product.images[:max_images], start=1):
        name = f"{product.sku}_{i}.jpg"
        out_path = os.path.join(out_dir, name)
        try:
            data = fetch(url)
            with Image.open(io.BytesIO(data)) as im:
                flatten_to_jpg(im, quality, out_path)
        except Exception as e:  # download/convert failure
            res.failed.append((name, f"convert error: {e}"))
            continue
        reason = validate_image(out_path, specs)
        res.jpgs.append(out_path)
        if reason:
            res.failed.append((name, reason))
        else:
            res.passed.append(out_path)
    return res
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_images.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/images.py tests/test_images.py
git commit -m "feat: image download, transparency flatten, JPG convert and validate"
```

---

## Task 7: fill — write mapped rows + image refs into Sarees sheet

**Files:**
- Create: `src/fill.py`
- Test: `tests/test_fill.py`

**Interfaces:**
- Consumes: `TemplateInfo`, `MappedRow`, `ImageResult`; `read_template`.
- Produces: `fill_template(template_path:str, template:TemplateInfo, rows:list[tuple[MappedRow, ImageResult]], out_path:str) -> None`. Opens the template with openpyxl, writes each row's cells into the Sarees sheet starting at `first_data_row`, writes the passing image JPG basenames into the 7 image columns (`Front Image`..`Additional Image 2`) in order, saves to `out_path`. (Dropdown preservation deferred.)
- Image column order list: `["Front Image","Side Image","Back Image","Detail Angle","Look Shot Image","Additional Image 1","Additional Image 2"]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_fill.py
import os, warnings, openpyxl
from src.template_reader import read_template
from src.models import MappedRow, ImageResult
from src.fill import fill_template

TEMPLATE = "Myntra-Sku-Template-2026-06-16.xlsx"

def test_fill_writes_rows(tmp_path):
    warnings.filterwarnings("ignore")
    t = read_template(TEMPLATE)
    r1 = MappedRow(sku="S1", cells={"vendorSkuCode":"S1","vendorArticleName":"Blue Saree",
                                    "MRP":"3499","ISP":"3199","articleType":"Sarees"})
    img = ImageResult(sku="S1", jpgs=["S1_1.jpg"], passed=["/x/S1_1.jpg"], failed=[])
    out = tmp_path / "filled.xlsx"
    fill_template(TEMPLATE, t, [(r1, img)], str(out))
    assert os.path.exists(out)
    wb = openpyxl.load_workbook(out)
    ws = wb["Sarees"]
    row = t.first_data_row
    sku_col = t.col_index_by_header["vendorSkuCode"]
    name_col = t.col_index_by_header["vendorArticleName"]
    front_col = t.col_index_by_header["Front Image"]
    assert ws.cell(row=row, column=sku_col).value == "S1"
    assert ws.cell(row=row, column=name_col).value == "Blue Saree"
    assert ws.cell(row=row, column=front_col).value == "S1_1.jpg"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_fill.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.fill'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/fill.py
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
        # passing images -> image columns in order
        passing_basenames = [os.path.basename(p) for p in images.passed]
        for header, basename in zip(IMAGE_COLUMNS, passing_basenames):
            col = template.col_index_by_header.get(header)
            if col:
                ws.cell(row=r, column=col, value=basename)
        r += 1

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    wb.save(out_path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_fill.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add src/fill.py tests/test_fill.py
git commit -m "feat: write mapped rows and image refs into Sarees sheet"
```

---

## Task 8: report — emit report.txt

**Files:**
- Create: `src/report.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `MappedRow`, `ImageResult`.
- Produces: `write_report(rows:list[tuple[MappedRow,ImageResult]], out_path:str) -> str`. Writes a human-readable `report.txt`: per-SKU filled-field count, blanks, vocab flags, and image pass/fail; plus a summary header. Returns the report text.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_report.py
from src.models import MappedRow, ImageResult, Flag
from src.report import write_report

def test_report_lists_flags_and_blanks(tmp_path):
    r = MappedRow(sku="S1", cells={"MRP":"3499","ISP":"3199"},
                  flags=[Flag(sku="S1", field="Saree Fabric", reason="not in dropdown", value="silk")],
                  blanks=["Occasion","Pattern"])
    img = ImageResult(sku="S1", jpgs=["S1_1.jpg","S1_2.jpg"], passed=["S1_1.jpg"],
                      failed=[("S1_2.jpg","dimensions 100x100 below minimum")])
    out = tmp_path / "report.txt"
    text = write_report([(r, img)], str(out))
    assert "S1" in text
    assert "Saree Fabric" in text
    assert "Occasion" in text
    assert "dimensions 100x100" in text
    assert (tmp_path / "report.txt").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_report.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.report'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/report.py
def write_report(rows, out_path):
    lines = []
    total_flags = sum(len(m.flags) for m, _ in rows)
    total_img_fail = sum(len(i.failed) for _, i in rows)
    lines.append("=== Myntra Phase 1 Fill Report ===")
    lines.append(f"Products: {len(rows)} | vocab flags: {total_flags} | image failures: {total_img_fail}")
    lines.append("")
    for m, img in rows:
        lines.append(f"SKU {m.sku}: {len(m.cells)} fields filled, {len(img.passed)} images OK")
        for f in m.flags:
            lines.append(f"  [FLAG] {f.field}: {f.reason} (value={f.value!r})")
        if m.blanks:
            lines.append(f"  [BLANK] left empty (manual/Phase 2): {', '.join(m.blanks)}")
        for name, reason in img.failed:
            lines.append(f"  [IMAGE FAIL] {name}: {reason}")
        lines.append("")
    text = "\n".join(lines)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_report.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add src/report.py tests/test_report.py
git commit -m "feat: emit fill report with flags, blanks, image failures"
```

---

## Task 9: run.py orchestrator + end-to-end run

**Files:**
- Create: `run.py`, `README.md`
- Test: `tests/test_end_to_end.py`

**Interfaces:**
- Consumes: all modules above.
- Produces: `main(template_path, csv_path, out_dir, config_dir, fetch=None) -> dict` returning `{"filled": <xlsx path>, "report": <txt path>, "products": N}`. CLI entry `python run.py` uses defaults: template + CSV from repo root (or `input/`), outputs to `output/`. Resolves input paths by checking `input/` first, then repo root.

- [ ] **Step 1: Write the failing test** (offline — inject fake fetch returning a real PNG)

```python
# tests/test_end_to_end.py
import io, os, openpyxl, warnings
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
    # row 4 (first data row) should have a SKU written in vendorSkuCode (col 3)
    assert ws.cell(row=4, column=3).value not in (None, "")
    # at least one converted image on disk
    assert any(f.endswith(".jpg") for f in os.listdir(out_dir / "images"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_end_to_end.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'run'` (or main missing)

- [ ] **Step 3: Write minimal implementation**

```python
# run.py
import os
import sys
import yaml
from src.template_reader import read_template
from src.shopify_reader import read_products
from src.mapper import map_product
from src.images import process_images
from src.fill import fill_template
from src.report import write_report

def _resolve(name):
    """Prefer input/<name>, else repo-root <name>."""
    cand = os.path.join("input", name)
    return cand if os.path.exists(cand) else name

def main(template_path=None, csv_path=None, out_dir="output", config_dir="config", fetch=None):
    template_path = template_path or _resolve("Myntra-Sku-Template-2026-06-16.xlsx")
    csv_path = csv_path or _resolve("products_export.csv")

    column_map = yaml.safe_load(open(os.path.join(config_dir, "column_map.yaml")))
    constants = yaml.safe_load(open(os.path.join(config_dir, "constants.yaml")))
    specs = yaml.safe_load(open(os.path.join(config_dir, "image_specs.yaml")))

    template = read_template(template_path)
    products = read_products(csv_path)

    images_dir = os.path.join(out_dir, "images")
    os.makedirs(images_dir, exist_ok=True)

    rows = []
    for p in products:
        mapped = map_product(p, template, column_map, constants)
        if fetch is None:
            img = process_images(p, specs, images_dir)
        else:
            img = process_images(p, specs, images_dir, fetch=fetch)
        rows.append((mapped, img))

    filled_path = os.path.join(out_dir, "myntra_filled.xlsx")
    fill_template(template_path, template, rows, filled_path)

    report_path = os.path.join(out_dir, "report.txt")
    write_report(rows, report_path)

    return {"filled": filled_path, "report": report_path, "products": len(products)}

if __name__ == "__main__":
    res = main()
    print(f"Filled: {res['filled']}")
    print(f"Report: {res['report']}")
    print(f"Products: {res['products']}")
```

`README.md`:

```markdown
# Myntra Bulk-Listing Automation — Phase 1

Deterministic fill: Shopify saree CSV + Myntra DIY template -> filled Sarees sheet + JPG images.

## Run
1. Put `products_export.csv` and `Myntra-Sku-Template-2026-06-16.xlsx` in `input/` (or repo root).
2. `pip install -r requirements.txt`
3. `python run.py`

Outputs to `output/`: `myntra_filled.xlsx`, `images/SKU_n.jpg`, `report.txt`.

## Notes
- No LLM/API/DB (that is Phase 2).
- Attribute fields Shopify lacks are left blank and listed in `report.txt`.
- Dropdown-validation preservation in the output file is a deferred decision.

## Test
`python -m pytest -v`
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_end_to_end.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Run the full suite + real run, then commit**

```bash
python -m pytest -v
python run.py
git add run.py README.md tests/test_end_to_end.py
git commit -m "feat: end-to-end orchestrator and README"
```

Note: `python run.py` (Step 5) performs real image downloads from Shopify URLs. If offline, the run still produces `myntra_filled.xlsx` and `report.txt` with image-download failures flagged — the sheet rows still fill.

---

## Self-Review

**Spec coverage:**
- Folder contract → Tasks 1–9 (run.py, src/, config/, output/). ✓
- Read template headers + hidden dropdowns → Task 2 (x14 parsing). ✓
- Read CSV, group variants under parent → Task 3. ✓
- Map columns per config, no guessing → Tasks 4–5. ✓
- Validate against allowed-value list, flag mismatches → Task 5 + Task 8. ✓
- Convert images, flatten transparency, WebP→JPG, validate, name → Task 6. ✓
- Write filled template preserving structure → Task 7 (dropdown preservation deferred per owner). ✓
- Emit report.txt → Task 8. ✓
- Deliberately NOT: attribute invention (blanks+flag, Task 5), persistent dedup (none), Myntra verification (none). ✓
- Acceptance: filled fields valid vocab (Task 5), images valid JPG named (Task 6), every blank/flag in report (Task 8), end-to-end (Task 9). ✓

**Placeholder scan:** No TBD/TODO; all steps contain real code. Dropdown preservation is an explicit deferred decision, not a placeholder.

**Type consistency:** `MappedRow.cells/flags/blanks`, `ImageResult.jpgs/passed/failed`, `TemplateInfo.col_index_by_header/vocab_by_header/first_data_row`, `validate_value`, `map_product`, `process_images(fetch=...)`, `fill_template`, `write_report`, `main(...)` — names consistent across tasks 1–9.
