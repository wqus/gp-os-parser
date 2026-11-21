import re

from bs4 import BeautifulSoup

from gp_os.parsers.common import finish, labeled, parse_tenants, photos, text


def parse(url: str, html: str, **kwargs):
    soup = BeautifulSoup(html, "html.parser")
    for element in soup.select("script, style, svg"):
        element.decompose()
    headings = [node for node in soup.find_all(["h2", "h3", "h4"]) if text(node) == "Описание"]
    section = headings[-1].parent if headings else None
    description = "\n".join(text(p) for p in section.find_all("p")) if section else ""
    rows = []
    for row in soup.select("table tbody tr"):
        cells = row.find_all("td")
        if len(cells) >= 5:
            rows.append(
                {
                    "name": text(cells[0]),
                    "rent": text(cells[1]),
                    "term": text(cells[3]).strip("- ") or None,
                    "indexation": text(cells[4]).strip("- ") or None,
                    "raw": text(row),
                }
            )
    address = soup.find(
        string=re.compile(r"^\s*(?:г Москва,|Московская обл,|г Московская|г Щербинка,)")
    )
    named_pharmacy = re.search(r'арендатор\s*[:—-]\s*(аптека\s*["«][^"»]+["»])', description, re.I)
    if named_pharmacy:
        for row in rows:
            if row["name"].casefold() == "аптека":
                row["name"] = named_pharmacy.group(1)
    metro = ""
    if address:
        metro_nodes = address.parent.parent.select('span[aria-hidden="true"]')
        metro = ", ".join(dict.fromkeys(text(node.parent) for node in metro_nodes))
    title = text(soup.find("h1"))
    rent_node = soup.find(string=re.compile(r"^\s*МАП\s*$"))
    rent = text(rent_node.parent.parent) if rent_node else ""
    price_text = labeled(soup, r"^Цена\s*(?:\([^)]*\))?\s*:")
    vat = re.search(r"Цена\s*\(([^)]+)\)", price_text)
    return finish(
        "invest7",
        url,
        html,
        title=title,
        description=description,
        price_text=price_text,
        area_text=labeled(soup, r"^Площадь\s*:"),
        address=str(address).strip() if address else "",
        metro=metro,
        tenants=parse_tenants(description, title, rows),
        rent_text=rent,
        vat=vat.group(1) if vat else "",
        photo_urls=photos(soup, url, '[class*="embla__slide"] img'),
        price_location="price label (excluding per-m² price)",
        **kwargs,
    )
