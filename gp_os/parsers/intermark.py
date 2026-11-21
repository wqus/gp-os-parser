import json
import re

from bs4 import BeautifulSoup

from gp_os.parsers.common import finish, text


def parse(url: str, html: str, **kwargs):
    soup = BeautifulSoup(html, "html.parser")
    title = text(soup.select_one(".object-detail h1"))
    description = text(soup.select_one(".object-detail .with-styled-list"))
    area = re.search(r"([\d., ]+)\s*кв\.\s*м", title)
    address = re.split(r"кв\.\s*м\s*,", title, maxsplit=1)
    metro = ""
    for item in soup.select(".object-detail li"):
        if text(item).startswith("Метро:"):
            metro = text(item).partition(":")[2].strip()
    images = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.get_text())
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict) and data.get("@type") == "Product":
            images = data.get("image", [])
    return finish(
        "intermark",
        url,
        html,
        title=title,
        description=description,
        price_text=text(soup.select_one(".price-text1")),
        area_text=area.group(1) if area else "",
        address=address[1].strip() if len(address) == 2 else "",
        metro=metro,
        photo_urls=images,
        price_location=".price-text1",
        tenant_hint=text(soup.find("title")),
        **kwargs,
    )
