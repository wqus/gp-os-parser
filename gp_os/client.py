import asyncio
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from gp_os.sources import listing_id, normalize_url


class SiteClient:
    def __init__(
        self, *, timeout: float = 20, delay: float = 0.5, retries: int = 1, transport=None
    ):
        self.timeout = timeout
        self.delay = delay
        self.retries = retries
        self.transport = transport
        self.session = None

    async def __aenter__(self):
        self.session = httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=False,
            transport=self.transport,
            headers={"User-Agent": "GP-OS-Collector/0.1 (public property listing monitoring)"},
        )
        return self

    async def __aexit__(self, *_):
        await self.session.aclose()

    async def fetch(self, source: str, url: str, *, listing: bool = True) -> httpx.Response:
        def request_url(value):
            normalize_url(source, value, listing=listing)
            parts = urlsplit(value)
            return urlunsplit(
                ("https", parts.netloc, parts.path, parts.query if not listing else "", "")
            )

        current = request_url(url)
        expected_id = listing_id(source, current) if listing else None
        for attempt in range(self.retries + 1):
            try:
                for _ in range(5):
                    await asyncio.sleep(self.delay)
                    response = await self.session.get(current)
                    if response.is_redirect:
                        current = request_url(urljoin(current, response.headers["location"]))
                        if listing and listing_id(source, current) != expected_id:
                            raise ValueError("Redirect points to a different listing ID")
                        continue
                    if response.status_code in (404, 410):
                        return response
                    response.raise_for_status()
                    if len(response.content) > 15_000_000:
                        raise ValueError("Page is larger than 15 MB")
                    content_type = response.headers.get("content-type", "")
                    if content_type and "html" not in content_type.lower():
                        raise ValueError("The response is not an HTML page")
                    return response
                raise ValueError("Too many redirects")
            except (httpx.TransportError, httpx.HTTPStatusError):
                if attempt == self.retries:
                    raise
                await asyncio.sleep(1)
        raise RuntimeError("No request attempt executed")
