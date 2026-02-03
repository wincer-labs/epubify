import io
import tempfile
import unittest
from pathlib import Path
import zipfile

import epubify


class TestEpubify(unittest.TestCase):
    def test_parse_metadata_and_chapters(self):
        text = """书名：示例书
作者：张三

内容简介：
这是简介第一行。
这是简介第二行。

第一章 开始
第一行内容
第二行内容

第二章 继续
第三行内容
"""
        book = epubify.parse_book(text, "fallback")
        self.assertEqual(book.title, "示例书")
        self.assertEqual(book.author, "张三")
        self.assertEqual(book.intro, "这是简介第一行。\n这是简介第二行。")
        self.assertEqual(len(book.root_chapters), 2)
        self.assertEqual(book.root_chapters[0].title, "第一章 开始")
        self.assertEqual(book.root_chapters[0].lines, ["第一行内容", "第二行内容"])

    def test_parse_volume_structure(self):
        text = """书名：卷测试
作者：李四

第一卷 起始
第一章 章一
内容一
第二章 章二
内容二
"""
        book = epubify.parse_book(text, "fallback")
        self.assertEqual(len(book.volumes), 1)
        volume = book.volumes[0]
        self.assertEqual(volume.title, "第一卷 起始")
        self.assertEqual(len(volume.chapters), 2)
        self.assertEqual(volume.chapters[1].title, "第二章 章二")

    def test_chapter_prefix_in_content_not_heading(self):
        text = """书名：误判测试
作者：作者

第一章 开始
第三章 表格司机叫熊威，山东济南人，十三年驾龄，唐洼子路线不慎驾车冲入水库，车上25人。
下一行内容
"""
        book = epubify.parse_book(text, "fallback")
        self.assertEqual(len(book.root_chapters), 1)
        lines = book.root_chapters[0].lines
        self.assertIn(
            "第三章 表格司机叫熊威，山东济南人，十三年驾龄，唐洼子路线不慎驾车冲入水库，车上25人。",
            lines,
        )

    def test_read_text_gbk(self):
        sample = "书名：测试\n作者：某人\n".encode("gbk")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "book.txt"
            path.write_bytes(sample)
            text = epubify.read_text(path)
        self.assertIn("书名：测试", text)

    def test_build_epub_outputs(self):
        text = """书名：测试书
作者：作者

第一章 开始
第一行
"""
        book = epubify.parse_book(text, "fallback")
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "book.epub"
            epubify.build_epub(book, output_path)
            self.assertTrue(output_path.exists())
            with zipfile.ZipFile(output_path, "r") as zf:
                names = set(zf.namelist())
                self.assertIn("mimetype", names)
                self.assertIn("META-INF/container.xml", names)
                self.assertIn("OEBPS/content.opf", names)
                self.assertIn("OEBPS/nav.xhtml", names)
                self.assertIn("OEBPS/style.css", names)
                self.assertTrue(any(name.startswith("OEBPS/text/section_") for name in names))
                info = zf.getinfo("mimetype")
                self.assertEqual(info.compress_type, zipfile.ZIP_STORED)

    def test_front_matter_in_spine_without_nav(self):
        text = """书名：测试书
作者：作者

内容简介：
这里是简介。

第一章 开始
第一行
"""
        book = epubify.parse_book(text, "fallback")
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "book.epub"
            epubify.build_epub(book, output_path)
            with zipfile.ZipFile(output_path, "r") as zf:
                opf = zf.read("OEBPS/content.opf").decode("utf-8")
                self.assertNotIn('idref="nav"', opf)
                self.assertRegex(opf, r"<spine[^>]*>\s*<itemref idref=\"section_0001\"/>")
                front = zf.read("OEBPS/text/section_0001.xhtml").decode("utf-8")
                self.assertIn("测试书", front)
                self.assertIn("作者：作者", front)
                self.assertIn("这里是简介。", front)


if __name__ == "__main__":
    unittest.main()
