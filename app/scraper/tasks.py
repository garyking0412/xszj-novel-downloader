"""爬取任务状态机与注册表。

内存 dict 保存运行中任务（供前端轮询），关键节点落库 crawl_tasks 表。
单进程 asyncio.create_task 执行，无需 celery。
"""
import asyncio
import itertools
import logging
from dataclasses import dataclass, field
from datetime import datetime

from .. import db

log = logging.getLogger(__name__)


class TaskCancelled(Exception):
    """任务被用户取消（协作式，管道在每章边界检查）。"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class TaskState:
    id: int
    book_id: int
    mode: str = "auto"            # auto=全量（自动选引擎） / update=增量检查更新
    engine: str = ""              # catalog / sequential（实际采用的引擎）
    status: str = "pending"       # pending / running / done / failed / cancelled
    total: int | None = None      # catalog 模式下的总章节数
    crawled: int = 0              # 本次任务已入库章节数
    current_title: str = ""
    note: str = ""                # 例如"没有新章节"
    error: str = ""
    started_at: str = field(default_factory=_now)
    finished_at: str = ""
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _asyncio_task: asyncio.Task | None = field(default=None, repr=False)

    def check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise TaskCancelled()

    def tick(self, title: str, crawled: int) -> None:
        self.current_title = title
        self.crawled = crawled

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "book_id": self.book_id,
            "mode": self.mode,
            "engine": self.engine,
            "status": self.status,
            "total": self.total,
            "crawled": self.crawled,
            "current_title": self.current_title,
            "note": self.note,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


_registry: dict[int, TaskState] = {}
_counter: itertools.count | None = None
ACTIVE_STATUSES = ("pending", "running")


def _next_task_id() -> int:
    """任务 id 跨进程重启单调递增：首次从 DB 的 MAX(id)+1 继续。"""
    global _counter
    if _counter is None:
        row = db.query_one("SELECT COALESCE(MAX(id),0) AS m FROM crawl_tasks")
        _counter = itertools.count((row["m"] if row else 0) + 1)
    return next(_counter)


def get_task(task_id: int) -> TaskState | None:
    return _registry.get(task_id)


def get_active_task_for_book(book_id: int) -> TaskState | None:
    for t in _registry.values():
        if t.book_id == book_id and t.status in ACTIVE_STATUSES:
            return t
    return None


def start_task(book_id: int, mode: str = "auto") -> TaskState:
    """创建并启动爬取任务。同书已有活动任务时抛 ValueError。"""
    if get_active_task_for_book(book_id) is not None:
        raise ValueError("该书籍已有进行中的爬取任务")
    state = TaskState(id=_next_task_id(), book_id=book_id, mode=mode)
    _registry[state.id] = state
    state._asyncio_task = asyncio.get_running_loop().create_task(_run(state))
    return state


def cancel_task(task_id: int) -> TaskState | None:
    state = _registry.get(task_id)
    if state and state.status in ACTIVE_STATUSES:
        state.cancel_event.set()
    return state


async def _run(state: TaskState) -> None:
    from . import pipeline  # 延迟导入避免环

    state.status = "running"
    try:
        try:
            await asyncio.to_thread(db.insert_task_row,
                                    state.id, state.book_id, state.mode)
        except Exception:
            log.exception("任务落库失败（不影响爬取继续）: task %s", state.id)
        await pipeline.crawl_book(state)
        if state.status == "running":
            state.status = "done"
    except TaskCancelled:
        state.status = "cancelled"
    except asyncio.CancelledError:
        state.status = "cancelled"
        raise
    except Exception as e:
        log.exception("爬取任务失败: book %s", state.book_id)
        state.status = "failed"
        state.error = str(e) or e.__class__.__name__
    finally:
        state.finished_at = _now()
        try:
            await asyncio.to_thread(
                db.finish_task_row, state.id, state.status, state.engine,
                state.total, state.crawled, state.current_title, state.error)
        except Exception:
            log.exception("任务状态落库失败: task %s", state.id)
        log.info("任务 #%s 结束: book=%s status=%s engine=%s crawled=%s",
                 state.id, state.book_id, state.status, state.engine, state.crawled)
