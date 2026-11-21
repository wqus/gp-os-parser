import calendar
import hashlib
import re
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, quote, urljoin, urlsplit

from bs4 import BeautifulSoup

from gp_os.models import Discrepancy, Evidence, Listing, RentStep, Tenant
from gp_os.sources import listing_id, normalize_url

NUMBER = r"\d+(?:[ \u00a0\u202f]\d{3})*(?:[.,]\d+)?"
BRANDS = (
    r"Красное\s*(?:и|&)?\s*Белое|Пят[её]рочка|Магнит(?:\s+у\s+дома)?|Дикси|"
    r"Перекр[её]сток|Вкус[Вв]илл|Здесь\s+Аптека|Доктор\s+Столетов|Аптека\s+Столетов|"
    r"Аптека\s+Ригла|Ригла|Горздрав|Здравсити|Винлаб|Бристоль|Ермолино|Ozon|Озон|"
    r"Wildberries|Улыбка\s+Радуги|Здрасте|French\s+Bakery|Аптека\s*36[,.\/]6|Аптека\s+Апрель"
)
RENT_LABEL = r"(?:общий\s+МАП|МАП|месячная\s+арендная\s+плата|арендная\s+плата\s+в\s+месяц)"


class ParseError(ValueError):
    pass


def clean(value: str) -> str:
    return re.sub(r"[ \t\r\f\v\u00a0\u202f]+", " ", value).strip()


def text(node) -> str:
    return clean(node.get_text(" ", strip=True)) if node else ""


def amount(value: str | None, *, money: bool = True) -> Decimal | None:
    if not value or re.search(r"по\s+запросу|договорн", value, re.I):
        return None
    match = re.search(NUMBER, value)
    if not match:
        return None
    token = re.sub(r"\s", "", match.group())
    if money and re.fullmatch(r"\d{1,3}(?:\.\d{3})+", token):
        token = token.replace(".", "")
    try:
        result = Decimal(token.replace(",", "."))
    except InvalidOperation:
        return None
    if not result.is_finite() or result < 0:
        return None
    suffix = value[match.end() :].lstrip().lower()
    if money:
        if suffix.startswith("млн"):
            result *= 1_000_000
        elif suffix.startswith("тыс"):
            result *= 1_000
    return result


def labeled(soup: BeautifulSoup, pattern: str) -> str:
    node = soup.find(string=re.compile(pattern, re.I))
    return text(node.parent.parent) if node else ""


def iso_dates(value: str) -> list[date]:
    result = []
    for day, month, year in re.findall(
        r"(?<!\d)(\d{1,2})[./](\d{1,2})[./](\d{4}|\d{2})(?!\d)", value
    ):
        try:
            result.append(date(int(year) + (2000 if len(year) == 2 else 0), int(month), int(day)))
        except ValueError:
            continue
    return result


def sentences(value: str, pattern: str) -> str | None:
    pieces = re.split(r"\n+|(?<=[.!?])\s+(?=[А-ЯA-Z])", value)
    matches = [clean(piece) for piece in pieces if re.search(pattern, piece, re.I)]
    return "\n".join(dict.fromkeys(matches)) or None


def future_tenant(value: str) -> bool:
    return bool(
        re.search(
            r"предварительн\w*\s+договор|\bПДА\b|будущ\w*\s+арендатор|ключи\s*:|открытие\s+магазина\s*:|ввод[^.\n]{0,60}20\d{2}",
            value,
            re.I,
        )
    )


def rent_values(value: str) -> tuple[Decimal | None, Decimal | None, list[RentStep]]:
    current, future = None, None
    steps = []
    for match in re.finditer(
        rf"(?:\bС\s*)?(\d+)\s*(?:[-–]\s*(\d+|го))?\s*месяц\w*\s*[:—-]?\s*({NUMBER})\s*руб",
        value,
        re.I,
    ):
        steps.append(
            RentStep(
                amount_rub=amount(match.group(3)),
                from_month=int(match.group(1)),
                to_month=int(match.group(2))
                if match.group(2) and match.group(2).isdigit()
                else None,
                raw=match.group(),
            )
        )
    stage_pattern = (
        rf"{RENT_LABEL}\s*(?:[:—-]\s*)?с\s*(\d+)\s*(?:[-–]\s*(?:го|ого))?"
        rf"\s*месяц\w*[^\n.;]{{0,35}}?({NUMBER})\s*(?:руб|₽|RUB)"
    )
    for match in re.finditer(stage_pattern, value, re.I):
        future = amount(match.group(2))
        steps.append(RentStep(amount_rub=future, from_month=int(match.group(1)), raw=match.group()))
    for match in re.finditer(
        rf"{RENT_LABEL}\s*[:=—-]?\s*({NUMBER})(?:\s*(млн|тыс)\.?\s*)?", value, re.I
    ):
        number = amount(match.group(1) + (match.group(2) or ""))
        tail = value[match.end() : match.end() + 65]
        if tail.lstrip().startswith("%"):
            minimum = re.search(rf"не\s+менее\s+({NUMBER})\s*руб", tail, re.I)
            if minimum:
                current = amount(minimum.group(1))
            continue
        year = re.match(
            r"\s*(?:руб\w*|₽|RUB)?\s*со?\s*(?:2[-–]?(?:го|ого)?|второго)\s*года", tail, re.I
        )
        month = re.match(
            r"\s*(?:руб\w*|₽|RUB)?\s*с\s*(\d+)\s*(?:[-–](?:го|ого))?\s*месяц", tail, re.I
        )
        if year or month:
            future = number
            steps.append(
                RentStep(
                    amount_rub=number,
                    from_month=13 if year else int(month.group(1)),
                    raw=match.group() + (year or month).group(),
                )
            )
        else:
            current = number
    return current, future, steps


def tenant_details(
    name: str | None, context: str, row: dict | None = None, *, future: bool = False
) -> Tenant:
    row = row or {}
    current, upcoming, steps = rent_values(context)
    table_rent = amount(row.get("rent"))
    if table_rent is not None and not steps:
        current = table_rent
    is_future = future or future_tenant(context)
    if is_future:
        upcoming = upcoming if upcoming is not None else current
        current = None
    contract = row.get("term") or sentences(context, r"договор|\bДДА\b|\bКДА\b|\bДА\b")
    contract_dates = iso_dates(contract or "")
    indexation = row.get("indexation") or sentences(context, "индексац")
    indexation = indexation if indexation and indexation.strip(" -—") else None
    turnover = sentences(context, r"РТО|товарооборот|\bТО\b|от\s+оборота")
    return Tenant(
        name=name,
        status="future" if is_future else "current",
        lease_start=contract_dates[0] if contract_dates else None,
        lease_end=contract_dates[1] if len(contract_dates) > 1 else None,
        lease_term_raw=contract,
        indexation_raw=indexation,
        rent_current_rub=current,
        rent_future_rub=upcoming,
        rent_steps=steps,
        rate_start_raw=steps[0].raw if steps else None,
        turnover_formula_raw=turnover,
        source_excerpt=clean(context),
    )


def parse_tenants(description: str, hint: str = "", rows: list[dict] | None = None) -> list[Tenant]:
    future = future_tenant(description)
    if rows:
        result = []
        for row in rows:
            name = row["name"]
            position = description.casefold().find(name.casefold())
            context = description[position:] if position >= 0 else description
            # Table values belong to this tenant; an aggregate is never divided among tenants.
            if len(rows) > 1:
                positions = [
                    description.casefold().find(other["name"].casefold(), position + len(name))
                    for other in rows
                    if other is not row
                ]
                end = min((p for p in positions if p >= 0), default=len(description))
                context = description[max(position, 0) : end] if position >= 0 else ""
            context = context + "\n" + row.get("raw", "")
            result.append(tenant_details(name, context, row, future=future))
        return result
    markers = list(re.finditer(r"Арендатор(?:ы)?\s*[:—-]", description, re.I))
    if len(markers) > 1:
        result = []
        for index, match in enumerate(markers):
            end = markers[index + 1].start() if index + 1 < len(markers) else len(description)
            for tenant in parse_tenants(description[match.start() : end]):
                if future:
                    tenant.status = "future"
                    tenant.rent_future_rub = tenant.rent_future_rub or tenant.rent_current_rub
                    tenant.rent_current_rub = None
                if not any(item.name == tenant.name for item in result):
                    result.append(tenant)
        return result
    marker = markers[0] if markers else None
    scope = description[marker.end() :] if marker else hint
    matches = list(re.finditer(BRANDS, scope, re.I))
    names = list(dict.fromkeys(match.group() for match in matches))
    if marker:
        first_line = re.split(
            r"\n|\.\s+(?=[А-ЯA-Z])|\bМАП\b|\bДДА\b", scope, maxsplit=1, flags=re.I
        )[0]
        if "," in first_line or re.search(
            r"\sи\s", first_line.replace("Красное и Белое", "КБ"), re.I
        ):
            protected = re.search(r"Красное\s*(?:и|&)\s*Белое", first_line, re.I)
            safe_line = first_line
            if protected:
                safe_line = (
                    first_line[: protected.start()] + "__KIB__" + first_line[protected.end() :]
                )
            pieces = re.split(r"\s*,\s*|\s+и\s+", safe_line)
            if protected:
                pieces = [piece.replace("__KIB__", protected.group()) for piece in pieces]
            candidates = [
                re.split(r"\s+[-—]\s+", piece, maxsplit=1)[0].strip(" .:1234567890")
                for piece in pieces
            ]
            candidates = [
                piece
                for piece in candidates
                if piece
                and not re.search(
                    r"арендная|месячн|договор|МАП|индексац|^КДА|^ДДА|получен", piece, re.I
                )
            ]
            if len(candidates) > 1:
                names = candidates
    if not names and marker:
        name = re.split(r"[,;\n]|\bМАП\b|\bДДА\b", scope, maxsplit=1)[0].strip(" .1234567890")
        names = [name] if name else []
    if not names:
        return []
    tenants = []
    for index, name in enumerate(names):
        start = scope.casefold().find(name.casefold())
        end = (
            scope.casefold().find(names[index + 1].casefold(), start + len(name))
            if index + 1 < len(names)
            else len(scope)
        )
        context = scope[start:end] if marker and start >= 0 else description
        if marker and len(names) > 1:
            tenant_rent = re.search(
                rf"МАП\s+(?:{re.escape(name)}|Аптека(?=\s+\d))\s*[:—-]?\s*({NUMBER})\s*руб",
                description,
                re.I,
            )
            if tenant_rent:
                context += "\nМАП: " + tenant_rent.group(1) + " руб."
        tenant = tenant_details(name, context, future=future)
        if marker and len(names) > 1 and not re.search(RENT_LABEL, context, re.I):
            tenant.rent_current_rub = None
        tenants.append(tenant)
    return tenants


def resolve_steps(tenant: Tenant, today: date) -> None:
    if tenant.status == "future" or not tenant.rent_steps:
        return
    latest_start = tenant.lease_start
    if latest_start is None:
        months = (
            "января февраля марта апреля мая июня июля августа сентября октября ноября декабря"
        ).split()
        match = re.search(
            r"(?:от|с)\s+(" + "|".join(months) + r")\s+(20\d{2})", tenant.source_excerpt, re.I
        )
        if match:
            month = months.index(match.group(1).lower()) + 1
            year = int(match.group(2))
            latest_start = date(year, month, calendar.monthrange(year, month)[1])
    if latest_start is None:
        return
    elapsed = (today.year - latest_start.year) * 12 + today.month - latest_start.month
    minimum_month = elapsed + (today.day >= latest_start.day)
    applicable = [
        step for step in tenant.rent_steps if step.from_month and step.from_month <= minimum_month
    ]
    if applicable:
        current = max(applicable, key=lambda step: step.from_month)
        tenant.rent_current_rub = current.amount_rub
        following = [
            step
            for step in tenant.rent_steps
            if step.from_month and step.from_month > minimum_month
        ]
        tenant.rent_future_rub = (
            min(following, key=lambda step: step.from_month).amount_rub if following else None
        )


def photos(soup: BeautifulSoup, url: str, selectors: str) -> list[str]:
    result = []
    for node in soup.select(selectors):
        value = node.get("data-src") or node.get("src") or node.get("href")
        if value and not value.startswith(("data:", "javascript:")):
            absolute = urljoin(url, value)
            if "/_next/image?" in absolute:
                absolute = parse_qs(urlsplit(absolute).query).get("url", [absolute])[0]
            if absolute.startswith("https://"):
                parts = urlsplit(absolute)
                base_host = urlsplit(url).hostname.removeprefix("www.")
                if parts.hostname and parts.hostname.removeprefix("www.") == base_host:
                    absolute = parts._replace(netloc=base_host).geturl()
                result.append(absolute)
    return list(dict.fromkeys(result))


def finish(
    source: str,
    url: str,
    html: str,
    *,
    title: str,
    description: str,
    price_text: str,
    area_text: str,
    address: str,
    metro: str = "",
    tenants: list[Tenant] | None = None,
    tenant_hint: str = "",
    rent_text: str = "",
    vat: str = "",
    commission: str = "",
    owner: str = "",
    ownership_form: str = "",
    photo_urls: list[str] | None = None,
    collected_at: datetime | None = None,
    price_location: str = "listing price",
    area_location: str = "listing area",
) -> Listing:
    if not title or not description:
        raise ParseError(
            "Listing title/description missing; possible layout change or access challenge"
        )
    if len(description) < 30:
        raise ParseError("Listing description is too short to verify this is a real listing")
    if not price_text:
        raise ParseError("Price widget is missing; refusing to replace previously verified data")
    timestamp = collected_at or datetime.now(UTC)
    tenants = tenants if tenants is not None else parse_tenants(description, tenant_hint or title)
    for tenant in tenants:
        if tenant.lease_start and tenant.lease_start > timestamp.date():
            tenant.status = "future"
            tenant.rent_future_rub = tenant.rent_future_rub or tenant.rent_current_rub
            tenant.rent_current_rub = None
        resolve_steps(tenant, timestamp.date())
    current, future, _ = rent_values(rent_text or description)
    if future_tenant(description) or (tenants and all(t.status == "future" for t in tenants)):
        future, current = future if future is not None else current, None
    actual = [tenant.rent_current_rub for tenant in tenants]
    upcoming = [tenant.rent_future_rub for tenant in tenants]
    conflicts = []
    if actual and all(value is not None for value in actual):
        total = sum(actual, Decimal(0))
        if current is not None and current != total:
            conflicts.append(
                Discrepancy(
                    field="rent_current_rub",
                    values=[str(current), str(total)],
                    explanation=(
                        "Общий МАП отличается от суммы платежей арендаторов; "
                        "сохранён общий МАП источника."
                    ),
                )
            )
        elif current is None:
            current = total
    if upcoming and all(value is not None for value in upcoming) and future is None:
        future = sum(upcoming, Decimal(0))
    if len(tenants) == 1 and current is not None and not tenants[0].rent_steps:
        if tenants[0].status == "current" and tenants[0].rent_current_rub is None:
            tenants[0].rent_current_rub = current
    if len(tenants) > 1 and not rent_text:
        current = sum(actual, Decimal(0)) if all(value is not None for value in actual) else None
        future = sum(upcoming, Decimal(0)) if all(value is not None for value in upcoming) else None
    price = amount(price_text)
    area = amount(area_text, money=False)
    if area == 0:
        area = None
    description_area = re.search(
        rf"(?:помещения?|площадь(?:ю)?|помещение)\s*[:—-]?\s*({NUMBER})\s*(?:м2|м²|кв\.?\s*м)",
        description,
        re.I,
    )
    if description_area:
        precise = amount(description_area.group(1), money=False)
        if area is not None and precise is not None and area != precise:
            conflicts.append(
                Discrepancy(
                    field="area_m2",
                    values=[str(area), str(precise)],
                    explanation=(
                        "Площадь в виджете и тексте различается; значения сохранены для проверки."
                    ),
                )
            )
            if precise % 1 and abs(area - precise) < 1:
                area = precise
    evidence = [
        Evidence(
            field="price_rub", text=clean(price_text) or "Цена не указана", location=price_location
        )
    ]
    if rent_text:
        evidence.append(Evidence(field="rent", text=clean(rent_text), location="rent summary"))
    for index, tenant in enumerate(tenants):
        evidence.append(
            Evidence(
                field=f"tenants[{index}]",
                text=tenant.source_excerpt,
                location="tenant table / description",
            )
        )
    description_price = re.search(
        rf"(?:Стоимость(?:\s+объекта)?|Цена(?:\s+объекта)?)\s*[:—-]?\s*({NUMBER}\s*(?:млн|тыс)?\.?\s*(?:руб\w*|₽|RUB))",
        description,
        re.I,
    )
    if description_price:
        alternate = amount(description_price.group(1))
        evidence.append(
            Evidence(field="price_rub", text=description_price.group(), location="description")
        )
        if alternate is not None and price is not None and alternate != price:
            conflicts.append(
                Discrepancy(
                    field="price_rub",
                    values=[str(price), str(alternate)],
                    explanation="Цена в шапке и описании различается; приоритет у шапки.",
                )
            )
    return Listing(
        source=source,
        listing_id=listing_id(source, url),
        url=normalize_url(source, url),
        collected_at=timestamp,
        last_successful_check_at=timestamp,
        title=title,
        description=description,
        price_rub=price,
        area_m2=area,
        address=address or None,
        map_url="https://yandex.ru/maps/?text=" + quote(address) if address else None,
        map_url_generated=bool(address),
        metro=metro or None,
        vat=vat or None,
        commission=commission or None,
        owner=owner or None,
        ownership_form=ownership_form or None,
        photos=photo_urls or [],
        tenants=tenants,
        rent_current_rub=current,
        rent_future_rub=future,
        payback_years=(price / (current * 12)).quantize(Decimal("0.01"))
        if price and current
        else None,
        evidence=evidence,
        discrepancies=conflicts,
        warnings=["Текущий МАП не подтверждён; окупаемость не рассчитана."]
        if current is None
        else [],
        html_sha256=hashlib.sha256(html.encode("utf-8")).hexdigest(),
    )
