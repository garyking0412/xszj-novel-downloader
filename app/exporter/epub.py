"""整本导出为 EPUB3（标准库 zipfile 手写，零第三方依赖）。

EPUB = zip 容器：mimetype（首个条目、不压缩）+ META-INF/container.xml
+ OEBPS/content.opf（元数据/清单/书脊）+ nav.xhtml（目录）+ 每章 XHTML + CSS。
"""
import io
import zipfile
from datetime import datetime, timezone
from html import escape

CSS = """
body { font-family: serif; line-height: 1.8; margin: 1em; }
h1 { font-size: 1.4em; text-align: center; margin: 1.5em 0 1em; }
h2 { font-size: 1.2em; margin: 1.2em 0 0.8em; }
p { text-indent: 2em; margin: 0.4em 0; }
.meta { text-align: center; color: #555; }
.intro p { text-indent: 2em; }
"""

CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""

_XHTML_HEAD = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="zh">
<head><meta charset="utf-8"/><title>{title}</title>
<link rel="stylesheet" type="text/css" href="style.css"/></head>
<body>
{body}
</body>
</html>"""


def _xhtml(title: str, body: str) -> str:
    return _XHTML_HEAD.format(title=escape(title), body=body)


def _chapter_body(title: str, content: str) -> str:
    paras = "".join(f"<p>{escape(p)}</p>"
                    for p in content.split("\n\n") if p.strip())
    return f"<h2>{escape(title)}</h2>\n{paras}"


def build_epub(book: dict, chapters: list[dict]) -> bytes:
    title = book["title"] or f"book-{book['id']}"
    author = book["author"] or "佚名"
    lang = "zh"
    uid = f"xszj-{book['id']}"
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 清单/书脊条目
    manifest_items = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="titlepage" href="titlepage.xhtml" media-type="application/xhtml+xml"/>',
        '<item id="css" href="style.css" media-type="text/css"/>',
    ]
    spine_items = ['<itemref idref="titlepage"/>']
    nav_lis = []
    chapter_files: list[tuple[str, str]] = []

    for i, ch in enumerate(chapters, 1):
        item_id = f"ch{i}"
        fname = f"chapter_{i}.xhtml"
        manifest_items.append(
            f'<item id="{item_id}" href="{fname}" media-type="application/xhtml+xml"/>')
        spine_items.append(f'<itemref idref="{item_id}"/>')
        nav_lis.append(f'<li><a href="{fname}">{escape(ch["title"])}</a></li>')
        chapter_files.append((fname, _xhtml(ch["title"],
                                            _chapter_body(ch["title"], ch["content"]))))

    opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="uid">{uid}</dc:identifier>
    <dc:title>{escape(title)}</dc:title>
    <dc:creator>{escape(author)}</dc:creator>
    <dc:language>{lang}</dc:language>
    <meta property="dcterms:modified">{modified}</meta>
  </metadata>
  <manifest>
    {chr(10).join(manifest_items)}
  </manifest>
  <spine>
    {chr(10).join(spine_items)}
  </spine>
</package>"""

    nav = _xhtml("目录", f"""<h1>目录</h1>
<nav epub:type="toc" id="toc">
<ol>
{chr(10).join(nav_lis)}
</ol>
</nav>""")

    intro_html = ""
    if book.get("intro"):
        intro_html = ('<div class="intro"><h2>内容简介</h2>'
                      + "".join(f"<p>{escape(p)}</p>"
                                for p in book["intro"].split("\n") if p.strip())
                      + "</div>")
    titlepage = _xhtml(title, f"""<h1>{escape(title)}</h1>
<p class="meta">作者：{escape(author)}</p>
{intro_html}""")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        # mimetype 必须是第一个条目且不压缩
        info = zipfile.ZipInfo("mimetype", date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_STORED
        z.writestr(info, "application/epub+zip")
        z.writestr("META-INF/container.xml", CONTAINER_XML)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/nav.xhtml", nav)
        z.writestr("OEBPS/style.css", CSS)
        z.writestr("OEBPS/titlepage.xhtml", titlepage)
        for fname, content in chapter_files:
            z.writestr(f"OEBPS/{fname}", content)
    return buf.getvalue()
