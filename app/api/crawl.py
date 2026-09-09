"""爬取任务端点：手动启动/重跑、进度轮询、取消。"""
import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db
from ..scraper import tasks

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


class CrawlIn(BaseModel):
    mode: str = "auto"   # auto=全量续爬 / update=增量检查更新


@router.post("/books/{book_id}/crawl")
async def start_crawl(book_id: int, body: CrawlIn | None = None):
    mode = body.mode if body else "auto"
    if mode not in ("auto", "update"):
        raise HTTPException(400, "mode 必须是 auto 或 update")
    book = await asyncio.to_thread(db.get_book, book_id)
    if book is None:
        raise HTTPException(404, "书籍不存在，请先添加")
    try:
        task = tasks.start_task(book_id, mode)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return task.to_dict()


@router.get("/tasks/{task_id}")
async def get_task(task_id: int):
    state = tasks.get_task(task_id)
    if state is None:
        raise HTTPException(404, "任务不存在（服务可能已重启，历史任务不再跟踪）")
    return state.to_dict()


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: int):
    state = tasks.cancel_task(task_id)
    if state is None:
        raise HTTPException(404, "任务不存在")
    return state.to_dict()
