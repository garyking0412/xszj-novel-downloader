"""前端浏览器验证：加载各视图、捕获控制台错误、截图。
用法: uv run python scripts/test_frontend.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = "http://127.0.0.1:8000"
OUT = Path("/tmp/xszj_ui")
OUT.mkdir(exist_ok=True)

ERRORS: list[str] = []


async def main():
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 900})
        page.on("console", lambda m: ERRORS.append(f"[console.{m.type}] {m.text}")
                if m.type == "error" else None)
        page.on("pageerror", lambda e: ERRORS.append(f"[pageerror] {e}"))

        # 书架
        await page.goto(BASE, wait_until="networkidle")
        await page.wait_for_selector(".book-card", timeout=10000)
        cards = await page.locator(".book-card").count()
        print(f"书架卡片数: {cards}")
        await page.screenshot(path=str(OUT / "1_shelf.png"), full_page=True)

        # 书籍详情
        await page.click(".book-card h3 a")
        await page.wait_for_selector(".catalog li", timeout=10000)
        n = await page.locator(".catalog li").count()
        print(f"目录条目数: {n}")
        await page.screenshot(path=str(OUT / "2_book.png"), full_page=True)

        # 阅读页
        await page.click(".catalog li:first-child a")
        await page.wait_for_selector(".chapter-body p", timeout=10000)
        paras = await page.locator(".chapter-body p").count()
        title = await page.locator(".chapter-title").inner_text()
        print(f"阅读页: {title} | 段落 {paras}")
        await page.screenshot(path=str(OUT / "3_reader.png"), full_page=False)

        # 下一章
        await page.click(".reader-nav button.primary")
        await page.wait_for_timeout(1200)
        title2 = await page.locator(".chapter-title").inner_text()
        print(f"下一章: {title2}")

        # 搜索页（会出现验证码框）
        await page.goto(BASE + "/#/search", wait_until="networkidle")
        await page.fill(".add-bar input", "弃白猫")
        await page.click(".add-bar button")
        await page.wait_for_selector(".captcha-box, .search-item", timeout=15000)
        has_captcha = await page.locator(".captcha-box").count()
        print(f"搜索页验证码框: {has_captcha}")
        await page.screenshot(path=str(OUT / "4_search.png"), full_page=True)

        await browser.close()

    if ERRORS:
        print("❌ 控制台/页面错误:")
        for e in ERRORS:
            print("  ", e)
        sys.exit(1)
    print("✅ 前端无控制台错误，截图已存 /tmp/xszj_ui/")


asyncio.run(main())
