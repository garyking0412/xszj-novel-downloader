"""SQLite 数据库：同步 sqlite3 + 全局锁。

所有函数都是同步阻塞的，异步上下文中请通过 `asyncio.to_thread(db.xxx, ...)` 调用。
"""
import sqlite3
import threading

from . import config

_conn: sqlite3.Connection | None = None
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS books(
    id                INTEGER PRIMARY KEY,           -- 站内 book_id
    title             TEXT NOT NULL DEFAULT '',
    author            TEXT NOT NULL DEFAULT '',
    intro             TEXT NOT NULL DEFAULT '',
    cover_url         TEXT,
    category          TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT '',      -- 完本 / 连载 ...
    site_update_time  TEXT,                          -- 站内显示的最后更新时间
    first_chapter_cid INTEGER,
    last_chapter_cid  INTEGER,
    last_checked_at   TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS chapters(
    id          INTEGER PRIMARY KEY,
    book_id     INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    cid         INTEGER NOT NULL,                    -- 站内 chapter_id
    order_index INTEGER NOT NULL,                    -- 全书序号，从 1 开始
    title       TEXT NOT NULL DEFAULT '',
    content     TEXT NOT NULL DEFAULT '',            -- 各分页合并后的正文
    page_count  INTEGER NOT NULL DEFAULT 1,
    next_cid    INTEGER,                             -- 站内"下一章"链接，用于断点续爬
    crawled_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(book_id, cid)
);
CREATE INDEX IF NOT EXISTS idx_chapters_book_order ON chapters(book_id, order_index);

CREATE TABLE IF NOT EXISTS reading_progress(
    book_id     INTEGER PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE,
    cid         INTEGER NOT NULL,
    order_index INTEGER,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS crawl_tasks(
    id            INTEGER PRIMARY KEY,
    book_id       INTEGER NOT NULL,
    mode          TEXT NOT NULL,                     -- auto / update
    engine        TEXT,                              -- catalog / sequential
    status        TEXT NOT NULL,
    total         INTEGER,
    crawled       INTEGER DEFAULT 0,
    current_title TEXT,
    error         TEXT,
    started_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    finished_at   TEXT
);
"""


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(config.DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.execute("PRAGMA busy_timeout=5000")
    return _conn


def init_db() -> None:
    with _lock:
        get_conn().executescript(SCHEMA)
        _conn.commit()


def query(sql: str, params: tuple = ()) -> list[dict]:
    with _lock:
        rows = get_conn().execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def query_one(sql: str, params: tuple = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple = ()) -> int:
    with _lock:
        conn = get_conn()
        cur = conn.execute(sql, params)
        conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------- books

def upsert_book_meta(book_id: int, meta: dict) -> None:
    execute(
        """
        INSERT INTO books(id, title, author, intro, cover_url, category, status,
                          site_update_time, first_chapter_cid, last_chapter_cid,
                          last_checked_at)
        VALUES(:id, :title, :author, :intro, :cover_url, :category, :status,
               :site_update_time, :first_chapter_cid, :last_chapter_cid,
               datetime('now','localtime'))
        ON CONFLICT(id) DO UPDATE SET
            title=excluded.title, author=excluded.author, intro=excluded.intro,
            cover_url=excluded.cover_url, category=excluded.category,
            status=excluded.status, site_update_time=excluded.site_update_time,
            first_chapter_cid=COALESCE(excluded.first_chapter_cid, books.first_chapter_cid),
            last_chapter_cid=excluded.last_chapter_cid,
            last_checked_at=excluded.last_checked_at,
            updated_at=datetime('now','localtime')
        """,
        {"id": book_id, **meta},
    )


def get_book(book_id: int) -> dict | None:
    return query_one("SELECT * FROM books WHERE id=?", (book_id,))


def list_books_with_stats() -> list[dict]:
    return query(
        """
        SELECT b.*,
               (SELECT COUNT(*) FROM chapters c WHERE c.book_id=b.id) AS chapter_count,
               (SELECT c.cid FROM chapters c WHERE c.book_id=b.id
                 ORDER BY c.order_index LIMIT 1) AS first_cid,
               (SELECT rp.cid FROM reading_progress rp WHERE rp.book_id=b.id) AS progress_cid,
               (SELECT rp.order_index FROM reading_progress rp WHERE rp.book_id=b.id) AS progress_order
        FROM books b
        ORDER BY b.updated_at DESC
        """
    )


def delete_book(book_id: int) -> None:
    execute("DELETE FROM books WHERE id=?", (book_id,))


# -------------------------------------------------------------- chapters

def insert_chapter(book_id: int, ch: dict, order_index: int) -> None:
    execute(
        """
        INSERT INTO chapters(book_id, cid, order_index, title, content, page_count, next_cid)
        VALUES(?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(book_id, cid) DO UPDATE SET
            order_index=excluded.order_index, title=excluded.title,
            content=excluded.content, page_count=excluded.page_count,
            next_cid=excluded.next_cid
        """,
        (book_id, ch["cid"], order_index, ch["title"], ch["content"],
         ch["page_count"], ch["next_cid"]),
    )


def get_chapter_by_cid(book_id: int, cid: int) -> dict | None:
    return query_one("SELECT * FROM chapters WHERE book_id=? AND cid=?", (book_id, cid))


def get_chapter_full(book_id: int, cid: int) -> dict | None:
    """章节正文 + 前后章导航信息。"""
    row = query_one("SELECT * FROM chapters WHERE book_id=? AND cid=?", (book_id, cid))
    if row is None:
        return None
    prev = query_one(
        "SELECT cid, title FROM chapters WHERE book_id=? AND order_index<? "
        "ORDER BY order_index DESC LIMIT 1", (book_id, row["order_index"]))
    nxt = query_one(
        "SELECT cid, title FROM chapters WHERE book_id=? AND order_index>? "
        "ORDER BY order_index ASC LIMIT 1", (book_id, row["order_index"]))
    row["prev"] = prev
    row["next"] = nxt
    return row


def list_chapter_meta(book_id: int) -> list[dict]:
    return query(
        "SELECT cid, order_index, title, page_count, crawled_at FROM chapters "
        "WHERE book_id=? ORDER BY order_index", (book_id,))


def get_last_chapter(book_id: int) -> dict | None:
    return query_one(
        "SELECT * FROM chapters WHERE book_id=? ORDER BY order_index DESC LIMIT 1",
        (book_id,))


def count_chapters(book_id: int) -> int:
    row = query_one("SELECT COUNT(*) AS n FROM chapters WHERE book_id=?", (book_id,))
    return row["n"] if row else 0


def existing_cids(book_id: int) -> set[int]:
    rows = query("SELECT cid FROM chapters WHERE book_id=?", (book_id,))
    return {r["cid"] for r in rows}


def all_chapters_for_export(book_id: int) -> list[dict]:
    return query(
        "SELECT cid, order_index, title, content FROM chapters "
        "WHERE book_id=? ORDER BY order_index", (book_id,))


# ------------------------------------------------------------ progress

def upsert_progress(book_id: int, cid: int, order_index: int | None) -> None:
    execute(
        """
        INSERT INTO reading_progress(book_id, cid, order_index, updated_at)
        VALUES(?, ?, ?, datetime('now','localtime'))
        ON CONFLICT(book_id) DO UPDATE SET
            cid=excluded.cid, order_index=excluded.order_index,
            updated_at=excluded.updated_at
        """,
        (book_id, cid, order_index),
    )


def get_progress(book_id: int) -> dict | None:
    return query_one("SELECT * FROM reading_progress WHERE book_id=?", (book_id,))


# --------------------------------------------------------------- tasks

def insert_task_row(task_id: int, book_id: int, mode: str) -> None:
    execute(
        "INSERT INTO crawl_tasks(id, book_id, mode, status) VALUES(?, ?, ?, 'running')",
        (task_id, book_id, mode),
    )


def finish_task_row(task_id: int, status: str, engine: str, total: int | None,
                    crawled: int, current_title: str, error: str) -> None:
    execute(
        """
        UPDATE crawl_tasks SET status=?, engine=?, total=?, crawled=?,
               current_title=?, error=?, finished_at=datetime('now','localtime')
        WHERE id=?
        """,
        (status, engine, total, crawled, current_title, error, task_id),
    )
