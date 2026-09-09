"""入口：uv run main.py → http://127.0.0.1:8000"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import config, db
from app.api import books, crawl, export, search
from app.scraper.http import close_http, init_http

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _init_db()
    init_http()
    logging.getLogger(__name__).info(
        "服务就绪: http://%s:%s  数据目录: %s", config.HOST, config.PORT, config.DATA_DIR)
    yield
    await close_http()


async def _init_db() -> None:
    import asyncio
    await asyncio.to_thread(db.init_db)


app = FastAPI(title="xszj-sh 小说书架", lifespan=lifespan)

app.include_router(books.router)
app.include_router(crawl.router)
app.include_router(search.router)
app.include_router(export.router)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


if __name__ == "__main__":
    uvicorn.run("main:app", host=config.HOST, port=config.PORT)
