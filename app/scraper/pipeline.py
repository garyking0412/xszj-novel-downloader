"""爬取编排管道。

两级策略：
1. catalog 模式（Playwright）：拿 /cs/N 完整目录 → Semaphore 并发下载缺失章节，
   可知总数、按百分比显示进度；
2. sequential 模式（降级，纯 httpx）：从第一章沿「下一章」链接顺序遍历，
   支持断点续爬与增量更新。
"""
import asyncio
import logging
import time

from .. import config, db
from .catalog_playwright import CatalogUnavailable, fetch_catalog
from .http import FetchError, get_http
from .parser import parse_book_page, parse_chapter_page
from .tasks import TaskCancelled, TaskState

log = logging.getLogger(__name__)

# 目录模式（Playwright 过 Cloudflare）失败缓存：失败后一段时间内直接跳过，
# 避免每次爬取都白等几十秒 challenge 超时。
_catalog_failed_at: float = 0.0


def _catalog_on_cooldown() -> bool:
    return (time.monotonic() - _catalog_failed_at) < config.CATALOG_RETRY_HOURS * 3600


async def refresh_book_meta(book_id: int) -> dict:
    """抓取书籍信息页并 upsert 到 books 表，返回解析出的 meta。"""
    http = get_http()
    html = await http.get(f"{config.SITE_ORIGIN}/b/{book_id}/")
    meta = parse_book_page(html, book_id)
    await asyncio.to_thread(db.upsert_book_meta, book_id, meta)
    return meta


async def fetch_chapter(book_id: int, cid: int) -> dict:
    """抓取单章全部分页并合并，返回 {cid, title, content, page_count, next_cid}。"""
    http = get_http()
    paragraphs: list[str] = []
    title: str | None = None
    page = 1
    next_cid: int | None = None

    while page <= config.MAX_PAGES_PER_CHAPTER:
        url = f"{config.SITE_ORIGIN}/b/{book_id}/c/{cid}"
        if page > 1:
            url += f"?page={page}"
        data = parse_chapter_page(await http.get(url))
        if title is None:
            title = data["title"]
        paragraphs.extend(data["paragraphs"])
        if data["next_page_url"]:
            page += 1
            continue
        next_cid = data["next_chapter_cid"]
        break
    else:
        raise FetchError(f"章节分页超过上限 {config.MAX_PAGES_PER_CHAPTER}: "
                         f"book {book_id} cid {cid}")

    if not paragraphs:
        raise FetchError(f"章节正文为空: book {book_id} cid {cid}")

    return {
        "cid": cid,
        "title": title or f"第{cid}章",
        "content": "\n\n".join(paragraphs),
        "page_count": page,
        "next_cid": next_cid,
    }


async def crawl_book(task: TaskState) -> None:
    """任务主入口：刷新元信息 → 尝试 catalog → 降级 sequential。"""
    book_id = task.book_id
    update_mode = task.mode == "update"

    meta = await refresh_book_meta(book_id)
    log.info("书籍元信息: %s（%s，最新章节 cid=%s）",
             meta["title"], meta["status"], meta["last_chapter_cid"])

    # 增量模式快速判定：库中最后一章即站内最新章节 → 无更新
    if update_mode:
        last = await asyncio.to_thread(db.get_last_chapter, book_id)
        if (last and meta["last_chapter_cid"]
                and last["cid"] == meta["last_chapter_cid"]):
            task.note = "已是最新，没有新章节"
            task.engine = "none"
            return

    # 第一级：Playwright 目录模式（冷却期内直接跳过）
    global _catalog_failed_at
    catalog: list[dict] | None = None
    if _catalog_on_cooldown():
        log.info("目录模式处于失败冷却期，直接使用顺序遍历")
        task.engine = "sequential"
    else:
        task.note = "尝试目录模式…"
        cat_task = asyncio.create_task(fetch_catalog(book_id))
        cancel_waiter = asyncio.create_task(task.cancel_event.wait())
        done, pending = await asyncio.wait(
            {cat_task, cancel_waiter}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        if cancel_waiter in done:
            raise TaskCancelled()
        try:
            catalog = cat_task.result()
            task.engine = "catalog"
            task.total = len(catalog)
            task.note = ""
        except CatalogUnavailable as e:
            log.info("目录模式不可用，降级为顺序遍历: %s", e)
            task.engine = "sequential"
            task.note = ""
            _catalog_failed_at = time.monotonic()

    if catalog:
        await _crawl_with_catalog(task, catalog)
    else:
        await _crawl_sequential(task, meta, update_mode)


async def _crawl_with_catalog(task: TaskState, catalog: list[dict]) -> None:
    """按完整目录并发下载缺失章节（断点续爬 = 目录 diff）。"""
    book_id = task.book_id
    have = await asyncio.to_thread(db.existing_cids, book_id)
    missing = [e for e in catalog if e["cid"] not in have]
    task.crawled = len(catalog) - len(missing)
    log.info("catalog 模式: 共 %d 章，缺失 %d 章", len(catalog), len(missing))

    if not missing:
        task.note = "所有章节均已入库"
        return

    sem = asyncio.Semaphore(config.MAX_CONCURRENCY)
    errors: list[str] = []

    async def worker(entry: dict) -> None:
        async with sem:
            task.check_cancel()
            try:
                ch = await fetch_chapter(book_id, entry["cid"])
            except FetchError as e:
                errors.append(f"第{entry['order']}章: {e}")
                log.warning("章节抓取失败（跳过）: %s", e)
                return
            await asyncio.to_thread(db.insert_chapter, book_id, ch, entry["order"])
            task.tick(ch["title"], task.crawled + 1)

    await asyncio.gather(*(worker(e) for e in missing))
    task.check_cancel()
    if errors:
        task.error = f"{len(errors)} 章抓取失败：" + "；".join(errors[:5])


async def _crawl_sequential(task: TaskState, meta: dict, update_mode: bool) -> None:
    """从第一章（或库中最后一章的下一章）沿「下一章」链接顺序遍历。"""
    book_id = task.book_id
    last = await asyncio.to_thread(db.get_last_chapter, book_id)

    if last:
        cid: int | None = last["next_cid"]
        order = last["order_index"]
        # 断链兜底：库里最后一章没存 next_cid（旧数据/当时是末章），重新抓一次拿链接
        if cid is None and update_mode and meta["last_chapter_cid"] != last["cid"]:
            ch = await fetch_chapter(book_id, last["cid"])
            cid = ch["next_cid"]
        elif cid is None:
            task.note = "已到最后一章，没有新章节"
            return
    else:
        cid = meta["first_chapter_cid"]
        order = 0

    target_cid = meta["last_chapter_cid"] if update_mode else None
    log.info("sequential 模式: 从 cid=%s（第 %d 章之后）开始，目标 cid=%s",
             cid, order, target_cid)

    while cid is not None:
        task.check_cancel()
        existing = await asyncio.to_thread(db.get_chapter_by_cid, book_id, cid)
        if existing:  # 断点续爬：跳过已入库章节，沿链前进
            order = existing["order_index"]
            cid = existing["next_cid"]
            continue
        ch = await fetch_chapter(book_id, cid)
        order += 1
        await asyncio.to_thread(db.insert_chapter, book_id, ch, order)
        task.tick(ch["title"], task.crawled + 1)
        if target_cid and cid == target_cid:
            break
        cid = ch["next_cid"]
