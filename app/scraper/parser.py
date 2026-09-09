"""页面解析：书籍页 / 章节页 / 目录页 / 搜索页。"""
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .. import config

# 章节标题尾部的分页标记，如"第1章 xxx （1/2）"
_PAGE_MARK_RE = re.compile(r"\s*[（(]\s*\d+\s*/\s*\d+\s*[)）]\s*$")
_CHAPTER_HREF_RE = re.compile(r"/b/(\d+)/c/(\d+)")


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def _og(soup: BeautifulSoup, prop: str) -> str | None:
    tag = soup.find("meta", attrs={"property": prop})
    if tag and tag.get("content"):
        return tag["content"].strip()
    return None


def parse_book_id(url: str) -> int:
    """从任意站内 URL（/b/{id}、/b/{id}/c/{cid}、带 query）提取 book_id。"""
    path = urlparse(url).path
    m = re.search(r"/b/(\d+)", path)
    if not m:
        raise ValueError(f"无法从 URL 解析书籍 ID（应形如 {config.SITE_ORIGIN}/b/490683/）: {url}")
    return int(m.group(1))


def parse_chapter_id(url: str) -> int | None:
    m = re.search(r"/c/(\d+)", urlparse(url).path)
    return int(m.group(1)) if m else None


def _clean_intro(text: str) -> str:
    """书籍页 #intro 常为「截断版…完整版」拼接，去掉重复的截断前缀。"""
    text = text.strip()
    if "…" in text:
        head, _, rest = text.partition("…")
        if rest and head and rest.startswith(head[:min(20, len(head))]):
            return rest.strip()
    return text


def parse_book_page(html: str, book_id: int) -> dict:
    """解析书籍信息页，返回可直接传给 db.upsert_book_meta 的 dict。"""
    soup = _soup(html)

    title = _og(soup, "og:novel:book_name")
    author = _og(soup, "og:novel:author")
    status = _og(soup, "og:novel:status") or ""
    category = _og(soup, "og:novel:category") or ""
    cover_url = _og(soup, "og:image")
    site_update_time = _og(soup, "og:novel:update_time")
    read_url = _og(soup, "og:novel:read_url") or ""
    latest_url = _og(soup, "og:novel:latest_chapter_url") or ""
    intro = _og(soup, "og:description") or ""

    # DOM 兜底
    if not title:
        h1 = soup.select_one("#info h1")
        title = h1.get_text(strip=True) if h1 else ""
    if not author:
        a = soup.select_one("#info p a[href^='/z/']")
        author = a.get_text(strip=True) if a else ""
    if not status:
        info = soup.select_one("#info")
        if info:
            m = re.search(r"(完本|连载中|连载)", info.get_text())
            status = m.group(1) if m else ""
    if not intro:
        div = soup.select_one("#intro")
        intro = div.get_text(" ", strip=True) if div else ""
    if not cover_url:
        img = soup.select_one("#fmimg img")
        if img and img.get("src"):
            cover_url = urljoin(config.SITE_ORIGIN, img["src"])
    if not read_url:
        a = soup.select_one(".readbtn a[href*='/c/']")
        if a:
            read_url = urljoin(config.SITE_ORIGIN, a["href"])
    if not latest_url:
        a = soup.select_one("#info a[rel='chapter'], .lastchapter a[rel='chapter']")
        if a:
            latest_url = urljoin(config.SITE_ORIGIN, a["href"])

    intro = _clean_intro(intro)

    first_chapter_cid = parse_chapter_id(read_url)
    last_chapter_cid = parse_chapter_id(latest_url)
    if not first_chapter_cid:
        raise ValueError(f"书籍页未找到「开始阅读」章节链接: book {book_id}")

    return {
        "title": title,
        "author": author,
        "intro": intro,
        "cover_url": cover_url,
        "category": category,
        "status": status,
        "site_update_time": site_update_time,
        "first_chapter_cid": first_chapter_cid,
        "last_chapter_cid": last_chapter_cid,
    }


def parse_chapter_page(html: str) -> dict:
    """解析章节正文页（单页）。

    返回 {title, paragraphs, next_page_url, next_chapter_cid}。
    next_page_url / next_chapter_cid 为 None 表示不存在。
    """
    soup = _soup(html)

    h1 = soup.select_one("h1.bookname") or soup.select_one("h1")
    raw_title = h1.get_text(strip=True) if h1 else ""
    title = _PAGE_MARK_RE.sub("", raw_title).strip()

    paragraphs = []
    for p in soup.select("#booktxt p"):
        text = p.get_text(strip=True)
        if text:
            paragraphs.append(text)

    next_page_url = None
    next_chapter_cid = None
    nav = soup.select_one("div.bottem1") or soup.select_one("div.bottem2")
    if nav:
        for a in nav.find_all("a", href=True):
            text = a.get_text(strip=True)
            href = a["href"]
            if "javascript:" in href or href == "#":
                continue
            if text in ("下一页", "下页") and "page=" in href:
                next_page_url = urljoin(config.SITE_ORIGIN, href)
            elif text in ("下一章",) :
                m = _CHAPTER_HREF_RE.search(urlparse(href).path)
                if m:
                    next_chapter_cid = int(m.group(2))

    return {
        "title": title,
        "paragraphs": paragraphs,
        "next_page_url": next_page_url,
        "next_chapter_cid": next_chapter_cid,
    }


def parse_catalog_page(html: str, book_id: int) -> tuple[list[dict], int | None]:
    """解析完整目录页 /b/{id}/cs/{n}（Playwright 渲染后的 HTML）。

    返回 (entries, next_page)：entries 为 [{cid, title}]（按页面顺序），
    next_page 为下一页页码（无则 None）。
    """
    soup = _soup(html)
    entries: list[dict] = []
    seen: set[int] = set()
    chapter_re = re.compile(rf"^/b/{book_id}/c/(\d+)$")
    for a in soup.find_all("a", href=True):
        path = urlparse(a["href"]).path
        m = chapter_re.match(path)
        if m:
            cid = int(m.group(1))
            title = a.get_text(strip=True)
            if cid not in seen and title:
                seen.add(cid)
                entries.append({"cid": cid, "title": title})

    next_page = None
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        if text in ("下一页", "下页", ">"):
            m = re.search(rf"/b/{book_id}/cs/(\d+)", a["href"])
            if m:
                next_page = int(m.group(1))
                break
    return entries, next_page


def is_captcha_page(html: str) -> bool:
    """搜索页触发站内图形验证码（<title>验证码</title> + POST /code 取图）。"""
    return "<title>验证码</title>" in html[:600]


def parse_search_page(html: str) -> list[dict]:
    """解析站内搜索结果页 /s/?keyword=xxx。

    结果项结构：div.item > dl > dt > a[href^='/b/']（书名），dd（简介截断），
    div.image img[data-original]（封面）。页面不展示作者。
    返回 [{book_id, title, author, status, intro, cover_url, updated, url}]。
    """
    soup = _soup(html)
    results: list[dict] = []
    seen: set[int] = set()
    for item in soup.select("div.item"):
        a = item.select_one("dt a[href^='/b/'], dl a[href^='/b/']")
        if a is None:
            continue
        m = re.match(r"^/b/(\d+)/?$", urlparse(a["href"]).path)
        if not m:
            continue
        book_id = int(m.group(1))
        title = a.get_text(strip=True) or (a.get("title") or "").strip()
        if not title or book_id in seen:
            continue
        seen.add(book_id)
        dd = item.select_one("dd")
        intro = dd.get_text(strip=True) if dd else ""
        img = item.select_one("img[data-original], img[src]")
        cover = None
        if img:
            cover = img.get("data-original") or img.get("src")
            if cover:
                cover = urljoin(config.SITE_ORIGIN, cover)
        em = item.select_one(".btm em")
        updated = em.get_text(strip=True) if em else ""
        results.append({
            "book_id": book_id,
            "title": title,
            "author": "",
            "status": "",
            "intro": intro,
            "cover_url": cover,
            "updated": updated,
            "url": f"{config.SITE_ORIGIN}/b/{book_id}/",
        })
    return results
