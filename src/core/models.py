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
    passed: list[str] = field(default_factory=list)        # local JPG paths that passed
    passed_urls: list[str] = field(default_factory=list)   # CDN URLs that passed (written to sheet)
    failed: list[tuple] = field(default_factory=list)


@dataclass
class TemplateInfo:
    headers: list[str]
    header_row: int
    first_data_row: int
    col_index_by_header: dict[str, int]
    vocab_by_header: dict[str, list[str]]
