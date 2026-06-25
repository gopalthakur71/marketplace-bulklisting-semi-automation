import re
import warnings
import zipfile

from src.myntra.template_reader import read_template
from src.core.models import MappedRow, ImageResult
from src.myntra.fill import fill_template

TEMPLATE = "templates/myntra/Myntra-Sku-Template-2026-06-16.xlsx"


def _count_x14_validations(xlsx_path):
    with zipfile.ZipFile(xlsx_path) as z:
        xml = z.read("xl/worksheets/sheet2.xml").decode("utf-8")
    return len(re.findall(r"<x14:dataValidation\b", xml))


def test_output_preserves_dropdowns(tmp_path):
    warnings.filterwarnings("ignore")
    original = _count_x14_validations(TEMPLATE)
    assert original == 37
    t = read_template(TEMPLATE)
    row = MappedRow(sku="S1", cells={"vendorSkuCode": "S1", "brand": "Ijor Ethnic Partners"})
    img = ImageResult(sku="S1")
    out = tmp_path / "filled.xlsx"
    fill_template(TEMPLATE, t, [(row, img)], str(out), preserve_dropdowns=True)
    assert _count_x14_validations(str(out)) == 37


def test_upload_file_has_no_dropdowns_by_default(tmp_path):
    """Default output must be clean (no x14 validations) so Myntra's parser reads it."""
    warnings.filterwarnings("ignore")
    t = read_template(TEMPLATE)
    row = MappedRow(sku="S1", cells={"vendorSkuCode": "S1"})
    out = tmp_path / "filled.xlsx"
    fill_template(TEMPLATE, t, [(row, ImageResult(sku="S1"))], str(out))
    assert _count_x14_validations(str(out)) == 0
