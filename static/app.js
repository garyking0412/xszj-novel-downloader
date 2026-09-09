/* 小说书架 SPA：Vue3 + hash 路由，无构建步骤。 */
const { createApp } = Vue;

async function api(path, opts = {}) {
  const resp = await fetch(path, {
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!resp.ok) {
    let msg = `HTTP ${resp.status}`;
    try {
      const j = await resp.json();
      if (j.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch (_) {}
    throw new Error(msg);
  }
  return resp.status === 204 ? null : resp.json();
}

function parseHash() {
  const h = location.hash.replace(/^#\/?/, "");
  const parts = h.split("/").filter(Boolean);
  if (parts[0] === "search") return { view: "search" };
  if (parts[0] === "book" && parts[1]) {
    const bookId = Number(parts[1]);
    if (parts[2] === "read" && parts[3])
      return { view: "read", bookId, cid: Number(parts[3]) };
    return { view: "book", bookId };
  }
  return { view: "shelf" };
}

createApp({
  data() {
    return {
      route: parseHash(),
      books: [],
      detail: null,          // {book, chapters, progress}
      chapter: null,         // 当前阅读章节（含 prev/next）
      addUrl: "",
      adding: false,
      keyword: "",
      searching: false,
      searched: false,
      searchResults: [],
      captcha: null,        // {cid, image}
      captchaCode: "",
      captchaError: "",
      fontSize: Number(localStorage.getItem("reader-font-size") || 19),
      toast: { msg: "", type: "info" },
      _toastTimer: null,
      _pollTimer: null,
    };
  },

  computed: {
    chapterParas() {
      return this.chapter ? this.chapter.content.split("\n\n").filter(Boolean) : [];
    },
  },

  methods: {
    /* ---------- 基础设施 ---------- */
    notify(msg, type = "info") {
      this.toast = { msg, type };
      clearTimeout(this._toastTimer);
      this._toastTimer = setTimeout(() => (this.toast.msg = ""), 3500);
    },

    go(view) {
      location.hash = view === "shelf" ? "#/" : `#/${view}`;
    },

    openBook(bookId) {
      location.hash = `#/book/${bookId}`;
    },

    isActive(task) {
      return task && (task.status === "pending" || task.status === "running");
    },

    /* ---------- 数据加载 ---------- */
    async loadBooks() {
      try {
        this.books = await api("/api/books");
      } catch (e) {
        this.notify(`加载书架失败：${e.message}`, "error");
      }
    },

    async loadDetail() {
      try {
        this.detail = await api(`/api/books/${this.route.bookId}`);
      } catch (e) {
        this.notify(`加载书籍失败：${e.message}`, "error");
        this.detail = null;
      }
    },

    async readChapter(cid) {
      location.hash = `#/book/${this.route.bookId}/read/${cid}`;
    },

    async loadChapter() {
      try {
        this.chapter = await api(
          `/api/books/${this.route.bookId}/chapters/${this.route.cid}`);
        window.scrollTo(0, 0);
        // 记录阅读进度（服务端保存）
        api(`/api/books/${this.route.bookId}/progress`, {
          method: "PUT", body: { cid: this.route.cid },
        }).catch(() => {});
      } catch (e) {
        this.notify(`章节加载失败：${e.message}`, "error");
        this.chapter = null;
      }
    },

    /* ---------- 任务轮询 ---------- */
    startPolling() {
      this._pollTimer = setInterval(async () => {
        const actives = [];
        for (const b of this.books) if (this.isActive(b.task)) actives.push(b.task);
        if (this.detail && this.isActive(this.detail.book.task)
            && !actives.some(t => t.id === this.detail.book.task.id))
          actives.push(this.detail.book.task);
        if (!actives.length) return;

        let anyFinished = false;
        for (const t of actives) {
          try {
            const fresh = await api(`/api/tasks/${t.id}`);
            Object.assign(t, fresh);
            if (!this.isActive(fresh)) {
              anyFinished = true;
              if (fresh.status === "done")
                this.notify(`《${this.bookTitle(fresh.book_id)}》爬取完成：${fresh.crawled} 章`, "success");
              else if (fresh.status === "failed")
                this.notify(`爬取失败：${fresh.error}`, "error");
            }
          } catch (_) {}
        }
        if (anyFinished) {
          this.loadBooks();
          if (this.route.view === "book") this.loadDetail();
          if (this.route.view === "read" && !this.chapter) this.loadChapter();
        }
      }, 1000);
    },

    bookTitle(bookId) {
      const b = this.books.find(x => x.id === bookId);
      return b ? b.title : "";
    },

    /* ---------- 操作 ---------- */
    async addBook() {
      if (!this.addUrl) return;
      this.adding = true;
      try {
        const r = await api("/api/books", { method: "POST", body: { url: this.addUrl } });
        this.addUrl = "";
        this.notify(r.existed ? "书籍已在书架中" : "已添加，后台开始爬取", "success");
        await this.loadBooks();
        this.openBook(r.book.id);
      } catch (e) {
        this.notify(`添加失败：${e.message}`, "error");
      } finally {
        this.adding = false;
      }
    },

    async addById(bookId) {
      try {
        const r = await api("/api/books", {
          method: "POST",
          body: { url: `/b/${bookId}/` },
        });
        this.notify(r.existed ? "已在书架中" : "已加入书架，后台开始爬取", "success");
        await this.loadBooks();
        this.openBook(bookId);
      } catch (e) {
        this.notify(`添加失败：${e.message}`, "error");
      }
    },

    async startCrawl(bookId, mode) {
      try {
        const task = await api(`/api/books/${bookId}/crawl`, {
          method: "POST", body: { mode },
        });
        this.notify(mode === "update" ? "开始检查更新…" : "开始爬取…", "success");
        await this.loadBooks();
        if (this.detail && this.detail.book.id === bookId) await this.loadDetail();
      } catch (e) {
        this.notify(e.message, "error");
      }
    },

    async cancelTask(taskId) {
      try {
        await api(`/api/tasks/${taskId}/cancel`, { method: "POST" });
        this.notify("已发送取消请求", "info");
      } catch (e) {
        this.notify(e.message, "error");
      }
    },

    async removeBook(b) {
      if (!confirm(`确定从书架删除《${b.title}》及全部已爬章节？`)) return;
      try {
        await api(`/api/books/${b.id}`, { method: "DELETE" });
        this.notify("已删除", "success");
        await this.loadBooks();
      } catch (e) {
        this.notify(e.message, "error");
      }
    },

    exportBook(bookId, format) {
      window.open(`/api/books/${bookId}/export?format=${format}`, "_blank");
    },

    async doSearch() {
      if (!this.keyword) return;
      this.searching = true;
      this.captchaError = "";
      this.captchaCode = "";
      try {
        const r = await api(`/api/search?keyword=${encodeURIComponent(this.keyword)}`);
        this.applySearchResult(r);
      } catch (e) {
        this.notify(`搜索失败：${e.message}`, "error");
        this.searchResults = [];
        this.captcha = null;
        this.searched = true;
      } finally {
        this.searching = false;
      }
    },

    async submitCaptcha() {
      if (!this.captcha || !this.captchaCode) return;
      this.searching = true;
      this.captchaError = "";
      try {
        const r = await api("/api/search/verify", {
          method: "POST",
          body: { keyword: this.keyword, cid: this.captcha.cid, code: this.captchaCode },
        });
        this.applySearchResult(r);
      } catch (e) {
        this.notify(`验证失败：${e.message}`, "error");
      } finally {
        this.searching = false;
      }
    },

    applySearchResult(r) {
      if (r.need_captcha) {
        this.captcha = r.captcha;
        this.captchaCode = "";
        this.captchaError = r.error || "";
        this.searchResults = [];
        this.searched = false;
      } else {
        this.captcha = null;
        this.searchResults = r.results;
        this.searched = true;
      }
    },

    continueRead(b) {
      const cid = b.progress_cid
        || b.first_cid
        || (this.detail && this.detail.chapters.length && this.detail.chapters[0].cid);
      if (!cid) {
        this.notify("还没有已爬取的章节", "error");
        return;
      }
      location.hash = `#/book/${b.id}/read/${cid}`;
    },

    changeFont(delta) {
      this.fontSize = Math.min(28, Math.max(14, this.fontSize + delta));
      localStorage.setItem("reader-font-size", String(this.fontSize));
    },

    /* ---------- 路由 ---------- */
    onRoute() {
      this.route = parseHash();
      this.chapter = null;
      if (this.route.view === "shelf") {
        this.detail = null;
        this.loadBooks();
      } else if (this.route.view === "book") {
        this.loadDetail();
      } else if (this.route.view === "read") {
        this.loadChapter();
      }
    },
  },

  mounted() {
    window.addEventListener("hashchange", this.onRoute);
    this.loadBooks();
    this.onRoute();
    this.startPolling();
  },
}).mount("#app");
