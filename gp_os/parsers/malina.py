import re

from bs4 import BeautifulSoup

from gp_os.parsers.common import ParseError, finish, labeled, parse_tenants, photos, text


def parse(url: str, html: str, **kwargs):
    soup = BeautifulSoup(html, "html.parser")
    title = text(soup.select_one(".b--item-head__title"))
    if title.casefold().startswith("аренда"):
        raise ParseError("This listing offers rent, not a sale")
    description = text(soup.select_one(".b--item-info__description-container"))
    description = description.partition("ПОПУЛЯРНЫЕ ВОПРОСЫ")[0].strip()
    values = {}
    for row in soup.select(".b--item-info__table-row"):
        key = text(row.select_one(".b--item-info__table-name")).strip(": ")
        values.setdefault(key, text(row.select_one(".b--item-info__table-value")))
    price_text = ""
    for block in soup.select(".b--item-info__number"):
        if "Цена объекта" in text(block):
            price_text = text(block)
            break
    tenant = values.get("Арендатор", "")
    # A compound tenant field is split by the shared parser, not treated as one company.
    rows = None
    if tenant and not re.search(r",|\sи\s", tenant.replace("Красное и Белое", "КБ"), re.I):
        rows = [
            {
                "name": tenant,
                "rent": values.get("Месячная арендная плата"),
                "raw": "\n".join(f"{k}: {v}" for k, v in values.items()),
            }
        ]
    tenants = parse_tenants(description, tenant or title, rows)
    marker = re.search(
        r"Арендатор(?:ы)?\s*:\s*(.*?)(?:\s+-\s+арендная|\bМАП\b|\n)", description, re.I
    )
    if marker and re.search(r",|\sи\s", marker.group(1), re.I):
        tenants = parse_tenants(description, tenant or title)
    commission = "Без комиссии" if re.search("без комиссии", description, re.I) else ""
    return finish(
        "malina",
        url,
        html,
        title=title,
        description=description,
        price_text=price_text,
        area_text=labeled(soup, r"^\s*Площадь\s*$"),
        address=text(soup.select_one(".b--item-head__address")),
        metro=text(soup.select_one(".b--item-head__metro")),
        tenants=tenants,
        rent_text="МАП: " + values["Месячная арендная плата"]
        if values.get("Месячная арендная плата")
        else "",
        commission=commission,
        photo_urls=photos(soup, url, ".b--item-gallery img, .b--item-slider img, a[data-fancybox]"),
        price_location=".b--item-info__number: Цена объекта",
        **kwargs,
    )
