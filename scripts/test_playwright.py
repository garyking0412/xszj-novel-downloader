"""手工验证脚本（不属于应用运行时）：
1. Playwright 能否过 Cloudflare 拿到 /cs/ 完整目录
2. 站内搜索页是否被拦截、解析是否正常
用法: uv run python scripts/test_playwright.py
"""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

from app.scraper.catalog_playwright import CatalogUnavailable, fetch_catalog, fetch_rendered_html
from app.scraper.parser import parse_search_page
from app import config


async def test_catalog():
    print("=" * 20, "目录页 Playwright 测试", "=" * 20)
    try:
        catalog = await fetch_catalog(490683)
        print(f"✅ 目录获取成功: {len(catalog)} 章")
        print("前3章:", [(e["order"], e["cid"], e["title"]) for e in catalog[:3]])
        print("末3章:", [(e["order"], e["cid"], e["title"]) for e in catalog[-3:]])
        return catalog
    except CatalogUnavailable as e:
        print(f"❌ CatalogUnavailable: {e}")
        return None


async def test_search():
    print("=" * 20, "搜索页测试", "=" * 20)
    from app.scraper.http import CloudflareBlocked, init_http, get_http, close_http
    from urllib.parse import quote
    init_http()
    url = f"{config.SITE_ORIGIN}/s/?keyword={quote('弃白猫')}"
    html = None
    try:
        html = await get_http().get(url)
        print("✅ 搜索页普通 HTTP 可访问")
    except CloudflareBlocked:
        print("⚠️ 搜索页被 Cloudflare 拦截，尝试 Playwright…")
        try:
            html = await fetch_rendered_html(url, wait_selector="a[href*='/b/']", timeout=40)
            print("✅ Playwright 拿到了搜索页")
        except CatalogUnavailable as e:
            print(f"❌ Playwright 也失败: {e}")
    finally:
        await close_http()
    if html:
        results = parse_search_page(html)
        print(f"解析到 {len(results)} 条结果:")
        for r in results[:8]:
            print("  ", r)


async def main():
    catalog = await test_catalog()
    if catalog:
        # 与数据库已有章节对比，验证目录完整性
        from app import db
        db.init_db()
        have = db.existing_cids(490683)
        cat_cids = {e["cid"] for e in catalog}
        print(f"DB 已有 {len(have)} 章；目录 {len(cat_cids)} 章；"
              f"目录缺失 {len(have - cat_cids)}；DB 缺失 {len(cat_cids - have)}")
        assert have == cat_cids, "目录与顺序爬取结果不一致！"
        print("✅ 目录 cid 集合与顺序爬取完全一致")
    await test_search()


asyncio.run(main())
