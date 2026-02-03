#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import html
import os
from pathlib import Path
import re
import sys
import uuid
import zipfile
from dataclasses import dataclass, field
from typing import Iterable, Optional

ENCODING_CANDIDATES = ("utf-8-sig", "utf-8", "gb18030")

LABEL_RE = re.compile(
    r"^\s*(书名|作者|作\s*者|内容简介|简介|内容介绍|文案)\s*[:：]\s*(.*)\s*$"
)

TITLE_ONLY_RE = re.compile(r"^《(.+)》$")

CHAPTER_PATTERNS = [
    re.compile(r"^第\s*[0-9一二三四五六七八九十百千万两零〇]+\s*章.*$"),
    re.compile(r"^第\s*[0-9一二三四五六七八九十百千万两零〇]+\s*节.*$"),
    re.compile(r"^第\s*[0-9一二三四五六七八九十百千万两零〇]+\s*回.*$"),
    re.compile(r"^Chapter\s+\d+.*$", re.IGNORECASE),
]

VOLUME_PATTERNS = [
    re.compile(r"^第\s*[0-9一二三四五六七八九十百千万两零〇]+\s*卷.*$"),
    re.compile(r"^卷\s*[0-9一二三四五六七八九十百千万两零〇]+.*$"),
    re.compile(r"^第\s*[0-9一二三四五六七八九十百千万两零〇]+\s*部.*$"),
]

SPECIAL_HEADINGS = [
    "序章",
    "序",
    "楔子",
    "引子",
    "前言",
    "前序",
    "后记",
    "后序",
    "尾声",
    "结语",
    "终章",
    "终卷",
    "终篇",
    "番外",
    "番外篇",
    "作者的话",
    "完结感言",
]

SKIP_CANDIDATE_RE = re.compile(r"(http|www|QQ群|群|公众号|微信|下载|txt|整理|校对|打包|本书|电子书)")
SENTENCE_END_RE = re.compile(r"[。！？]$")
COMMA_RE = re.compile(r"[，,]")
HEADING_MAX_LEN = 40
HEADING_MAX_COMMAS = 1


@dataclass
class Chapter:
    title: str
    lines: list[str] = field(default_factory=list)
    volume: Optional["Volume"] = None
    file_name: str = ""
    item_id: str = ""


@dataclass
class Volume:
    title: str
    lines: list[str] = field(default_factory=list)
    chapters: list[Chapter] = field(default_factory=list)
    file_name: str = ""
    item_id: str = ""


@dataclass
class FrontMatter:
    title: str
    author: Optional[str]
    intro: Optional[str]
    file_name: str = ""
    item_id: str = ""


@dataclass
class Book:
    title: str
    author: Optional[str]
    intro: Optional[str]
    volumes: list[Volume] = field(default_factory=list)
    root_chapters: list[Chapter] = field(default_factory=list)
    spine: list[object] = field(default_factory=list)


def read_text(path: Path) -> str:
    data = path.read_bytes()
    for enc in ENCODING_CANDIDATES:
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def is_heading(line: str, prev_line: Optional[str] = None, next_line: Optional[str] = None) -> bool:
    return classify_heading(line, prev_line, next_line) is not None


def is_likely_heading_line(line: str, prev_line: Optional[str], next_line: Optional[str]) -> bool:
    s = line.strip()
    if not s:
        return False

    prev_blank = prev_line is None or not prev_line.strip()
    next_blank = next_line is None or not next_line.strip()
    isolated = prev_blank or next_blank

    if SENTENCE_END_RE.search(s) and not isolated:
        return False

    if len(s) > HEADING_MAX_LEN and not isolated:
        return False

    if len(COMMA_RE.findall(s)) > HEADING_MAX_COMMAS and not isolated:
        return False

    return True


def classify_heading(line: str, prev_line: Optional[str] = None, next_line: Optional[str] = None) -> Optional[str]:
    s = line.strip()
    if not s:
        return None
    for pattern in CHAPTER_PATTERNS:
        if pattern.match(s):
            return "chapter" if is_likely_heading_line(line, prev_line, next_line) else None
    for kw in SPECIAL_HEADINGS:
        if s == kw or s.startswith(kw + " ") or s.startswith(kw + "：") or s.startswith(kw + ":"):
            return "chapter" if is_likely_heading_line(line, prev_line, next_line) else None
    for pattern in VOLUME_PATTERNS:
        if pattern.match(s):
            return "volume" if is_likely_heading_line(line, prev_line, next_line) else None
    return None


def parse_metadata(lines: list[str]) -> tuple[Optional[str], Optional[str], Optional[str], set[int]]:
    title = None
    author = None
    intro_lines: list[str] = []
    skip_idx: set[int] = set()
    candidates: list[tuple[int, str]] = []
    pending_label: Optional[str] = None
    in_intro = False

    first_heading_idx = None
    for i, line in enumerate(lines):
        prev_line = lines[i - 1] if i > 0 else None
        next_line = lines[i + 1] if i + 1 < len(lines) else None
        if is_heading(line, prev_line, next_line):
            first_heading_idx = i
            break

    scan_limit = first_heading_idx if first_heading_idx is not None else len(lines)
    non_empty_seen = 0

    for i in range(scan_limit):
        raw = lines[i]
        prev_line = lines[i - 1] if i > 0 else None
        next_line = lines[i + 1] if i + 1 < len(lines) else None
        s = raw.strip()
        if not s:
            if in_intro and intro_lines:
                in_intro = False
            continue

        if in_intro:
            if is_heading(raw, prev_line, next_line):
                break
            intro_lines.append(s)
            skip_idx.add(i)
            continue

        m = LABEL_RE.match(s)
        if m:
            label = m.group(1)
            value = m.group(2).strip()
            skip_idx.add(i)
            if label in ("书名",):
                if value:
                    title = value
                else:
                    pending_label = "title"
            elif label in ("作者", "作 者"):
                if value:
                    author = value
                else:
                    pending_label = "author"
            else:
                if value:
                    intro_lines.append(value)
                in_intro = True
            continue

        if s in ("书名", "书 名"):
            pending_label = "title"
            skip_idx.add(i)
            continue
        if s in ("作者", "作 者"):
            pending_label = "author"
            skip_idx.add(i)
            continue
        if s in ("内容简介", "简介", "内容介绍", "文案"):
            in_intro = True
            skip_idx.add(i)
            continue

        if pending_label and (s.startswith("：") or s.startswith(":")):
            value = s[1:].strip()
            skip_idx.add(i)
            if pending_label == "title" and value:
                title = value
            elif pending_label == "author" and value:
                author = value
            pending_label = None
            continue

        m_title_only = TITLE_ONLY_RE.match(s)
        if m_title_only and title is None:
            title = m_title_only.group(1).strip()
            skip_idx.add(i)
            continue

        non_empty_seen += 1
        if non_empty_seen <= 6 and not SKIP_CANDIDATE_RE.search(s):
            candidates.append((i, s))

    if title is None and candidates:
        idx, value = candidates[0]
        title = value
        skip_idx.add(idx)

    if author is None and len(candidates) >= 2:
        idx, value = candidates[1]
        if value != title:
            author = value
            skip_idx.add(idx)

    intro = "\n".join(intro_lines).strip() if intro_lines else None
    return title, author, intro, skip_idx


def normalize_content_line(line: str) -> str:
    s = line.strip()
    if not s:
        return ""
    return s.replace("\u3000", "")


def parse_book(text: str, source_name: str) -> Book:
    lines = text.splitlines()
    title, author, intro, skip_idx = parse_metadata(lines)

    body_lines = [line for idx, line in enumerate(lines) if idx not in skip_idx]
    while body_lines and not body_lines[0].strip():
        body_lines.pop(0)

    book = Book(title=title or source_name, author=author, intro=intro)

    current_volume: Optional[Volume] = None
    current_chapter: Optional[Chapter] = None

    def start_volume(heading: str) -> Volume:
        vol = Volume(title=heading)
        book.volumes.append(vol)
        book.spine.append(vol)
        return vol

    def start_chapter(heading: str, volume: Optional[Volume]) -> Chapter:
        chap = Chapter(title=heading, volume=volume)
        if volume:
            volume.chapters.append(chap)
        else:
            book.root_chapters.append(chap)
        book.spine.append(chap)
        return chap

    for idx, line in enumerate(body_lines):
        prev_line = body_lines[idx - 1] if idx > 0 else None
        next_line = body_lines[idx + 1] if idx + 1 < len(body_lines) else None
        heading_type = classify_heading(line, prev_line, next_line)
        if heading_type == "volume":
            current_chapter = None
            current_volume = start_volume(line.strip())
            continue
        if heading_type == "chapter":
            current_chapter = start_chapter(line.strip(), current_volume)
            continue

        content = normalize_content_line(line)
        if not content:
            continue

        if current_chapter:
            current_chapter.lines.append(content)
        elif current_volume:
            current_volume.lines.append(content)
        else:
            if not book.root_chapters:
                current_chapter = start_chapter("正文", None)
            else:
                current_chapter = book.root_chapters[-1]
            current_chapter.lines.append(content)

    return book


def build_nav_items(book: Book) -> list[dict]:
    items: list[dict] = []
    current_volume_item: Optional[dict] = None

    for section in book.spine:
        if isinstance(section, Volume):
            volume_item = {"title": section.title, "href": section.file_name, "children": []}
            items.append(volume_item)
            current_volume_item = volume_item
        else:
            chapter_item = {"title": section.title, "href": section.file_name, "children": []}
            if section.volume is not None and current_volume_item is not None:
                current_volume_item["children"].append(chapter_item)
            else:
                items.append(chapter_item)
    return items


def render_nav_list(items: Iterable[dict], indent: str = "  ") -> str:
    lines = [f"{indent}<ol>"]
    for item in items:
        lines.append(f"{indent}  <li><a href=\"{html.escape(item['href'])}\">{html.escape(item['title'])}</a>")
        if item["children"]:
            lines.append(render_nav_list(item["children"], indent + "    "))
            lines.append(f"{indent}  </li>")
        else:
            lines.append(f"{indent}  </li>")
    lines.append(f"{indent}</ol>")
    return "\n".join(lines)


def render_section(title: str, lines: list[str], lang: str) -> str:
    paragraphs = []
    for line in lines:
        if not line:
            continue
        paragraphs.append(f"    <p>{html.escape(line)}</p>")

    body = "\n".join(paragraphs) if paragraphs else ""
    return (
        "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
        "<html xmlns=\"http://www.w3.org/1999/xhtml\" xml:lang=\"{lang}\">\n"
        "  <head>\n"
        "    <meta charset=\"utf-8\" />\n"
        "    <title>{title}</title>\n"
        "    <link rel=\"stylesheet\" type=\"text/css\" href=\"../style.css\" />\n"
        "  </head>\n"
        "  <body>\n"
        "    <h2>{title}</h2>\n"
        "{body}\n"
        "  </body>\n"
        "</html>\n"
    ).format(lang=lang, title=html.escape(title), body=body)


def render_front_matter(front: FrontMatter, lang: str) -> str:
    paragraphs = []
    if front.author:
        paragraphs.append(f"    <p class=\"author\">作者：{html.escape(front.author)}</p>")
    if front.intro:
        paragraphs.append("    <p class=\"intro-label\">简介</p>")
        for raw in front.intro.splitlines():
            line = raw.strip()
            if not line:
                continue
            paragraphs.append(f"    <p>{html.escape(line)}</p>")

    body = "\n".join(paragraphs) if paragraphs else ""
    return (
        "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
        "<html xmlns=\"http://www.w3.org/1999/xhtml\" xml:lang=\"{lang}\">\n"
        "  <head>\n"
        "    <meta charset=\"utf-8\" />\n"
        "    <title>{title}</title>\n"
        "    <link rel=\"stylesheet\" type=\"text/css\" href=\"../style.css\" />\n"
        "  </head>\n"
        "  <body class=\"front-matter\">\n"
        "    <h1>{title}</h1>\n"
        "{body}\n"
        "  </body>\n"
        "</html>\n"
    ).format(lang=lang, title=html.escape(front.title), body=body)


def build_epub(book: Book, output_path: Path) -> None:
    lang = "zh-CN"
    book_id = f"urn:uuid:{uuid.uuid4()}"
    modified = (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )

    front_matter = None
    if book.title or book.author or book.intro:
        front_matter = FrontMatter(title=book.title, author=book.author, intro=book.intro)

    spine_sections: list[object] = []
    if front_matter:
        spine_sections.append(front_matter)
    spine_sections.extend(book.spine)

    section_files: list[object] = []
    for idx, section in enumerate(spine_sections, start=1):
        file_name = f"text/section_{idx:04d}.xhtml"
        item_id = f"section_{idx:04d}"
        section.file_name = file_name
        section.item_id = item_id
        section_files.append(section)

    nav_items = build_nav_items(book)

    nav_doc = (
        "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
        "<html xmlns=\"http://www.w3.org/1999/xhtml\" xmlns:epub=\"http://www.idpf.org/2007/ops\" "
        "xml:lang=\"{lang}\">\n"
        "  <head>\n"
        "    <meta charset=\"utf-8\" />\n"
        "    <title>目录</title>\n"
        "    <link rel=\"stylesheet\" type=\"text/css\" href=\"style.css\" />\n"
        "  </head>\n"
        "  <body>\n"
        "    <nav epub:type=\"toc\" id=\"toc\">\n"
        "      <h1>目录</h1>\n"
        "{toc}\n"
        "    </nav>\n"
        "    <nav epub:type=\"landmarks\">\n"
        "      <h2>Landmarks</h2>\n"
        "      <ol>\n"
        "        <li><a epub:type=\"bodymatter\" href=\"{first_href}\">正文</a></li>\n"
        "      </ol>\n"
        "    </nav>\n"
        "  </body>\n"
        "</html>\n"
    ).format(
        lang=lang,
        toc=render_nav_list(nav_items, "      "),
        first_href=html.escape(section_files[0].file_name) if section_files else "",
    )

    manifest_items = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="css" href="style.css" media-type="text/css"/>',
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
    ]

    spine_items: list[str] = []
    for section in section_files:
        manifest_items.append(
            f'<item id="{section.item_id}" href="{section.file_name}" media-type="application/xhtml+xml"/>'
        )
        spine_items.append(f'<itemref idref="{section.item_id}"/>')

    metadata_lines = [
        f'<dc:identifier id="bookid">{html.escape(book_id)}</dc:identifier>',
        f'<dc:title>{html.escape(book.title)}</dc:title>',
        f'<dc:language>{lang}</dc:language>',
    ]
    if book.author:
        metadata_lines.append(f'<dc:creator>{html.escape(book.author)}</dc:creator>')
    if book.intro:
        metadata_lines.append(f'<dc:description>{html.escape(book.intro)}</dc:description>')
    metadata_lines.append(f'<meta property="dcterms:modified">{modified}</meta>')

    content_opf = (
        "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
        "<package xmlns=\"http://www.idpf.org/2007/opf\" unique-identifier=\"bookid\" version=\"3.0\">\n"
        "  <metadata xmlns:dc=\"http://purl.org/dc/elements/1.1/\">\n"
        "    {metadata}\n"
        "  </metadata>\n"
        "  <manifest>\n"
        "    {manifest}\n"
        "  </manifest>\n"
        "  <spine toc=\"ncx\">\n"
        "    {spine}\n"
        "  </spine>\n"
        "</package>\n"
    ).format(
        metadata="\n    ".join(metadata_lines),
        manifest="\n    ".join(manifest_items),
        spine="\n    ".join(spine_items),
    )

    nav_points = []
    play_order = 1

    def add_nav_point(item: dict, depth: int = 1) -> str:
        nonlocal play_order
        current_order = play_order
        play_order += 1
        children = "".join(add_nav_point(child, depth + 1) for child in item["children"])
        return (
            f"<navPoint id=\"navPoint-{current_order}\" playOrder=\"{current_order}\">"
            f"<navLabel><text>{html.escape(item['title'])}</text></navLabel>"
            f"<content src=\"{html.escape(item['href'])}\"/>"
            f"{children}"
            f"</navPoint>"
        )

    for item in nav_items:
        nav_points.append(add_nav_point(item))

    toc_ncx = (
        "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
        "<ncx xmlns=\"http://www.daisy.org/z3986/2005/ncx/\" version=\"2005-1\">\n"
        "  <head>\n"
        "    <meta name=\"dtb:uid\" content=\"{book_id}\" />\n"
        "  </head>\n"
        "  <docTitle><text>{title}</text></docTitle>\n"
        "  <navMap>\n"
        "    {nav_points}\n"
        "  </navMap>\n"
        "</ncx>\n"
    ).format(book_id=html.escape(book_id), title=html.escape(book.title), nav_points="\n    ".join(nav_points))

    css = (
        "p { text-indent: 2em; margin: 0 0 0.8em; }\n"
        "h2 { font-weight: bold; font-size: 1.2em; margin: 1.5em 0 1em; }\n"
        ".front-matter p.author { text-align: center; text-indent: 0; margin: 0 0 1.5em; }\n"
        ".front-matter p.intro-label { text-indent: 0; font-weight: bold; margin: 1.2em 0 0.6em; }\n"
    )

    container_xml = (
        "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
        "<container version=\"1.0\" xmlns=\"urn:oasis:names:tc:opendocument:xmlns:container\">\n"
        "  <rootfiles>\n"
        "    <rootfile full-path=\"OEBPS/content.opf\" media-type=\"application/oebps-package+xml\" />\n"
        "  </rootfiles>\n"
        "</container>\n"
    )

    with zipfile.ZipFile(output_path, "w") as zf:
        mimetype_info = zipfile.ZipInfo("mimetype")
        mimetype_info.compress_type = zipfile.ZIP_STORED
        zf.writestr(mimetype_info, "application/epub+zip")

        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("OEBPS/content.opf", content_opf)
        zf.writestr("OEBPS/nav.xhtml", nav_doc)
        zf.writestr("OEBPS/style.css", css)
        zf.writestr("OEBPS/toc.ncx", toc_ncx)

        for section in section_files:
            if isinstance(section, FrontMatter):
                content = render_front_matter(section, lang)
            else:
                content = render_section(section.title, section.lines, lang)
            zf.writestr(f"OEBPS/{section.file_name}", content)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert TXT novel to EPUB.")
    parser.add_argument("input", help="Input TXT file path")
    parser.add_argument("-o", "--output", help="Output EPUB file path")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        return 1

    output_path = Path(args.output) if args.output else input_path.with_suffix(".epub")

    text = read_text(input_path)
    book = parse_book(text, input_path.stem)
    build_epub(book, output_path)
    print(f"EPUB saved to: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
