"""全局配置，均可用环境变量覆盖。"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# 数据目录（SQLite 数据库）
DATA_DIR = Path(os.environ.get("XSZJ_DATA_DIR", str(BASE_DIR / "data")))
DB_PATH = DATA_DIR / "novel.db"

# 目标站点
SITE_ORIGIN = os.environ.get("XSZJ_ORIGIN", "https://xszj.org").rstrip("/")

# HTTP 请求
USER_AGENT = os.environ.get(
    "XSZJ_UA",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
)
REQUEST_TIMEOUT = float(os.environ.get("XSZJ_TIMEOUT", "30"))
MAX_RETRIES = int(os.environ.get("XSZJ_MAX_RETRIES", "3"))

# 礼貌延时：每次请求成功后随机 sleep [MIN, MAX] 秒
REQUEST_DELAY_MIN = float(os.environ.get("XSZJ_DELAY_MIN", "1.0"))
REQUEST_DELAY_MAX = float(os.environ.get("XSZJ_DELAY_MAX", "2.0"))

# 目录模式（Playwright 拿到完整目录后）并发下载章节数
MAX_CONCURRENCY = int(os.environ.get("XSZJ_CONCURRENCY", "3"))

# 单章最大分页数（防死循环保险丝）
MAX_PAGES_PER_CHAPTER = int(os.environ.get("XSZJ_MAX_PAGES", "30"))

# 目录模式（Playwright 过 Cloudflare）失败后的冷却时长（小时），
# 冷却期内新任务直接走顺序遍历，避免每次白等 challenge 超时
CATALOG_RETRY_HOURS = float(os.environ.get("XSZJ_CATALOG_RETRY_HOURS", "6"))

# Web 服务
HOST = os.environ.get("XSZJ_HOST", "127.0.0.1")
PORT = int(os.environ.get("XSZJ_PORT", "8000"))
