"""Playwright（可选依赖）：用无头浏览器过 Cloudflare 抓完整目录页 /cs/N。

未安装 playwright / 未装浏览器 / challenge 未通过时抛 CatalogUnavailable，
调用方（pipeline）自动降级为顺序遍历模式。
"""
import asyncio
import logging

from .. import config
from .parser import parse_catalog_page

log = logging.getLogger(__name__)

# 目录页上等待章节链接出现的选择器（Cloudflare 转圈页面上不存在这些元素）
_LIST_SELECTOR = "#list a[href*='/c/'], a[href*='/c/']"
_CHALLENGE_TIMEOUT = 40  # 秒：managed challenge 自动通过通常需要几秒到几十秒


class CatalogUnavailable(Exception):
    """无法通过 Playwright 获取完整目录（原因见 message）。"""


def _playwright_available() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


async def fetch_rendered_html(url: str, wait_selector: str | None = None,
                              timeout: int = _CHALLENGE_TIMEOUT) -> str:
    """用无头浏览器打开 url，等 Cloudflare challenge 自动通过后返回 HTML。

    供目录页与搜索页共用。失败抛 CatalogUnavailable。
    优先使用完整版 Chromium 的新无头模式（channel="chromium"，反检测能力
    远强于 chromium-headless-shell），未安装时回退默认无头壳。
    """
    if not _playwright_available():
        raise CatalogUnavailable("playwright 未安装（uv sync --extra playwright && "
                                 "uv run playwright install chromium）")
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:  # pragma: no cover
        raise CatalogUnavailable(f"playwright 导入失败: {e}")

    launch_args = ["--disable-blink-features=AutomationControlled"]

    async with async_playwright() as p:
        browser = None
        for kwargs in ({"channel": "chromium"}, {}):
            try:
                browser = await p.chromium.launch(headless=True, args=launch_args, **kwargs)
                break
            except Exception as e:
                last_err = e
        if browser is None:
            raise CatalogUnavailable(
                f"chromium 启动失败（请先 uv run playwright install chromium --no-shell）: {last_err}")
        try:
            ctx = await browser.new_context(
                user_agent=config.USER_AGENT, locale="zh-CN",
                viewport={"width": 1366, "height": 900})
            # 抹掉最常见的自动化指纹
            await ctx.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
            page = await ctx.new_page()
            try:
                await page.goto(url, timeout=(timeout + 15) * 1000,
                                wait_until="domcontentloaded")
            except Exception as e:
                raise CatalogUnavailable(f"页面打开失败 {url}: {e}")
            if wait_selector:
                try:
                    await page.wait_for_selector(wait_selector, timeout=timeout * 1000)
                except Exception:
                    title = await page.title()
                    raise CatalogUnavailable(
                        f"Cloudflare challenge 未通过（title={title!r}）: {url}")
            html = await page.content()
            return html
        finally:
            await browser.close()


async def fetch_catalog(book_id: int) -> list[dict]:
    """抓取整本书的完整目录，返回 [{cid, title, order}]（order 从 1 开始）。"""
    all_entries: list[dict] = []
    seen: set[int] = set()
    page_no = 1
    max_pages = 200  # 保险丝

    while page_no and page_no <= max_pages:
        url = f"{config.SITE_ORIGIN}/b/{book_id}/cs/{page_no}"
        log.info("Playwright 抓取目录页: %s", url)
        html = await fetch_rendered_html(url, wait_selector=_LIST_SELECTOR)
        entries, next_page = parse_catalog_page(html, book_id)
        new = 0
        for e in entries:
            if e["cid"] not in seen:
                seen.add(e["cid"])
                all_entries.append(e)
                new += 1
        if new == 0:
            break  # 没有新章节，翻页到头（或解析失效）
        page_no = next_page if next_page and next_page != page_no else None
        if page_no:
            await asyncio.sleep(1.5)  # 浏览器模式也保持礼貌

    if not all_entries:
        raise CatalogUnavailable(f"目录页解析结果为空: book {book_id}")

    for i, e in enumerate(all_entries, 1):
        e["order"] = i
    log.info("目录获取成功: book %s 共 %d 章", book_id, len(all_entries))
    return all_entries
