"""整本导出为 TXT（UTF-8）。"""


def build_txt(book: dict, chapters: list[dict]) -> bytes:
    lines = [book["title"], f"作者：{book['author']}", ""]
    if book.get("intro"):
        lines += ["内容简介", book["intro"], ""]
    lines.append("=" * 30)
    for ch in chapters:
        lines += ["", ch["title"], "", ch["content"], ""]
    return "\n".join(lines).encode("utf-8")
