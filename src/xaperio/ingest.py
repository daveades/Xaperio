import io
import os
import re
import xml.etree.ElementTree as ET
import zipfile
from html.parser import HTMLParser

import pymupdf
from PIL import Image, ImageOps

SUPPORTED_FORMATS = ("epub", "pdf", "html")
MAX_BOOK_SIZE = 100 * 1024 * 1024  # 100 MB
MAX_COVER_SIZE = 10 * 1024 * 1024  # 10 MB
COVER_WIDTH = 480
COVER_HEIGHT = 720
MAX_HTML_COVER_BYTES = 1024 * 1024

IMAGE_SIGNATURES = {
    b"\xff\xd8\xff": ".jpg",
    b"\x89PNG\r\n\x1a\n": ".png",
    b"RIFF": ".webp",  # WebP starts with RIFF....WEBP
}


def normalize_language(raw):
    if not raw:
        return "en"
    code = raw.strip().lower()
    # Handle language codes like en-US, pt-BR, fr_FR
    if "-" in code:
        code = code.split("-")[0]
    elif "_" in code:
        code = code.split("_")[0]
    # PostgreSQL schema constraint: language = lower(language) and char_length between 2 and 3
    if 2 <= len(code) <= 3 and code.isalpha():
        return code
    return "en"


def validate_file(stream_or_bytes, format_name, max_size=MAX_BOOK_SIZE):
    if isinstance(stream_or_bytes, (bytes, bytearray)):
        data = stream_or_bytes
    else:
        stream_or_bytes.seek(0)
        data = stream_or_bytes.read()
        stream_or_bytes.seek(0)

    if not data:
        raise ValueError(f"File for format '{format_name}' is empty.")

    if len(data) > max_size:
        raise ValueError(
            f"File for format '{format_name}' exceeds maximum allowed size ({max_size // (1024*1024)}MB)."
        )

    fmt = format_name.lower().strip()
    if fmt == "pdf":
        if not data.startswith(b"%PDF-"):
            raise ValueError("Uploaded file is not a valid PDF document (missing %PDF- header).")
    elif fmt == "epub":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = z.namelist()
                if "mimetype" not in names:
                    raise ValueError("Invalid EPUB: missing 'mimetype' file.")
                mimetype = z.read("mimetype").strip().decode("ascii", errors="ignore")
                if "application/epub+zip" not in mimetype:
                    raise ValueError(f"Invalid EPUB mimetype: expected 'application/epub+zip', got '{mimetype}'.")
                if "META-INF/container.xml" not in names:
                    raise ValueError("Invalid EPUB: missing 'META-INF/container.xml'.")
        except zipfile.BadZipFile:
            raise ValueError("Uploaded file is not a valid EPUB archive (corrupt zip).")
    elif fmt == "html":
        # Check that it decodes to text and contains basic html markers
        try:
            text = data.decode("utf-8", errors="replace").lower()
            if not any(tag in text for tag in ("<html", "<!doctype", "<body", "<head", "<p", "<div")):
                raise ValueError("Uploaded file does not appear to contain valid HTML.")
        except Exception:
            raise ValueError("Uploaded file cannot be decoded as HTML text.")
    elif fmt in ("cover", "image"):
        valid = False
        for sig in IMAGE_SIGNATURES:
            if data.startswith(sig):
                if sig == b"RIFF" and b"WEBP" not in data[:16]:
                    continue
                valid = True
                break
        if not valid:
            raise ValueError("Cover file must be a valid JPEG, PNG, or WebP image.")
    else:
        raise ValueError(f"Unsupported format: '{format_name}'. Supported formats: {', '.join(SUPPORTED_FORMATS)}")

    return True


class _HTMLMetaExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = None
        self.in_title = False
        self.authors = []
        self.description = None

    def handle_starttag(self, tag, attrs):
        attr_dict = {k.lower(): v for k, v in attrs if v is not None}
        if tag == "title":
            self.in_title = True
        elif tag == "meta":
            name = attr_dict.get("name", "").lower()
            content = attr_dict.get("content", "").strip()
            if not content:
                return
            if name == "author":
                self.authors.append(content)
            elif name == "description":
                self.description = content

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title and not self.title:
            self.title = data.strip()


def extract_epub_metadata(data):
    meta = {
        "title": None,
        "authors": [],
        "description": None,
        "language": None,
        "pub_year": None,
        "publisher": None,
        "topics": [],
        "license_name": None,
        "cover_bytes": None,
        "cover_ext": None,
    }

    with zipfile.ZipFile(io.BytesIO(data)) as z:
        try:
            container_xml = z.read("META-INF/container.xml")
            container = ET.fromstring(container_xml)
            rootfile = container.find(
                "{urn:oasis:names:tc:opendocument:xmlns:container}rootfiles/{urn:oasis:names:tc:opendocument:xmlns:container}rootfile"
            )
            opf_path = rootfile.attrib["full-path"]
        except Exception:
            return meta

        try:
            opf_dir = os.path.dirname(opf_path)
            opf_xml = z.read(opf_path)
            opf = ET.fromstring(opf_xml)
        except Exception:
            return meta

        dc_ns = "{http://purl.org/dc/elements/1.1/}"
        metadata = opf.find("{http://www.idpf.org/2007/opf}metadata")
        if metadata is None:
            metadata = opf.find("metadata")

        if metadata is not None:
            # Title
            title_elem = metadata.find(f"{dc_ns}title")
            if title_elem is not None and title_elem.text:
                meta["title"] = title_elem.text.strip()

            # Authors
            for creator in metadata.findall(f"{dc_ns}creator"):
                if creator.text and creator.text.strip():
                    meta["authors"].append(creator.text.strip())

            # Description
            desc_elem = metadata.find(f"{dc_ns}description")
            if desc_elem is not None and desc_elem.text:
                # Strip basic html tags if description contains markup
                clean_desc = re.sub(r"<[^>]+>", "", desc_elem.text).strip()
                meta["description"] = clean_desc

            # Language
            lang_elem = metadata.find(f"{dc_ns}language")
            if lang_elem is not None and lang_elem.text:
                meta["language"] = normalize_language(lang_elem.text)

            # Date / Year
            date_elem = metadata.find(f"{dc_ns}date")
            if date_elem is not None and date_elem.text:
                year_match = re.search(r"\b(\d{4})\b", date_elem.text)
                if year_match:
                    meta["pub_year"] = int(year_match.group(1))

            # Publisher
            pub_elem = metadata.find(f"{dc_ns}publisher")
            if pub_elem is not None and pub_elem.text:
                meta["publisher"] = pub_elem.text.strip()

            # Topics / Subjects
            for subj in metadata.findall(f"{dc_ns}subject"):
                if subj.text and subj.text.strip():
                    meta["topics"].append(subj.text.strip())

            # Rights / License
            rights_elem = metadata.find(f"{dc_ns}rights")
            if rights_elem is not None and rights_elem.text:
                meta["license_name"] = rights_elem.text.strip()

        # Extract Cover Image from Manifest
        manifest = opf.find("{http://www.idpf.org/2007/opf}manifest")
        if manifest is not None:
            cover_item = None

            # 1. Check for properties="cover-image"
            for item in manifest:
                props = item.attrib.get("properties", "").lower().split()
                if "cover-image" in props:
                    cover_item = item
                    break

            # 2. Check for meta name="cover"
            if cover_item is None and metadata is not None:
                for m in metadata:
                    if m.attrib.get("name") == "cover":
                        cover_id = m.attrib.get("content")
                        for item in manifest:
                            if item.attrib.get("id") == cover_id:
                                cover_item = item
                                break
                        break

            # 3. Check for id or href containing 'cover' and image media-type
            if cover_item is None:
                for item in manifest:
                    mtype = item.attrib.get("media-type", "").lower()
                    item_id = item.attrib.get("id", "").lower()
                    href = item.attrib.get("href", "").lower()
                    if mtype.startswith("image/") and ("cover" in item_id or "cover" in href):
                        cover_item = item
                        break

            if cover_item is not None:
                href = cover_item.attrib.get("href")
                if href:
                    img_path = os.path.normpath(os.path.join(opf_dir, href)).replace("\\", "/")
                    if img_path in z.namelist():
                        meta["cover_bytes"] = z.read(img_path)
                        mtype = cover_item.attrib.get("media-type", "").lower()
                        if "png" in mtype:
                            meta["cover_ext"] = ".png"
                        elif "webp" in mtype:
                            meta["cover_ext"] = ".webp"
                        else:
                            meta["cover_ext"] = ".jpg"

    return meta


def _jpeg_cover(image):
    image = ImageOps.exif_transpose(image)
    image.thumbnail((COVER_WIDTH, COVER_HEIGHT), Image.Resampling.LANCZOS)
    rgba = image.convert("RGBA")
    rgb = Image.new("RGB", rgba.size, "white")
    rgb.paste(rgba, mask=rgba.getchannel("A"))
    output = io.BytesIO()
    rgb.save(output, "JPEG", quality=82, optimize=True, progressive=True)
    return output.getvalue()


def _pdf_cover(data):
    with pymupdf.open(stream=data, filetype="pdf") as document:
        if not document.page_count:
            return None
        page = document.load_page(0)
        scale = COVER_WIDTH / max(page.rect.width, 1)
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        return _jpeg_cover(image)


def _html_cover(data):
    html = data[:MAX_HTML_COVER_BYTES].decode("utf-8", errors="replace")
    output = io.BytesIO()
    writer = pymupdf.DocumentWriter(output)
    page_rect = pymupdf.Rect(0, 0, COVER_WIDTH, COVER_HEIGHT)
    content_rect = pymupdf.Rect(24, 24, COVER_WIDTH - 24, COVER_HEIGHT - 24)
    device = writer.begin_page(page_rect)
    story = pymupdf.Story(
        html=html,
        user_css="body { margin: 0; font-family: sans-serif; font-size: 14px; } img { max-width: 100%; height: auto; }",
    )
    story.place(content_rect)
    story.draw(device)
    writer.end_page()
    writer.close()
    with pymupdf.open(stream=output.getvalue(), filetype="pdf") as document:
        pixmap = document.load_page(0).get_pixmap(alpha=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        return _jpeg_cover(image)


def extract_pdf_metadata(data):
    meta = {
        "title": None,
        "authors": [],
        "description": None,
        "language": None,
        "pub_year": None,
        "publisher": None,
        "topics": [],
        "license_name": None,
        "cover_bytes": None,
        "cover_ext": None,
    }

    # Search for Document Information entries
    title_matches = re.findall(rb"/Title\s*\(([^)]+)\)", data)
    if title_matches:
        try:
            meta["title"] = title_matches[0].decode("latin-1", errors="ignore").strip()
        except Exception:
            pass

    author_matches = re.findall(rb"/Author\s*\(([^)]+)\)", data)
    if author_matches:
        try:
            raw_authors = author_matches[0].decode("latin-1", errors="ignore").strip()
            # Split authors if separated by comma or semicolon
            for a in re.split(r"[,;]\s*", raw_authors):
                if a.strip():
                    meta["authors"].append(a.strip())
        except Exception:
            pass

    subject_matches = re.findall(rb"/Subject\s*\(([^)]+)\)", data)
    if subject_matches:
        try:
            meta["description"] = subject_matches[0].decode("latin-1", errors="ignore").strip()
        except Exception:
            pass

    # Search for creation date
    date_matches = re.findall(rb"/CreationDate\s*\(D:(\d{4})", data)
    if date_matches:
        try:
            meta["pub_year"] = int(date_matches[0].decode("ascii"))
        except Exception:
            pass

    # Search for XMP Dublin Core metadata
    xmp_titles = re.findall(rb"<dc:title[^>]*>.*?<rdf:li[^>]*>(.*?)</rdf:li>", data, re.DOTALL)
    if xmp_titles and not meta["title"]:
        try:
            meta["title"] = xmp_titles[0].decode("utf-8", errors="ignore").strip()
        except Exception:
            pass

    xmp_creators = re.findall(rb"<dc:creator[^>]*>.*?<rdf:li[^>]*>(.*?)</rdf:li>", data, re.DOTALL)
    if xmp_creators and not meta["authors"]:
        for c in xmp_creators:
            try:
                name = c.decode("utf-8", errors="ignore").strip()
                if name:
                    meta["authors"].append(name)
            except Exception:
                pass

    try:
        meta["cover_bytes"] = _pdf_cover(data)
        if meta["cover_bytes"]:
            meta["cover_ext"] = ".jpg"
    except Exception:
        pass

    return meta


def extract_html_metadata(data):
    meta = {
        "title": None,
        "authors": [],
        "description": None,
        "language": None,
        "pub_year": None,
        "publisher": None,
        "topics": [],
        "license_name": None,
        "cover_bytes": None,
        "cover_ext": None,
    }
    try:
        text = data.decode("utf-8", errors="replace")
        parser = _HTMLMetaExtractor()
        parser.feed(text[:100000])  # Scan header portion
        if parser.title:
            meta["title"] = parser.title
        if parser.authors:
            meta["authors"] = parser.authors
        if parser.description:
            meta["description"] = parser.description
    except Exception:
        pass
    try:
        meta["cover_bytes"] = _html_cover(data)
        if meta["cover_bytes"]:
            meta["cover_ext"] = ".jpg"
    except Exception:
        pass
    return meta


def extract_metadata(stream_or_bytes, format_name):
    if isinstance(stream_or_bytes, (bytes, bytearray)):
        data = stream_or_bytes
    else:
        stream_or_bytes.seek(0)
        data = stream_or_bytes.read()
        stream_or_bytes.seek(0)

    fmt = format_name.lower().strip()
    if fmt == "epub":
        return extract_epub_metadata(data)
    elif fmt == "pdf":
        return extract_pdf_metadata(data)
    elif fmt == "html":
        return extract_html_metadata(data)
    return {
        "title": None,
        "authors": [],
        "description": None,
        "language": "en",
        "pub_year": None,
        "publisher": None,
        "topics": [],
        "license_name": None,
        "cover_bytes": None,
        "cover_ext": None,
    }


def ingest_file(conn, file_path, title=None, authors=None, language=None, topics=None):
    from xaperio import books, storage

    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    ext = os.path.splitext(file_path)[1].lower().lstrip(".")
    if ext not in SUPPORTED_FORMATS:
        raise ValueError(f"Unsupported format '{ext}'. Supported: {', '.join(SUPPORTED_FORMATS)}")

    with open(file_path, "rb") as f:
        data = f.read()

    validate_file(data, ext)
    meta = extract_metadata(data, ext)

    pub_year = meta.get("pub_year")
    if pub_year is not None and not 1 <= pub_year <= 2100:
        raise ValueError("pub_year must be between 1 and 2100")

    final_title = (
        title
        or meta.get("title")
        or os.path.splitext(os.path.basename(file_path))[0].replace("-", " ").replace("_", " ").title()
    )
    final_authors = authors or meta.get("authors") or ["Unknown"]
    final_topics = topics or meta.get("topics") or ["General"]
    final_language = normalize_language(language or meta.get("language") or "en")

    # Generate slug stem
    from xaperio.routes import _stem

    stem = _stem(final_title)

    rel_format_path = f"books/{stem}.{ext}"
    content_types = {
        "epub": "application/epub+zip",
        "pdf": "application/pdf",
        "html": "text/html; charset=utf-8",
    }
    storage.put_object(rel_format_path, data, content_types[ext])

    cover_ref = None
    if meta.get("cover_bytes"):
        c_ext = meta.get("cover_ext") or ".jpg"
        cover_ref = f"covers/{stem}{c_ext}"
        cover_content_types = {
            ".jpg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }
        storage.put_object(cover_ref, meta["cover_bytes"], cover_content_types[c_ext])

    book = {
        "title": final_title,
        "language": final_language,
        "description": meta.get("description"),
        "pub_year": pub_year,
        "publisher": meta.get("publisher"),
        "edition": None,
        "cover_ref": cover_ref,
        "authors": final_authors,
        "topics": final_topics,
        "formats": {ext: rel_format_path},
        "license": {
            "name": meta.get("license_name") or "Open Access",
            "url": "https://creativecommons.org/",
        },
    }

    return books.create_book(conn, book)


if __name__ == "__main__":
    import argparse
    from xaperio import db

    parser = argparse.ArgumentParser(description="Xaperio book ingestion CLI")
    parser.add_argument("file", help="Path to the book file to ingest (.epub, .pdf, .html)")
    parser.add_argument("--title", help="Override book title")
    parser.add_argument("--author", action="append", help="Override book author(s)")
    parser.add_argument("--topic", action="append", help="Override book topic(s)")
    parser.add_argument("--language", help="Override book language")
    args = parser.parse_args()

    with db.connect() as db_conn:
        book_id = ingest_file(
            db_conn,
            args.file,
            title=args.title,
            authors=args.author,
            language=args.language,
            topics=args.topic,
        )
        print(f"Successfully ingested book ID {book_id} from {args.file}")
