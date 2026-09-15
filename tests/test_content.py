import io
import unittest
import zipfile
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from xaperio import content


class ContentExtractionTests(unittest.TestCase):
    def epub_bytes(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            archive.writestr(
                "META-INF/container.xml",
                """<?xml version="1.0"?>
                <container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
                  <rootfiles><rootfile full-path="OPS/content.opf"/></rootfiles>
                </container>""",
            )
            archive.writestr(
                "OPS/content.opf",
                """<?xml version="1.0"?>
                <package xmlns="http://www.idpf.org/2007/opf">
                  <manifest>
                    <item id="second" href="second.xhtml" media-type="application/xhtml+xml"/>
                    <item id="first" href="first.xhtml" media-type="application/xhtml+xml"/>
                  </manifest>
                  <spine><itemref idref="first"/><itemref idref="second"/></spine>
                </package>""",
            )
            archive.writestr(
                "OPS/second.xhtml",
                "<html><body><h1 id='second'>Second</h1><p>Second chapter text.</p></body></html>",
            )
            archive.writestr(
                "OPS/first.xhtml",
                "<html><body><h1 id='first'>First</h1><p>First chapter text.</p></body></html>",
            )
        return output.getvalue()

    def pdf_bytes(self, page_texts, outlines=(), password=None):
        writer = PdfWriter()
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        font_reference = writer._add_object(font)
        for text in page_texts:
            page = writer.add_blank_page(width=612, height=792)
            page[NameObject("/Resources")] = DictionaryObject(
                {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_reference})}
            )
            if text:
                stream = DecodedStreamObject()
                escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                stream.set_data(f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1"))
                page[NameObject("/Contents")] = writer._add_object(stream)
        for title, page_number in outlines:
            writer.add_outline_item(title, page_number)
        if password:
            writer.encrypt(password)
        output = io.BytesIO()
        writer.write(output)
        return output.getvalue()

    def test_epub_follows_spine_and_preserves_locators(self):
        sections = content.extract_sections(self.epub_bytes(), "epub")

        self.assertEqual([section["title"] for section in sections], ["First", "Second"])
        self.assertEqual(sections[0]["locator"], {"href": "first.xhtml", "anchor": "first"})
        self.assertEqual(sections[1]["locator"], {"href": "second.xhtml", "anchor": "second"})

    def test_html_splits_headings_and_removes_non_visible_content(self):
        data = b"""<html><head><title>Example</title><style>.hidden {}</style></head><body>
            <p>Opening paragraph.</p><script>secret script text</script>
            <h1 id="one">First heading</h1><p>Visible <strong>content</strong>.</p>
            <h2>Second heading</h2><p>More content.</p></body></html>"""

        sections = content.extract_sections(data, "html")

        self.assertEqual([section["title"] for section in sections], ["Example", "First heading", "Second heading"])
        self.assertEqual(sections[1]["locator"], {"anchor": "one"})
        self.assertEqual(sections[2]["locator"], {"section": 2})
        self.assertNotIn("secret script text", " ".join(section["text"] for section in sections))
        self.assertIn("Visible content.", sections[1]["text"])

    def test_html_internal_locator_is_deterministic(self):
        data = b"<html><body><h1>Heading</h1><p>Text</p></body></html>"

        first = content.extract_sections(data, "html")
        second = content.extract_sections(data, "html")

        self.assertEqual(first[0]["locator"], {"section": 1})
        self.assertEqual(first, second)

    def test_pdf_uses_outline_titles_and_one_based_pages(self):
        data = self.pdf_bytes(
            ["First page", "Second page", "Third page"],
            [("Chapter One", 0), ("Chapter Two", 2)],
        )

        sections = content.extract_sections(data, "pdf")

        self.assertEqual([section["title"] for section in sections], ["Chapter One", "Chapter Two"])
        self.assertEqual(sections[0]["locator"], {"page_start": 1, "page_end": 2})
        self.assertEqual(sections[1]["locator"], {"page_start": 3, "page_end": 3})
        self.assertIn("Second page", sections[0]["text"])

        sections = content.extract_sections(
            self.pdf_bytes(["Front matter", "More front matter", "Chapter"], [("Chapter One", 2)]),
            "pdf",
        )
        self.assertEqual(sections[0]["title"], "Pages 1-2")
        self.assertEqual(sections[1]["title"], "Chapter One")

    def test_pdf_without_outlines_uses_page_ranges(self):
        data = self.pdf_bytes([f"Page {number}" for number in range(1, 7)])

        sections = content.extract_sections(data, "pdf")

        self.assertEqual([section["title"] for section in sections], ["Pages 1-5", "Pages 6-6"])
        self.assertEqual(sections[1]["locator"], {"page_start": 6, "page_end": 6})

    def test_image_only_pdf_has_no_extractable_text(self):
        with self.assertRaises(content.NoExtractableTextError):
            content.extract_sections(self.pdf_bytes([None]), "pdf")

    def test_password_protected_pdf_is_rejected(self):
        with self.assertRaisesRegex(content.ContentExtractionError, "without a password"):
            content.extract_sections(self.pdf_bytes(["Protected"], password="secret"), "pdf")

    def test_corrupt_epub_is_rejected(self):
        with self.assertRaises(content.ContentExtractionError):
            content.extract_sections(b"PK\x03\x04not an epub", "epub")

    def test_epub_expansion_limit_is_enforced(self):
        with patch.object(content, "MAX_EPUB_EXPANDED_BYTES", 1):
            with self.assertRaisesRegex(content.ContentExtractionError, "Expanded EPUB"):
                content.extract_epub_sections(self.epub_bytes())


if __name__ == "__main__":
    unittest.main()
