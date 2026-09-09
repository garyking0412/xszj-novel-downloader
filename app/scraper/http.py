"""httpx 异步客户端封装：UA、超时、指数退避重试、礼貌延时、Cloudflare 检测。"""
import asyncio
import logging
import random

import httpx

from .. import config

log = logging.getLogger(__name__)


class CloudflareBlocked(Exception):
    """页面返回 Cloudflare 人机验证（managed challenge），普通 HTTP 无法通过。"""

    def __init__(self, url: str):
        self.url = url
        super().__init__(f"Cloudflare challenge: {url}")


class FetchError(Exception):
    """重试耗尽后的抓取失败。"""


def is_cloudflare_challenge(text: str) -> bool:
    head = text[:3000]
    return "Just a moment" in head and "_cf_chl" in text


class HttpClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            headers={
                "User-Agent": config.USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
            timeout=config.REQUEST_TIMEOUT,
            follow_redirects=True,
        )

    async def get(self, url: str) -> str:
        """GET 一个页面并返回 HTML 文本；网络错误重试，CF 拦截立即抛出。"""
        return await self._request("GET", url)

    async def post(self, url: str) -> str:
        """POST（空 body），返回响应文本。Cookie 由共享 client 自动保持。"""
        return await self._request("POST", url)

    async def _request(self, method: str, url: str) -> str:
        last_exc: Exception | None = None
        for attempt in range(config.MAX_RETRIES):
            if attempt:
                backoff = 2 ** (attempt + 1)  # 4s, 8s
                log.warning("重试 %s（第 %d 次，%ds 后）：%s",
                            url, attempt, backoff, last_exc)
                await asyncio.sleep(backoff)
            try:
                resp = await self._client.request(method, url)
                resp.raise_for_status()
                text = resp.text
                if is_cloudflare_challenge(text):
                    raise CloudflareBlocked(url)
                await self._polite_delay()
                return text
            except (CloudflareBlocked, httpx.HTTPStatusError) as e:
                # CF 拦截与 4xx/5xx 状态码：不重试，直接失败
                if isinstance(e, CloudflareBlocked):
                    raise
                last_exc = e
                status = e.response.status_code
                if 400 <= status < 500 and status != 429:
                    raise FetchError(f"HTTP {status}: {url}") from e
            except httpx.HTTPError as e:
                last_exc = e
        raise FetchError(f"抓取失败（已重试 {config.MAX_RETRIES} 次）: {url}") from last_exc

    async def _polite_delay(self) -> None:
        await asyncio.sleep(random.uniform(
            config.REQUEST_DELAY_MIN, config.REQUEST_DELAY_MAX))

    async def close(self) -> None:
        await self._client.aclose()


_http: HttpClient | None = None


def init_http() -> None:
    global _http
    _http = HttpClient()


def get_http() -> HttpClient:
    if _http is None:
        init_http()
    assert _http is not None
    return _http


async def close_http() -> None:
    global _http
    if _http is not None:
        await _http.close()
        _http = None
