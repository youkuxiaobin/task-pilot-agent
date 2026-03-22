import asyncio
import json
import os
from abc import ABC, abstractmethod
from typing import List
import aiohttp
from bs4 import BeautifulSoup
import httpx

from utils.logger import get_logger

from tools.mcp_local.model.document import Doc
from tools.mcp_local.util.log_util import timer
from config.config import agentSettings, reveal_secret

logger = get_logger(__name__)


class SearchBase(ABC):
	"""搜索基类"""

	def __init__(self):
		self._count = int(os.getenv("SEARCH_COUNT", 10))
		self._timeout = int(os.getenv("SEARCH_TIMEOUT", 100))
		self._use_jd_gateway = os.getenv("USE_JD_SEARCH_GATEWAY", "false") == "true"
		self._blacklist_prefixes = [
			"https://huggingface.co",
		]

	@abstractmethod
	async def search(self, query: str, request_id: str = None, *args, **kwargs) -> List[Doc]:
		"""抽象搜索方法"""
		raise NotImplementedError

	@staticmethod
	@timer()
	async def parser(docs: List[Doc], timeout: int = 10, proxy: str | None = None, **kwargs) -> List[Doc]:
		async def _parser(source_url, timeout, proxy):
			async with aiohttp.ClientSession() as session:
				try:
					async with session.get(source_url, timeout=timeout, proxy=proxy or None) as response:
						if response.content_type.lower() in [
								"text/html", "text/plain", "text/xml", "application/json", "application/xml", "application/octet-stream"]:
							return await response.text()
						else:
							# TODO 其他类型暂时不解析
							logger.warning(f"parser content-type[{response.content_type}] not parser: url=[{source_url}]")
							return ""
				except UnicodeDecodeError as ude:
					return ude.args[1].decode("gb2312", errors="ignore")
				except Exception as e:
					logger.warning(f"parser error: url=[{source_url}] error={e}")
					return ""
		tasks = []
		try:
			async with asyncio.TaskGroup() as tg:
				tasks = [tg.create_task(_parser(doc.link, timeout, proxy)) for doc in docs]
		except* Exception as exc_group:
			logger.warning(f"parser TaskGroup errors: {exc_group}")

		raw_results = []
		for doc, task in zip(docs, tasks):
			try:
				raw_results.append(task.result())
			except Exception as exc:  # noqa: BLE001 - log and continue
				logger.warning(f"parser task result error: url=[{doc.link}] error={exc}")
				raw_results.append("")

		results = [BeautifulSoup(result, "html.parser") for result in raw_results]
		results = [soup.get_text() if soup.get_text() and len(soup.get_text().strip()) > 50 else str(soup.text) for soup in results]
		for doc, result in zip(docs, results):
			if result:
				doc.content = result
				logger.debug("parsed content for url=%s content_len=%s", doc.link, len(result))
		return docs

	@timer()
	async def search_and_dedup(
			self, query: str, request_id: str = None, *args, **kwargs
	) -> List[Doc]:
		"""
		搜索并去重，同时删除没有内容的文档
		"""
		docs = await self.search(query=query, request_id=request_id, *args, **kwargs)
		#docs = await self.parser(docs=docs)

		seen_docs = set()
		deduped_docs = []
		for doc in docs:
			if doc.content and doc.content not in seen_docs:
				deduped_docs.append(doc)
				seen_docs.add(doc.content)
		return deduped_docs


class BingSearch(SearchBase):

	def __init__(self):
		super().__init__()
		self._engine = "bing-search"
		self._url = os.getenv("BING_SEARCH_URL")
		self._api_key = os.getenv("BING_SEARCH_API_KEY")

		self.headers = {
			"Content-Type": "application/json",
		}
		self.set_auth()

	def set_auth(self):
		if self._use_jd_gateway:
			self.headers["Authorization"] = f"Bearer {self._api_key}"
		else:
			self.headers["Ocp-Apim-Subscription-Key"] = self._api_key

	def construct_body(self, query: str, request_id: str = None):
		if self._use_jd_gateway:
			return {
				"request_id": request_id,
				"model": self._engine,

				"messages": [{
					"role": "user",
					"content": query
				}],
				"count": self._count,
				"stream": False,
			}
		else:
			return {
				"q": query,
				"textDecorations": True
			}

	async def search(self, query: str, request_id: str = None, *args, **kwargs) -> List[Doc]:
		body = self.construct_body(query, request_id)
		async with aiohttp.ClientSession() as session:
			async with session.post(self._url, json=body, headers=self.headers, timeout=self._timeout) as response:
				result = json.loads(await response.text())
				return [
					Doc(
						doc_type="web_page",
						content=item.get("snippet", ""),
						title=item.get("name", ""),
						link=item.get("url", ""),
						data={"search_engine": self._engine},
					) for item in result.get("webPages", {}).get("value", [])
				]


class JinaSearch(BingSearch):

	def __init__(self):
		super().__init__()
		self._engine = "search_pro_jina"
		#self._url = os.getenv("JINA_SEARCH_URL")
		self._url = "https://s.jina.ai/"
		self._api_key = next(
			(reveal_secret(s.api_key) for s in (agentSettings.search or []) if s.provider == "jina"),
			os.getenv("JINA_SEARCH_API_KEY")
		)

	async def search(self, query: str, request_id: str = None, *args, **kwargs) -> List[Doc]:
		if not self._api_key:
			logger.warning("JinaSearch skipped: missing JINA_SEARCH_API_KEY")
			return []
		headers = {
			"Accept": "application/json",
			"Authorization": f"Bearer {self._api_key}",
			"X-Respond-With": "no-content",
			"X-Engine": "direct",
		}
		try:
			async with aiohttp.ClientSession() as session:
				async with session.get(self._url, params={"q": query}, headers=headers, timeout=self._timeout) as response:
					if response.status != 200:
						logger.warning(
							f"JinaSearch request failed: status={response.status} reason={response.reason}"
						)
						return []
					raw_text = await response.text()
					try:
						result = json.loads(raw_text)
					except json.JSONDecodeError:
						logger.warning(f"JinaSearch returned non-JSON payload: {raw_text}")
						return []
					items = result.get("data") or []
					if not isinstance(items, list):
						logger.warning(f"JinaSearch data payload is not a list: {items}")
						return []
					items = items[: self._count]
					reader_headers = {
						"Authorization": f"Bearer {self._api_key}",
						"X-Engine": "browser",
						"X-Return-Format": "text",
					}
					async def fetch_reader_content(target_url: str) -> str:
						if not target_url:
							return ""
						if any(target_url.startswith(prefix) for prefix in self._blacklist_prefixes):
							return ""
						reader_url = f"https://r.jina.ai/{target_url}"
						try:
							async with session.get(reader_url, headers=reader_headers, timeout=self._timeout) as reader_response:
								if reader_response.status != 200:
									logger.warning(
										f"JinaSearch reader failed: url={target_url} status={reader_response.status} reason={reader_response.reason}"
									)
									return ""
								return await reader_response.text()
						except Exception as exc:
							logger.warning(f"JinaSearch reader exception: url={target_url} error={exc}")
							return ""
					reader_tasks = [asyncio.create_task(fetch_reader_content(item.get("url", ""))) for item in items]
					reader_contents = await asyncio.gather(*reader_tasks)
					docs: List[Doc] = []
					for item, reader_content in zip(items, reader_contents):
						content = (reader_content or "").strip()
						if not content:
							content = item.get("content") or item.get("description") or ""
						docs.append(
							Doc(
								doc_type="web_page",
								content=content,
								title=item.get("title", ""),
								link=item.get("url", ""),
								data={
									"search_engine": self._engine,
								},
							)
						)
					return docs
		except Exception as exc:  # noqa: BLE001 - surface as warning
			logger.warning(f"JinaSearch request exception: query={query} error={exc}")
			return []
class BochaSearch(BingSearch):
	def __init__(self):
		super().__init__()
		self._engine = "bocha_search"
		self._url = "https://api.bochaai.com/v1/web-search"
		self._api_key = next(
			(reveal_secret(s.api_key) for s in (agentSettings.search or []) if s.provider == "bocha"),
			os.getenv("BOCHA_SEARCH_API_KEY")
		)

	async def search(self, query: str, request_id: str = None, *args, **kwargs) -> List[Doc]:
		try:
			payload = {
				"query": query,
				"summary": True,
				"freshness": "noLimit",
				"count": 5
			}

			headers = {
				"Authorization": f"Bearer {self._api_key}",
				"Content-Type": "application/json",
			}

			async with httpx.AsyncClient() as client:
				response = await client.post(
					self._url, headers=headers, json=payload, timeout=10.0
				)

				response.raise_for_status()
				resp = response.json()
				if "data" not in resp:
					return "Search error."
				
				data = resp["data"]

				if "webPages" not in data:
					return "No results found."

		
				return [
					Doc(
						doc_type="web_page",
						content=item.get("summary", ""),
						title=item.get("name", ""),
						link=item.get("url", ""),
						data={"search_engine": self._engine},
					) for item in resp["data"].get("webPages", {}).get("value", [])
				]

		except httpx.HTTPStatusError as e:
			logger.error(
				f"Bocha Web Search API HTTP error occurred: {e.response.status_code} - {e.response.text}"
			)
			return f"Bocha Web Search API HTTP error occurred: {e.response.status_code} - {e.response.text}"
		except httpx.RequestError as e:
			logger.error(f"Error communicating with Bocha Web Search API: {str(e)}")
			return f"Error communicating with Bocha Web Search API: {str(e)}"
		except Exception as e:
			logger.error(f"Unexpected error: {str(e)}")
			return f"Unexpected error: {str(e)}"

class SogouSearch(JinaSearch):

	def __init__(self):
		super().__init__()
		self._engine = "search_pro_sogou"
		self._url = os.getenv("SOGOU_SEARCH_URL")
		self._api_key = os.getenv("SOGOU_SEARCH_API_KEY")


class SerperSearch(SearchBase):

	def __init__(self):
		super().__init__()
		self._engine = "serper"
		self._url = "https://google.serper.dev/search"
		self._proxy = next(
			(
				getattr(s, "proxy", None)
				for s in (agentSettings.search or [])
				if s.provider == "serper" and getattr(s, "proxy", None)
			),
			None,
		) or os.getenv("SERPER_SEARCH_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
		self._api_key = next(
			(reveal_secret(s.api_key) for s in (agentSettings.search or []) if s.provider == "serper"),
			os.getenv("SERPER_SEARCH_API_KEY")
		)
		self.headers = {
			"Content-Type": "application/json",
		}
		self.set_auth()

	def set_auth(self):
		if not self._api_key:
			return
		self.headers["X-API-KEY"] = self._api_key

	def construct_body(self, query: str, request_id: str = None):
		return {
			"q": query,
		}

	async def search(self, query: str, request_id: str = None, *args, **kwargs) -> List[Doc]:
		if not self._api_key:
			logger.warning("SerperSearch skipped: missing SERPER_SEARCH_API_KEY")
			return []
		body = self.construct_body(query, request_id)
		try:
			async with aiohttp.ClientSession() as session:
				async with session.post(
					self._url,
					json=body,
					headers=self.headers,
					timeout=self._timeout,
					proxy=self._proxy or None,
				) as response:
					if response.status != 200:
						logger.warning(f"SerperSearch request failed: status={response.status} reason={response.reason}")
						return []
					raw_text = await response.text()
					try:
						result = json.loads(raw_text)
					except json.JSONDecodeError:
						logger.warning(f"SerperSearch returned non-JSON payload: {raw_text}")
						return []
					organic = result.get("organic") or []
					docs = [
						Doc(
							doc_type="web_page",
							content=item.get("snippet", ""),
							title=item.get("title", ""),
							link=item.get("link", ""),
							data={"search_engine": self._engine},
						) for item in organic[: self._count]
					]
					docs = await self.parser(docs=docs, proxy=self._proxy or None)
					return docs
		except Exception as exc:  # noqa: BLE001 - surface as warning
			logger.warning(f"SerperSearch request exception: query={query} error={exc}")
			return []


class MixSearch(BingSearch):

	def __init__(self):
		super().__init__()
		self._engine = "mix_search"
		self._bing_engine = BingSearch()
		self._jina_engine = JinaSearch()
		self._sogou_engine = SogouSearch()
		self._serp_engine = SerperSearch()
		self._bocha_engine = BochaSearch()
	async def search(
			self, query: str, request_id: str = None,
			use_bing: bool = False, use_jina: bool = False, use_sogou: bool = False,
			use_serp: bool = False, use_bocha: bool = True, *args, **kwargs) -> List[Doc]:
		assert use_bing or use_jina or use_sogou or use_serp or use_bocha
		engines = []
		if use_bing:
			engines.append(self._bing_engine)
		if use_jina:
			engines.append(self._jina_engine)
		if use_sogou:
			engines.append(self._sogou_engine)
		if use_serp:
			engines.append(self._serp_engine)
		if use_bocha:
			engines.append(self._bocha_engine)
		async def _run_engine(engine: SearchBase) -> List[Doc]:
			try:
				return await engine.search_and_dedup(query=query, request_id=request_id)
			except Exception as exc:  # noqa: BLE001 - log and continue
				logger.warning(
					f"MixSearch engine {getattr(engine, '_engine', engine.__class__.__name__)} failed: {exc}"
				)
				return []

		tasks = []
		try:
			async with asyncio.TaskGroup() as tg:
				tasks = [tg.create_task(_run_engine(engine)) for engine in engines]
		except* Exception as exc_group:
			logger.warning(f"MixSearch TaskGroup errors: {exc_group}")

		results = []
		for engine, task in zip(engines, tasks):
			try:
				results.append(task.result())
			except Exception as exc:  # noqa: BLE001 - log and continue
				logger.warning(
					f"MixSearch task result error for {getattr(engine, '_engine', engine.__class__.__name__)}: {exc}"
				)
				results.append([])
		return [doc for docs in results for doc in docs] 
