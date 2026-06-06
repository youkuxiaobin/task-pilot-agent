import asyncio

import pytest

from tools.mcp_local.model.document import Doc
from tools.mcp_local.tool import simple_search
from tools.mcp_local.tool.search_component.search_engine import MixSearch
from tools.mcp_local.tool.simple_search import PageContentFetcher, WebReader, WebSearch


@pytest.mark.asyncio
async def test_web_search_returns_rendered_page_content_for_limited_results(monkeypatch):
    calls = []
    fetcher_calls = []

    class FakeMixSearch:
        async def search_and_dedup(self, *args, **kwargs):
            calls.append({"args": args, "kwargs": kwargs})
            return [
                Doc(
                    doc_type="web_page",
                    title="First",
                    link="https://example.test/first",
                    content="abcdefghijklmnopqrstuvwxyz",
                    data={"search_engine": "fake"},
                ),
                Doc(
                    doc_type="web_page",
                    title="First duplicate",
                    link="https://example.test/first",
                    content="duplicate",
                    data={"search_engine": "fake"},
                ),
                Doc(
                    doc_type="web_page",
                    title="Second",
                    link="https://example.test/second",
                    content="second result",
                    data={"search_engine": "fake"},
                ),
            ]

    class FakePageContentFetcher:
        async def fill(self, docs):
            fetcher_calls.append([doc.link for doc in docs])
            for doc in docs:
                doc.content = f"rendered page content for {doc.title}"
                doc.data = {
                    **doc.data,
                    "content_fetched": True,
                    "content_source": "test_renderer",
                }
            return docs

    monkeypatch.setattr(simple_search, "MixSearch", FakeMixSearch)
    monkeypatch.setattr(simple_search, "PageContentFetcher", FakePageContentFetcher)

    result = await WebSearch(engines=["jina"]).run(
        query=" TaskPilot ",
        request_id="request-1",
        max_results=2,
        content_max_chars=21,
    )

    assert calls[0]["args"] == ("TaskPilot", "request-1")
    assert calls[0]["kwargs"]["use_jina"] is True
    assert calls[0]["kwargs"]["fetch_content"] is False
    assert fetcher_calls == [["https://example.test/first", "https://example.test/second"]]
    assert result["query"] == "TaskPilot"
    assert result["fetch_content"] is True
    assert result["count"] == 2
    assert [item["rank"] for item in result["results"]] == [1, 2]
    assert [item["link"] for item in result["results"]] == [
        "https://example.test/first",
        "https://example.test/second",
    ]
    assert result["results"][0]["content"] == "rendered page content"
    assert result["results"][0]["data"]["content_source"] == "test_renderer"


@pytest.mark.asyncio
async def test_web_search_can_skip_page_content_fetch(monkeypatch):
    class FakeMixSearch:
        async def search_and_dedup(self, *args, **kwargs):
            return [
                Doc(
                    doc_type="web_page",
                    title="Only snippet",
                    link="https://example.test/snippet",
                    content="search snippet",
                    data={"search_engine": "fake"},
                )
            ]

    class FakePageContentFetcher:
        async def fill(self, docs):
            raise AssertionError("page content fetch should not be called")

    monkeypatch.setattr(simple_search, "MixSearch", FakeMixSearch)
    monkeypatch.setattr(simple_search, "PageContentFetcher", FakePageContentFetcher)

    result = await WebSearch(engines=["jina"]).run(
        query="TaskPilot",
        fetch_content=False,
    )

    assert result["fetch_content"] is False
    assert result["results"][0]["content"] == "search snippet"


@pytest.mark.asyncio
async def test_web_search_rejects_empty_query_without_calling_engine(monkeypatch):
    class FakeMixSearch:
        async def search_and_dedup(self, *args, **kwargs):
            raise AssertionError("search engine should not be called")

    monkeypatch.setattr(simple_search, "MixSearch", FakeMixSearch)

    result = await WebSearch(engines=["jina"]).run(query="  ")

    assert result["count"] == 0
    assert result["results"] == []
    assert result["error"] == "query is required"


def test_web_search_falls_back_when_engines_are_unsupported():
    assert WebSearch(engines=["unknown"]).engines == ["jina"]
    assert WebSearch(engines=["unknown", "jina"]).engines == ["jina"]


@pytest.mark.asyncio
async def test_page_content_fetcher_prefers_rendered_reader(monkeypatch):
    fetcher = PageContentFetcher()
    calls = []

    async def fake_rendered_reader(session, url):
        calls.append(("rendered", url))
        return "rendered body"

    async def fake_http_parser(session, url):
        calls.append(("http", url))
        return "plain body"

    monkeypatch.setattr(fetcher, "_fetch_with_jina_reader", fake_rendered_reader)
    monkeypatch.setattr(fetcher, "_fetch_with_http_parser", fake_http_parser)

    content, source = await fetcher._fetch_doc_content(
        session=None,
        url="https://example.test/page",
        semaphore=asyncio.Semaphore(1),
    )

    assert content == "rendered body"
    assert source == "jina_reader_browser"
    assert calls == [("rendered", "https://example.test/page")]


@pytest.mark.asyncio
async def test_page_content_fetcher_fetch_url_prefers_rendered_and_truncates(monkeypatch):
    fetcher = PageContentFetcher()
    calls = []

    async def fake_rendered_reader(session, url):
        calls.append(("rendered", url))
        return "rendered body content"

    async def fake_http_parser(session, url):
        calls.append(("http", url))
        return "plain body"

    monkeypatch.setattr(fetcher, "_fetch_with_jina_reader", fake_rendered_reader)
    monkeypatch.setattr(fetcher, "_fetch_with_http_parser", fake_http_parser)

    result = await fetcher.fetch_url(
        "https://example.test/page",
        content_max_chars=8,
    )

    assert result["ok"] is True
    assert result["url"] == "https://example.test/page"
    assert result["content"] == "rendered"
    assert result["contentLength"] == len("rendered body content")
    assert result["truncated"] is True
    assert result["source"] == "jina_reader_browser"
    assert calls == [("rendered", "https://example.test/page")]


@pytest.mark.asyncio
async def test_page_content_fetcher_fetch_url_rejects_non_http_url(monkeypatch):
    fetcher = PageContentFetcher()

    async def fake_rendered_reader(session, url):
        raise AssertionError("reader should not be called for non-http URLs")

    monkeypatch.setattr(fetcher, "_fetch_with_jina_reader", fake_rendered_reader)

    result = await fetcher.fetch_url("file:///tmp/secret.txt")

    assert result["ok"] is False
    assert result["content"] == ""
    assert result["error"] == "url must start with http:// or https://"


@pytest.mark.asyncio
async def test_web_reader_returns_page_content_with_request_id(monkeypatch):
    calls = []

    async def fake_fetch_url(self, url, content_max_chars=4000):
        calls.append({"url": url, "content_max_chars": content_max_chars})
        return {
            "url": url,
            "ok": True,
            "content": "page body",
            "contentLength": 9,
            "truncated": False,
            "source": "test_reader",
            "error": "",
        }

    monkeypatch.setattr(PageContentFetcher, "fetch_url", fake_fetch_url)

    result = await WebReader().run(
        "https://example.test/page",
        request_id="request-reader",
        content_max_chars=12,
    )

    assert result["requestId"] == "request-reader"
    assert result["content"] == "page body"
    assert calls == [{"url": "https://example.test/page", "content_max_chars": 12}]


@pytest.mark.asyncio
async def test_mix_search_propagates_fetch_content_flag_to_engines():
    calls = []

    class FakeEngine:
        _engine = "fake"

        async def search_and_dedup(self, *args, **kwargs):
            calls.append({"args": args, "kwargs": kwargs})
            return [
                Doc(
                    doc_type="web_page",
                    title="Propagated",
                    link="https://example.test/propagated",
                    content="propagated content",
                    data={"search_engine": "fake"},
                )
            ]

    mix_search = MixSearch()
    mix_search._jina_engine = FakeEngine()

    docs = await mix_search.search_and_dedup(
        "TaskPilot",
        "request-1",
        use_jina=True,
        use_bocha=False,
        fetch_content=False,
    )

    assert [doc.link for doc in docs] == ["https://example.test/propagated"]
    assert calls[0]["args"] == ("TaskPilot", "request-1")
    assert calls[0]["kwargs"]["fetch_content"] is False
