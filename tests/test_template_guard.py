import pytest
import yaml

from src.core.models import TemplateInfo
from src.myntra.template_reader import read_template
from src.myntra.template_guard import (assert_template_compatible,
                                       TemplateIncompatibleError)

V13 = "templates/myntra/Myntra-Sku-Template-2026-07-24.xlsx"


def test_guard_passes_for_v13_with_real_config():
    t = read_template(V13)
    cmap = yaml.safe_load(open("config/myntra/column_map.yaml", encoding="utf-8"))
    consts = yaml.safe_load(open("config/myntra/constants.yaml", encoding="utf-8"))
    assert_template_compatible(t, cmap, consts)  # must not raise


def test_guard_raises_listing_missing_headers():
    t = TemplateInfo(headers=["brand"], header_row=3, first_data_row=4,
                     col_index_by_header={"brand": 1}, vocab_by_header={})
    with pytest.raises(TemplateIncompatibleError) as exc:
        assert_template_compatible(t, {"title": "vendorArticleName"}, {"brand": "Ijor"})
    assert "vendorArticleName" in str(exc.value)
