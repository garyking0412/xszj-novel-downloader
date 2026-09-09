"""导出下载端点：TXT / EPUB。"""
import asyncio
import logging
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from .. import db
from ..exporter import epub, txt

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

_MEDIA = {
    "txt": ("text/plain; charset=utf-8", ".txt"),
    "epub": ("application/epub+zip", ".epub"),
}


@router.get("/books/{book_id}/export")
async def export_book(book_id: int, format: str = "txt"):
    fmt = format.lower()
    if fmt not in _MEDIA:
        raise HTTPException(400, "format 必须是 txt 或 epub")
    book = await asyncio.to_thread(db.get_book, book_id)
    if book is None:
        raise HTTPException(404, "书籍不存在")
    chapters = await asyncio.to_thread(db.all_chapters_for_export, book_id)
    if not chapters:
        raise HTTPException(404, "该书籍还没有已爬取的章节，无法导出")

    if fmt == "txt":
        data = await asyncio.to_thread(txt.build_txt, book, chapters)
    else:
        data = await asyncio.to_thread(epub.build_epub, book, chapters)

    media_type, ext = _MEDIA[fmt]
    safe_name = "".join(c for c in book["title"] if c not in '/\\:*?"<>|') or str(book_id)
    filename = quote(f"{safe_name}{ext}")
    return Response(
        content=data,
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
        },
    )
