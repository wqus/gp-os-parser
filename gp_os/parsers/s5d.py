import re

from bs4 import BeautifulSoup

from gp_os.parsers.common import ParseError, finish, photos, text


def parse(url: str, html: str, **kwargs):
    soup = BeautifulSoup(html, "html.parser")
    if re.search(r"месяц|/мес", text(soup.select_one(".price-article")), re.I):
        raise ParseError("This listing offers rent, not a sale")
    section = soup.select_one(".article-top-section")
    paragraphs = section.find_all("p") if section else []
    description = "\n".join(text(p) for p in paragraphs if len(text(p)) > 160)
    params = text(soup.select_one(".article-parameter-section"))

    def field(pattern):
        match = re.search(pattern, params, re.I)
        return match.group(1).strip() if match else ""

    return finish(
        "s5d",
        url,
        html,
        title=text(soup.find("h1")),
        description=description,
        price_text=text(soup.select_one(".price-article")),
        area_text=field(r"Общая площадь:\s*([\d., ]+)"),
        address=text(soup.select_one(".article-top-section .address")),
        metro=field(r"Близость метро:\s*(.*?)\s*Транспортная"),
        rent_text=field(r"(Общий МАП:\s*[\d., ]+\s*(?:RUB)?)"),
        vat=field(r"(НДС\s*(?:включен|не включен|не облагается|не применяется))"),
        ownership_form=field(r"ФОРМА ВЛАДЕНИЯ:\s*(.*?)\s*Общий МАП"),
        photo_urls=photos(soup, url, '.article-top-section img[src*="/images-"]'),
        price_location=".price-article",
        **kwargs,
    )
