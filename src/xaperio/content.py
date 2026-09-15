import io
import posixpath
import re
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile

from bs4 import BeautifulSoup
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from xaperio import ingest

MAX_EPUB_ENTRIES = 10_000
MAX_EPUB_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_PDF_PAGES = 5_000
MAX_PDF_PAGE_CONTENT_BYTES = 50 * 1024 * 1024
MAX_EXTRACTED_CHARACTERS = 100_000_000
PDF_PAGE_GROUP_SIZE = 5


class ContentExtractionError(ValueError):
    pass


class NoExtractableTextError(ContentExtractionError):
    pass


def normalize_text(value):
    if not value:
        return ""

    text = unicodedata.normalize("NFKC", str(value))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(
        character
        for character in text
        if character in "\n\t" or not unicodedata.category(character).startswith("C")
    )
    paragraphs = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = re.sub(r"\s+", " ", paragraph).strip()
        paragraph = re.sub(r"\s+([,.;:!?])", r"\1", paragraph)
        if paragraph:
            paragraphs.append(paragraph)
    return "\n\n".join(paragraphs)


def extract_sections(data, format_name):
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("Book content must be bytes.")

    data = bytes(data)
    format_name = str(format_name).strip().lower()
    try:
        ingest.validate_file(data, format_name)
    except ValueError as exc:
        raise ContentExtractionError(str(exc)) from exc

    try:
        if format_name == "epub":
            sections = extract_epub_sections(data)
        elif format_name == "pdf":
            sections = extract_pdf_sections(data)
        elif format_name == "html":
            sections = extract_html_sections(data)
        else:
            raise ContentExtractionError(f"Unsupported content format: {format_name}")
    except ContentExtractionError:
        raise
    except (ET.ParseError, KeyError, OSError, PdfReadError, ValueError, zipfile.BadZipFile) as exc:
        raise ContentExtractionError(f"Could not extract {format_name} content: {exc}") from exc

    cleaned = []
    character_count = 0
    for section in sections:
        text = normalize_text(section.get("text"))
        if not text:
            continue
        character_count += len(text)
        if character_count > MAX_EXTRACTED_CHARACTERS:
            raise ContentExtractionError("Extracted book text exceeds the processing limit.")
        locator = section.get("locator")
        if not isinstance(locator, dict):
            raise ContentExtractionError("A content section has an invalid reader locator.")
        cleaned.append(
            {
                "order": len(cleaned),
                "title": normalize_text(section.get("title")) or f"Section {len(cleaned) + 1}",
                "locator": locator,
                "text": text,
            }
        )

    if not cleaned:
        raise NoExtractableTextError("The document does not contain extractable text.")
    return cleaned


def extract_html_sections(data, href=None):
    try:
        soup = BeautifulSoup(data, "html.parser")
    except Exception as exc:
        raise ContentExtractionError(f"Could not parse HTML content: {exc}") from exc

    document_title = ""
    if soup.title:
        document_title = normalize_text(soup.title.get_text(" ", strip=True))

    for element in soup.find_all(("script", "style", "template", "svg", "canvas", "noscript", "head")):
        element.decompose()

    root = soup.body or soup
    headings = list(root.find_all(("h1", "h2", "h3")))
    markers = []
    for position, heading in enumerate(headings, start=1):
        marker = f"\ue000XAPERIO_SECTION_{position}\ue001"
        title = normalize_text(heading.get_text(" ", strip=True)) or f"Section {position}"
        locator = {}
        if href:
            locator["href"] = href
        heading_id = heading.get("id")
        if heading_id:
            locator["anchor"] = str(heading_id)
        else:
            locator["section"] = position
        markers.append((marker, title, locator))
        heading.replace_with(marker)

    raw_text = root.get_text("\n")
    sections = []
    cursor = 0
    current_title = document_title or "Introduction"
    current_locator = {"section": 0}
    if href:
        current_locator["href"] = href

    for marker, title, locator in markers:
        marker_position = raw_text.find(marker, cursor)
        if marker_position < 0:
            continue
        text = normalize_text(raw_text[cursor:marker_position])
        if text:
            sections.append(
                {
                    "order": len(sections),
                    "title": current_title,
                    "locator": current_locator,
                    "text": text,
                }
            )
        cursor = marker_position + len(marker)
        current_title = title
        current_locator = locator

    text = normalize_text(raw_text[cursor:])
    if text:
        sections.append(
            {
                "order": len(sections),
                "title": current_title,
                "locator": current_locator,
                "text": text,
            }
        )
    return sections


def epub_package(archive):
    entries = archive.infolist()
    if len(entries) > MAX_EPUB_ENTRIES:
        raise ContentExtractionError("EPUB contains too many archive entries.")
    if sum(entry.file_size for entry in entries) > MAX_EPUB_EXPANDED_BYTES:
        raise ContentExtractionError("Expanded EPUB content exceeds the processing limit.")

    archive_names = {}
    for entry in entries:
        name = entry.filename.replace("\\", "/")
        normalized = posixpath.normpath(name)
        if name.startswith("/") or normalized == ".." or normalized.startswith("../"):
            raise ContentExtractionError("EPUB contains an unsafe archive path.")
        if entry.flag_bits & 0x1:
            raise ContentExtractionError("Encrypted EPUB entries are not supported.")
        if normalized in archive_names:
            raise ContentExtractionError("EPUB contains duplicate archive paths.")
        archive_names[normalized] = entry.filename

    container_name = archive_names.get("META-INF/container.xml")
    if not container_name:
        raise ContentExtractionError("EPUB is missing META-INF/container.xml.")
    container = ET.fromstring(archive.read(container_name))
    rootfile = container.find(".//{*}rootfile")
    if rootfile is None or not rootfile.get("full-path"):
        raise ContentExtractionError("EPUB does not identify an OPF package.")

    package_path = posixpath.normpath(rootfile.get("full-path").replace("\\", "/"))
    if package_path.startswith("/") or package_path == ".." or package_path.startswith("../"):
        raise ContentExtractionError("EPUB package path is unsafe.")
    package_name = archive_names.get(package_path)
    if not package_name:
        raise ContentExtractionError("EPUB OPF package is missing.")
    package = ET.fromstring(archive.read(package_name))

    manifest = {}
    manifest_element = package.find(".//{*}manifest")
    if manifest_element is None:
        raise ContentExtractionError("EPUB manifest is missing.")
    for item in manifest_element.findall("{*}item"):
        item_id = item.get("id")
        item_href = item.get("href")
        if item_id and item_href:
            manifest[item_id] = {
                "href": item_href,
                "media_type": item.get("media-type", "").lower(),
                "properties": set(item.get("properties", "").lower().split()),
            }

    spine = []
    spine_element = package.find(".//{*}spine")
    if spine_element is None:
        raise ContentExtractionError("EPUB reading spine is missing.")
    for itemref in spine_element.findall("{*}itemref"):
        item_id = itemref.get("idref")
        if item_id and itemref.get("linear", "yes").lower() != "no":
            spine.append(item_id)
    if not spine:
        raise ContentExtractionError("EPUB reading spine is empty.")
    return posixpath.dirname(package_path), manifest, spine, archive_names


def extract_epub_sections(data):
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ContentExtractionError("EPUB archive is corrupt.") from exc

    with archive:
        package_directory, manifest, spine, archive_names = epub_package(archive)
        sections = []
        seen_paths = set()
        for item_id in spine:
            item = manifest.get(item_id)
            if not item:
                raise ContentExtractionError(f"EPUB spine item '{item_id}' is missing from the manifest.")
            if item["media_type"] not in ("application/xhtml+xml", "text/html"):
                continue
            if "nav" in item["properties"]:
                continue

            parsed_href = urllib.parse.urlsplit(item["href"])
            if parsed_href.scheme or parsed_href.netloc:
                raise ContentExtractionError("EPUB manifest contains an external reading path.")
            href = urllib.parse.unquote(parsed_href.path).replace("\\", "/")
            archive_path = posixpath.normpath(posixpath.join(package_directory, href))
            if archive_path.startswith("/") or archive_path == ".." or archive_path.startswith("../"):
                raise ContentExtractionError("EPUB manifest contains an unsafe reading path.")
            if archive_path in seen_paths:
                continue
            seen_paths.add(archive_path)
            archive_name = archive_names.get(archive_path)
            if not archive_name:
                raise ContentExtractionError(f"EPUB reading document '{href}' is missing.")

            for section in extract_html_sections(archive.read(archive_name), href):
                section["order"] = len(sections)
                sections.append(section)
        return sections


def pdf_outline_starts(reader):
    starts = {}
    try:
        stack = [iter(reader.outline)]
        while stack:
            try:
                item = next(stack[-1])
            except StopIteration:
                stack.pop()
                continue
            if isinstance(item, list):
                stack.append(iter(item))
                continue
            page_number = reader.get_destination_page_number(item)
            title = normalize_text(getattr(item, "title", ""))
            if page_number is not None and page_number >= 0 and title:
                starts[page_number] = title
    except (AttributeError, KeyError, PdfReadError, ValueError):
        return {}
    return starts


def extract_pdf_sections(data):
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
    except Exception as exc:
        raise ContentExtractionError(f"Could not open PDF content: {exc}") from exc

    if reader.is_encrypted:
        try:
            decrypted = reader.decrypt("")
        except Exception as exc:
            raise ContentExtractionError("Encrypted PDF cannot be read without a password.") from exc
        if not decrypted:
            raise ContentExtractionError("Encrypted PDF cannot be read without a password.")

    page_count = len(reader.pages)
    if page_count > MAX_PDF_PAGES:
        raise ContentExtractionError("PDF contains too many pages to process safely.")
    if page_count == 0:
        return []

    outline_starts = pdf_outline_starts(reader)
    use_outlines = bool(outline_starts)
    sections = []
    group_text = []
    group_start = 0
    group_title = outline_starts.get(0)
    if not use_outlines:
        group_title = f"Pages 1-{min(PDF_PAGE_GROUP_SIZE, page_count)}"

    for page_index, page in enumerate(reader.pages):
        starts_new_group = page_index > 0 and (
            (use_outlines and page_index in outline_starts)
            or (not use_outlines and page_index % PDF_PAGE_GROUP_SIZE == 0)
        )
        if starts_new_group:
            text = normalize_text("\n\n".join(group_text))
            if text:
                sections.append(
                    {
                        "order": len(sections),
                        "title": group_title or f"Pages {group_start + 1}-{page_index}",
                        "locator": {"page_start": group_start + 1, "page_end": page_index},
                        "text": text,
                    }
                )
            group_text = []
            group_start = page_index
            if use_outlines:
                group_title = outline_starts[page_index]
            else:
                group_title = f"Pages {page_index + 1}-{min(page_index + PDF_PAGE_GROUP_SIZE, page_count)}"

        try:
            contents = page.get_contents()
            if contents is not None and len(contents.get_data()) > MAX_PDF_PAGE_CONTENT_BYTES:
                raise ContentExtractionError(
                    f"PDF page {page_index + 1} exceeds the content-stream processing limit."
                )
            page_text = normalize_text(page.extract_text() or "")
        except ContentExtractionError:
            raise
        except Exception as exc:
            raise ContentExtractionError(f"Could not extract PDF page {page_index + 1}: {exc}") from exc
        if page_text:
            group_text.append(page_text)

    text = normalize_text("\n\n".join(group_text))
    if text:
        sections.append(
            {
                "order": len(sections),
                "title": group_title or f"Pages {group_start + 1}-{page_count}",
                "locator": {"page_start": group_start + 1, "page_end": page_count},
                "text": text,
            }
        )
    return sections
