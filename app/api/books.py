"""书籍相关端点：书架、添加、详情、章节内容、阅读进度、删除。"""
import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db
from ..scraper import parser, tasks
from ..scraper.http import CloudflareBlocked, FetchError
from ..scraper.pipeline import refresh_book_meta

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


class AddBookIn(BaseModel):
    url: str


class ProgressIn(BaseModel):
    cid: int


def _book_with_task(book: dict) -> dict:
    t = tasks.get_active_task_for_book(book["id"])
    book["task"] = t.to_dict() if t else None
    return book


@router.get("/books")
async def list_books():
    books = await asyncio.to_thread(db.list_books_with_stats)
    return [_book_with_task(b) for b in books]


@router.post("/books")
async def add_book(body: AddBookIn):
    try:
        book_id = parser.parse_book_id(body.url.strip())
    except ValueError as e:
        raise HTTPException(400, str(e))
    try:
        await refresh_book_meta(book_id)
    except CloudflareBlocked as e:
        raise HTTPException(502, f"书籍页被 Cloudflare 拦截: {e}")
    except FetchError as e:
        raise HTTPException(502, f"书籍页抓取失败: {e}")

    existed = await asyncio.to_thread(db.count_chapters, book_id) > 0
    task = tasks.get_active_task_for_book(book_id)
    if task is None:
        task = tasks.start_task(book_id, "auto")
    book = await asyncio.to_thread(db.get_book, book_id)
    stats = (await asyncio.to_thread(db.list_books_with_stats))
    row = next((b for b in stats if b["id"] == book_id), book)
    return {"book": _book_with_task(row), "existed": existed}


@router.get("/books/{book_id}")
async def get_book(book_id: int):
    book = await asyncio.to_thread(db.get_book, book_id)
    if book is None:
        raise HTTPException(404, "书籍不存在")
    chapters = await asyncio.to_thread(db.list_chapter_meta, book_id)
    progress = await asyncio.to_thread(db.get_progress, book_id)
    return {"book": _book_with_task(book), "chapters": chapters, "progress": progress}


@router.delete("/books/{book_id}")
async def delete_book(book_id: int):
    if tasks.get_active_task_for_book(book_id):
        raise HTTPException(409, "该书籍有进行中的爬取任务，请先取消任务再删除")
    book = await asyncio.to_thread(db.get_book, book_id)
    if book is None:
        raise HTTPException(404, "书籍不存在")
    await asyncio.to_thread(db.delete_book, book_id)
    return {"ok": True}


@router.get("/books/{book_id}/chapters/{cid}")
async def get_chapter(book_id: int, cid: int):
    ch = await asyncio.to_thread(db.get_chapter_full, book_id, cid)
    if ch is None:
        raise HTTPException(404, "章节不存在（可能尚未爬取）")
    return ch


@router.put("/books/{book_id}/progress")
async def update_progress(book_id: int, body: ProgressIn):
    ch = await asyncio.to_thread(db.get_chapter_by_cid, book_id, body.cid)
    if ch is None:
        raise HTTPException(404, "章节不存在")
    await asyncio.to_thread(db.upsert_progress, book_id, body.cid, ch["order_index"])
    return {"ok": True}
