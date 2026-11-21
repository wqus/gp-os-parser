from bs4 import BeautifulSoup

from gp_os.parsers.common import ParseError, finish, parse_tenants, photos, text


def parse(url: str, html: str, **kwargs):
    soup = BeautifulSoup(html, "html.parser")
    fields = {}
    for row in soup.select(".detail-characteristic__item"):
        name = text(row.select_one(".detail-characteristic__item-title"))
        fields.setdefault(name, text(row.select_one(".detail-characteristic__item-text")))
    for row in soup.select(".detail-characteristic__address"):
        spans = row.find_all("span", recursive=False)
        if len(spans) == 2:
            fields[text(spans[0])] = text(spans[1])
    description = text(soup.select_one(".detail-description__text"))
    if fields.get("Тип сделки") == "Аренда":
        raise ParseError("This listing offers rent, not a sale")
    title = text(soup.select_one(".detail-view__title"))
    record = finish(
        "nfgroup",
        url,
        html,
        title=title,
        description=description,
        price_text=text(
            soup.select_one('.detail-price__price-item[data-currency="rur"] .detail-price__price')
            or soup.select_one(".detail-price__price")
        ),
        area_text=fields.get("Площадь в продажу", ""),
        address=" ".join(filter(None, [fields.get("Город"), fields.get("Адрес")])),
        metro=text(soup.select_one(".detail-view__metro")),
        tenants=parse_tenants(description, fields.get("Арендатор", title)),
        rent_text="МАП: " + fields["Доход"] if fields.get("Доход") else "",
        vat=fields.get("НДС", ""),
        photo_urls=photos(soup, url, ".detail-view__slide img"),
        price_location=".detail-price__price-item (RUB)",
        **kwargs,
    )
    if soup.select_one(".detail-view--removed, .detail-view__removed-block"):
        record.status = "removed"
    return record
