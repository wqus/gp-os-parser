import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit


@dataclass(frozen=True)
class SourceSpec:
    name: str
    host: str
    pattern: str
    catalog: str


SOURCES = {
    "intermark": SourceSpec(
        "Intermark",
        "intermark.ru",
        r"/objects/[a-zA-Z0-9-]+-(\d+)",
        "https://intermark.ru/kommercheskaya-nedvizhimost/prodazha",
    ),
    "malina": SourceSpec(
        "Malina Property",
        "malinaproperty.ru",
        r"/property/propertiesRetail/(\d+)",
        "https://malinaproperty.ru/property/rentbis",
    ),
    "invest7": SourceSpec(
        "Invest7", "invest7.ru", r"/lots/(\d+)", "https://invest7.ru/investments"
    ),
    "s5d": SourceSpec("S5D", "s5d.ru", r"/object/(\d+)", "https://www.s5d.ru/"),
    "nfgroup": SourceSpec(
        "NF Group",
        "kf.expert",
        r"/retail/(?:[a-zA-Z0-9-]+/)*[a-zA-Z0-9-]+-([sr]\d+)",
        "https://kf.expert/retail/rental-business/investirovanie",
    ),
}


def normalize_url(source: str, url: str, *, listing: bool = True) -> str:
    spec = SOURCES[source]
    parts = urlsplit(url.strip())
    if (
        parts.scheme != "https"
        or parts.hostname not in (spec.host, f"www.{spec.host}")
        or parts.port not in (None, 443)
        or parts.username is not None
        or parts.password is not None
    ):
        raise ValueError(f"Only public HTTPS URLs on {spec.host} are accepted")
    path = parts.path.rstrip("/") or "/"
    if listing and re.fullmatch(spec.pattern, path) is None:
        raise ValueError(f"Not a {spec.name} listing URL: {path}")
    return urlunsplit(("https", spec.host, path, parts.query if not listing else "", ""))


def listing_id(source: str, url: str) -> str:
    normalized = normalize_url(source, url)
    return re.fullmatch(SOURCES[source].pattern, urlsplit(normalized).path).group(1)
