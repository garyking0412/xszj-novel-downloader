"""站内搜索代理。

站点行为（实测）：GET /s/?keyword=xxx 不触发 Cloudflare，但会返回站内图形
验证码页（<title>验证码</title>）。流程：
1. GET /s/?keyword= → 验证码页（Cookie 由共享 httpx client 保持）
2. POST /code → JSON {CId, Image(data-uri base64)}
3. 用户在前端输入验证码
4. GET /s/?keyword=xx&id={CId}&code={code} → 结果页；验证码错误则回到 1

若未来搜索页改为 Cloudflare 拦截，则尝试 Playwright 渲染兜底。
"""
import json
import logging
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import config
from ..scraper.catalog_playwright import CatalogUnavailable, fetch_rendered_html
from ..scraper.http import CloudflareBlocked, FetchError, get_http
from ..scraper.parser import is_captcha_page, parse_search_page

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


class VerifyIn(BaseModel):
    keyword: str
    cid: str
    code: str


async def _fetch_captcha() -> dict:
    """POST /code 获取验证码，返回 {cid, image}。"""
    text = await get_http().post(f"{config.SITE_ORIGIN}/code")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise HTTPException(502, f"验证码接口返回异常: {text[:100]}")
    return {"cid": str(data.get("CId", "")), "image": data.get("Image", "")}


async def _search_url(keyword: str, cid: str = "", code: str = "") -> str:
    url = f"{config.SITE_ORIGIN}/s/?keyword={quote(keyword)}"
    if cid:
        url += f"&id={quote(cid)}&code={quote(code)}"
    return url


async def _get_search_page(url: str) -> str:
    try:
        return await get_http().get(url)
    except CloudflareBlocked:
        log.info("搜索页被 Cloudflare 拦截，尝试 Playwright: %s", url)
        try:
            return await fetch_rendered_html(url, wait_selector="a[href*='/b/']", timeout=40)
        except CatalogUnavailable as e:
            raise HTTPException(
                502, f"搜索页被 Cloudflare 拦截，且 Playwright 不可用（{e}）。"
                     f"可改用书籍 URL 直接添加。")
    except FetchError as e:
        raise HTTPException(502, f"搜索请求失败: {e}")


def _respond(html: str, keyword: str) -> dict:
    """验证码页 → 返回 need_captcha；结果页 → 解析返回。"""
    if is_captcha_page(html):
        return {"need_captcha": True, "keyword": keyword, "results": []}
    return {"need_captcha": False, "keyword": keyword,
            "results": parse_search_page(html)}


@router.get("/search")
async def search(keyword: str):
    keyword = keyword.strip()
    if not keyword:
        raise HTTPException(400, "keyword 不能为空")
    html = await _get_search_page(await _search_url(keyword))
    resp = _respond(html, keyword)
    if resp["need_captcha"]:
        resp["captcha"] = await _fetch_captcha()
    return resp


@router.post("/search/verify")
async def search_verify(body: VerifyIn):
    keyword = body.keyword.strip()
    if not keyword:
        raise HTTPException(400, "keyword 不能为空")
    html = await _get_search_page(await _search_url(keyword, body.cid, body.code))
    resp = _respond(html, keyword)
    if resp["need_captcha"]:
        # 验证码错误/过期：换一张新图
        resp["captcha"] = await _fetch_captcha()
        resp["error"] = "验证码错误或已过期，请重新输入"
    return resp
