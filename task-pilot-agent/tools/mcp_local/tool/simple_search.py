import os
import asyncio
from functools import partial
from typing import Any, List, Optional
from urllib.parse import urlparse

import aiohttp
from bs4 import BeautifulSoup

from config.config import agentSettings, reveal_secret
from tools.mcp_local.model.document import Doc
from tools.mcp_local.tool.search_component.search_engine import MixSearch
from tools.mcp_local.util.log_util import timer
from utils.logger import get_logger


logger = get_logger(__name__)


def _resolve_engines(engines: Optional[List[str]] = None) -> List[str]:
    supported = {"bing", "jina", "sogou", "serp", "serper", "bocha"}
    configured = [
        item.provider
        for item in (agentSettings.search or [])
        if getattr(item, "provider", None)
    ]
    selected = engines or configured or ["jina"]
    normalized = [
        item.strip().lower()
        for item in selected
        if item and item.strip() and item.strip().lower() in supported
    ]
    if not normalized:
        return ["jina"]
    return normalized


def _positive_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        resolved = default
    return max(minimum, min(maximum, resolved))


def _doc_key(doc: Doc) -> str:
    return doc.link or f"{doc.title}\n{doc.content[:200]}"


def _is_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _resolve_jina_reader_api_key() -> str:
    return next(
        (
            reveal_secret(item.api_key)
            for item in (agentSettings.search or [])
            if getattr(item, "provider", None) == "jina"
        ),
        os.getenv("JINA_SEARCH_API_KEY", ""),
    )


class PageContentFetcher:
    """Fetch rendered page text for the final search results only."""

    def __init__(self):
        self.api_key = _resolve_jina_reader_api_key()
        self.timeout = _positive_int(
            os.getenv("WEB_SEARCH_CONTENT_TIMEOUT"),
            os.getenv("SEARCH_TIMEOUT", 30),
            1,
            120,
        )
        self.max_concurrency = _positive_int(
            os.getenv("WEB_SEARCH_CONTENT_CONCURRENCY"),
            5,
            1,
            10,
        )
        self.blacklist_prefixes = ("https://huggingface.co",)

    async def fill(self, docs: list[Doc]) -> list[Doc]:
        if not docs:
            return docs

        semaphore = asyncio.Semaphore(self.max_concurrency)
        async with aiohttp.ClientSession() as session:
            tasks = [
                asyncio.create_task(self._fetch_doc_content(session, doc.link, semaphore))
                for doc in docs
            ]
            fetched = await asyncio.gather(*tasks)

        for doc, (content, source) in zip(docs, fetched):
            doc.data = dict(doc.data or {})
            if content:
                doc.content = content
                doc.data["content_fetched"] = True
                doc.data["content_source"] = source
            else:
                doc.data["content_fetched"] = False
                doc.data.setdefault("content_source", doc.data.get("search_engine", "search_snippet"))
        return docs

    async def fetch_url(self, url: str, content_max_chars: int = 4000) -> dict[str, Any]:
        url_text = (url or "").strip()
        if not url_text:
            return {
                "url": url_text,
                "ok": False,
                "content": "",
                "contentLength": 0,
                "truncated": False,
                "source": "",
                "error": "url is required",
            }
        if not _is_http_url(url_text):
            return {
                "url": url_text,
                "ok": False,
                "content": "",
                "contentLength": 0,
                "truncated": False,
                "source": "",
                "error": "url must start with http:// or https://",
            }

        content_limit = _positive_int(
            content_max_chars,
            os.getenv("WEB_READER_CONTENT_MAX_CHARS", 4000),
            0,
            20000,
        )
        async with aiohttp.ClientSession() as session:
            content, source = await self._fetch_doc_content(
                session,
                url_text,
                asyncio.Semaphore(1),
            )

        content_length = len(content)
        truncated = content_limit > 0 and content_length > content_limit
        if content_limit > 0:
            content = content[:content_limit]
        return {
            "url": url_text,
            "ok": bool(content),
            "content": content,
            "contentLength": content_length,
            "truncated": truncated,
            "source": source,
            "error": "" if content else "content fetch failed",
        }

    async def _fetch_doc_content(
        self,
        session: aiohttp.ClientSession,
        url: str,
        semaphore: asyncio.Semaphore,
    ) -> tuple[str, str]:
        async with semaphore:
            if not url or any(url.startswith(prefix) for prefix in self.blacklist_prefixes):
                return "", ""
            rendered = await self._fetch_with_jina_reader(session, url)
            if rendered:
                return rendered, "jina_reader_browser"
            parsed = await self._fetch_with_http_parser(session, url)
            if parsed:
                return parsed, "http_parser"
            return "", ""

    async def _fetch_with_jina_reader(self, session: aiohttp.ClientSession, url: str) -> str:
        if not self.api_key or self.api_key == "CHANGE_ME":
            return ""

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "X-Engine": "browser",
            "X-Return-Format": "text",
        }
        try:
            async with session.get(
                f"https://r.jina.ai/{url}",
                headers=headers,
                timeout=self.timeout,
            ) as response:
                if response.status != 200:
                    logger.warning(
                        "web_search rendered fetch failed: url=%s status=%s reason=%s",
                        url,
                        response.status,
                        response.reason,
                    )
                    return ""
                return (await response.text()).strip()
        except Exception as exc:  # noqa: BLE001 - fallback to plain parser
            logger.warning("web_search rendered fetch exception: url=%s error=%s", url, exc)
            return ""

    async def _fetch_with_http_parser(self, session: aiohttp.ClientSession, url: str) -> str:
        try:
            async with session.get(url, timeout=self.timeout) as response:
                content_type = (response.headers.get("content-type") or "").split(";")[0].lower()
                if content_type not in {
                    "text/html",
                    "text/plain",
                    "text/xml",
                    "application/json",
                    "application/xml",
                }:
                    logger.warning("web_search parser skipped content-type=%s url=%s", content_type, url)
                    return ""
                text = await response.text()
        except UnicodeDecodeError as exc:
            return exc.args[1].decode("gb2312", errors="ignore")
        except Exception as exc:  # noqa: BLE001 - keep search result when parsing fails
            logger.warning("web_search parser exception: url=%s error=%s", url, exc)
            return ""

        soup = BeautifulSoup(text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        return soup.get_text("\n", strip=True)


class WebSearch:
    """Search pages and fetch rendered content without LLM expansion or synthesis."""

    def __init__(self, engines: Optional[List[str]] = None):
        self.engines = _resolve_engines(engines)
        use_bing = "bing" in self.engines
        use_jina = "jina" in self.engines
        use_sogou = "sogou" in self.engines
        use_serp = "serp" in self.engines or "serper" in self.engines
        use_bocha = "bocha" in self.engines
        self._search_single_query = partial(
            MixSearch().search_and_dedup,
            use_bing=use_bing,
            use_jina=use_jina,
            use_sogou=use_sogou,
            use_serp=use_serp,
            use_bocha=use_bocha,
            fetch_content=False,
        )

    @timer()
    async def run(
        self,
        query: str,
        request_id: Optional[str] = None,
        max_results: int = 5,
        content_max_chars: int = 500,
        fetch_content: bool = True,
    ) -> dict[str, Any]:
        query_text = (query or "").strip()
        if not query_text:
            return {
                "query": query_text,
                "engines": self.engines,
                "count": 0,
                "results": [],
                "error": "query is required",
            }

        limit = _positive_int(
            max_results,
            os.getenv("WEB_SEARCH_MAX_RESULTS", 5),
            1,
            20,
        )
        content_limit = _positive_int(
            content_max_chars,
            os.getenv("WEB_SEARCH_CONTENT_MAX_CHARS", 500),
            0,
            4000,
        )

        docs = await self._search_single_query(query_text, request_id)
        unique_docs: list[Doc] = []
        seen = set()
        for doc in docs:
            if not isinstance(doc, Doc):
                continue
            key = _doc_key(doc)
            if key in seen:
                continue
            unique_docs.append(doc)
            seen.add(key)
            if len(unique_docs) >= limit:
                break

        if fetch_content:
            unique_docs = await PageContentFetcher().fill(unique_docs)

        return {
            "query": query_text,
            "engines": self.engines,
            "fetch_content": fetch_content,
            "count": len(unique_docs),
            "results": [
                {
                    "rank": index,
                    **doc.to_dict(truncate_len=content_limit),
                }
                for index, doc in enumerate(unique_docs, start=1)
            ],
        }


class WebReader:
    """Read one known URL with rendered browser fetch first, then plain HTTP parsing."""

    @timer()
    async def run(
        self,
        url: str,
        request_id: Optional[str] = None,
        content_max_chars: int = 4000,
    ) -> dict[str, Any]:
        result = await PageContentFetcher().fetch_url(url, content_max_chars=content_max_chars)
        result["requestId"] = request_id
        return result
