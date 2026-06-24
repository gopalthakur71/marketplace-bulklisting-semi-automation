from src.models import MappedRow, ImageResult, Flag
from src.report import write_report


def test_report_lists_flags_and_blanks(tmp_path):
    r = MappedRow(sku="S1", cells={"MRP": "3499", "ISP": "3199"},
                  flags=[Flag(sku="S1", field="Saree Fabric", reason="not in dropdown", value="silk")],
                  blanks=["Occasion", "Pattern"])
    img = ImageResult(sku="S1", jpgs=["S1_1.jpg", "S1_2.jpg"], passed=["S1_1.jpg"],
                      failed=[("S1_2.jpg", "dimensions 100x100 below minimum")])
    out = tmp_path / "report.txt"
    text = write_report([(r, img)], str(out))
    assert "S1" in text
    assert "Saree Fabric" in text
    assert "Occasion" in text
    assert "dimensions 100x100" in text
    assert (tmp_path / "report.txt").exists()
