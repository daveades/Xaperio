import os
import re

import psycopg
from flask import Flask, Response, abort, jsonify, request, session
from werkzeug.http import http_date

from xaperio import books, db, embedding_client, ingest, progress, rate_limits, storage, users

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-secret-key")

BOOK_CONTENT_TYPES = {
    "epub": "application/epub+zip",
    "pdf": "application/pdf",
    "html": "text/html; charset=utf-8",
}

COVER_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def rate_limit_response(retry_after):
    response = jsonify({"error": f"too many attempts; try again in {retry_after} seconds"})
    response.status_code = 429
    response.headers["Retry-After"] = str(retry_after)
    return response


def current_user_id():
    return session.get("user_id")


def user_is_admin(user):
    admin_email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
    return bool(user and admin_email and user["email"].lower() == admin_email)


def can_access_book(user, book):
    return book and (book["moderation_status"] == "approved" or user_is_admin(user))


@app.get("/submissions")
def submissions():
    user_id = current_user_id()
    if user_id is None:
        return {"error": "not signed in"}, 401
    with db.connect() as conn:
        user = users.get_by_id(conn, user_id)
        return books.list_submissions(conn, user_id, user_is_admin(user))


@app.patch("/submissions/<int:book_id>")
def moderate_submission(book_id):
    user_id = current_user_id()
    if user_id is None:
        return {"error": "not signed in"}, 401
    data = request.get_json(silent=True) or {}
    with db.connect() as conn:
        user = users.get_by_id(conn, user_id)
        if not user_is_admin(user):
            return {"error": "administrator access required"}, 403
        try:
            reviewed = books.review_submission(
                conn,
                book_id,
                data.get("status", ""),
                data.get("review_note", ""),
                data,
            )
        except ValueError as e:
            return {"error": str(e)}, 400
    if not reviewed:
        return {"error": "pending submission not found"}, 404
    return {"ok": True}


@app.get("/books")
def browse():
    with db.connect() as conn:
        return books.list_books(conn)


@app.get("/search")
def search():
    query = request.args.get("q", "")
    query_embedding = None
    if query.strip():
        try:
            query_embedding = embedding_client.embed_query(query)
        except embedding_client.EmbeddingClientError:
            pass
    with db.connect() as conn:
        try:
            return books.search_books(
                conn,
                query,
                query_embedding,
                embedding_client.MODEL_NAME,
                embedding_client.rerank,
                embedding_client.extract_answers,
            )
        except embedding_client.AnswerClientError:
            try:
                return books.search_books(
                    conn,
                    query,
                    query_embedding,
                    embedding_client.MODEL_NAME,
                    embedding_client.rerank,
                )
            except embedding_client.EmbeddingClientError:
                pass
        except embedding_client.EmbeddingClientError:
            pass
        return books.search_books(
            conn,
            query,
            query_embedding,
            embedding_client.MODEL_NAME,
        )


@app.get("/books/<int:book_id>")
def details(book_id):
    with db.connect() as conn:
        book = books.get_book(conn, book_id)
        user_id = current_user_id()
        user = users.get_by_id(conn, user_id) if user_id is not None else None
    if not can_access_book(user, book):
        abort(404)
    book = dict(book)
    book.pop("submitted_by", None)
    if user_id is not None:
        with db.connect() as conn:
            saved = progress.get_progress(conn, user_id, book_id)
        book["progress"] = {
            "position": saved["position"],
            "format": saved["format"],
        } if saved else None
        if saved and saved.get("format") and saved["format"] in (book.get("formats") or []):
            book["read_format"] = saved["format"]
    return book


@app.delete("/books/<int:book_id>")
def delete_book(book_id):
    user_id = current_user_id()
    if user_id is None:
        return {"error": "not signed in"}, 401
    with db.connect() as conn:
        user = users.get_by_id(conn, user_id)
        if not user_is_admin(user):
            return {"error": "administrator access required"}, 403
        object_keys = books.delete_book(conn, book_id)
    if object_keys is None:
        return {"error": "book not found"}, 404
    storage.delete_objects(object_keys)
    return {"ok": True}


@app.get("/books/<int:book_id>/read")
def read(book_id):
    with db.connect() as conn:
        book = books.get_book(conn, book_id)
        user_id = current_user_id()
        user = users.get_by_id(conn, user_id) if user_id is not None else None
        if not can_access_book(user, book):
            abort(404)
        location = books.get_book_file(conn, book_id, request.args.get("format"))

    if location is None:
        abort(404)

    stored_object = storage.get_object(location, request.headers.get("Range"))
    if stored_object is None:
        abort(404)

    body = stored_object["Body"]
    status = 206 if stored_object.get("ContentRange") else 200
    response = Response(
        body.iter_chunks(chunk_size=64 * 1024),
        status=status,
        content_type=(
            stored_object.get("ContentType")
            or BOOK_CONTENT_TYPES.get(os.path.splitext(location)[1].lstrip("."))
            or "application/octet-stream"
        ),
    )
    response.call_on_close(body.close)
    if stored_object.get("ContentRange"):
        response.headers["Content-Range"] = stored_object["ContentRange"]
    if stored_object.get("ContentLength") is not None:
        response.headers["Content-Length"] = str(stored_object["ContentLength"])
    if stored_object.get("ETag"):
        response.headers["ETag"] = stored_object["ETag"]
    if stored_object.get("LastModified"):
        response.headers["Last-Modified"] = http_date(stored_object["LastModified"])
    response.headers["Accept-Ranges"] = "bytes"
    if response.mimetype == "text/html":
        response.headers["Content-Security-Policy"] = "sandbox allow-same-origin"
    return response

@app.get("/books/<int:book_id>/cover")
def cover(book_id):
    with db.connect() as conn:
        book = books.get_book(conn, book_id)
        user_id = current_user_id()
        user = users.get_by_id(conn, user_id) if user_id is not None else None
        if not can_access_book(user, book):
            abort(404)
        location = books.get_book_cover(conn, book_id)
    if location is None:
        abort(404)
    stored_object = storage.get_object(location)
    if stored_object is None:
        abort(404)

    body = stored_object["Body"]
    response = Response(
        body.iter_chunks(chunk_size=64 * 1024),
        content_type=(
            stored_object.get("ContentType")
            or COVER_CONTENT_TYPES.get(os.path.splitext(location)[1].lower())
            or "application/octet-stream"
        ),
    )
    response.call_on_close(body.close)
    if stored_object.get("ContentLength") is not None:
        response.headers["Content-Length"] = str(stored_object["ContentLength"])
    if stored_object.get("ETag"):
        response.headers["ETag"] = stored_object["ETag"]
    if stored_object.get("LastModified"):
        response.headers["Last-Modified"] = http_date(stored_object["LastModified"])
    response.headers["Accept-Ranges"] = "bytes"
    return response


@app.post("/auth/register")
def register():
    email = request.json.get("email", "") if request.is_json else ""
    password = request.json.get("password", "") if request.is_json else ""
    with db.connect() as conn:
        retry_after = rate_limits.consume(conn, "register", email.strip().lower(), 5, 60 * 60)
    if retry_after is not None:
        return rate_limit_response(retry_after)
    try:
        with db.connect() as conn:
            user_id = users.register(conn, email, password)
    except ValueError as e:
        return {"error": str(e)}, 400
    except psycopg.errors.UniqueViolation:
        return {"error": "an account with that email already exists"}, 409
    session["user_id"] = user_id
    return {"id": user_id, "email": email.strip().lower()}, 201


@app.post("/auth/login")
def login():
    email = request.json.get("email", "") if request.is_json else ""
    password = request.json.get("password", "") if request.is_json else ""
    with db.connect() as conn:
        retry_after = rate_limits.consume(conn, "login", email.strip().lower(), 10, 15 * 60)
    if retry_after is not None:
        return rate_limit_response(retry_after)
    if not email or not password:
        return {"error": "email and password are required"}, 400
    with db.connect() as conn:
        user_id = users.authenticate(conn, email, password)
    if user_id is None:
        return {"error": "email or password is incorrect"}, 401
    session["user_id"] = user_id
    return {"id": user_id}

@app.post("/auth/logout")
def logout():
    session.clear()
    return {"ok": True}

@app.get("/auth/me")
def me():
    user_id = current_user_id()
    if user_id is None:
        return {"user": None}
    with db.connect() as conn:
        user = users.get_by_id(conn, user_id)
    if user is None:
        session.clear()
        return {"user": None}
    return {
        "user": {
            "id": user["id"],
            "email": user["email"],
            "is_admin": user_is_admin(user),
        }
    }


@app.get("/books/history")
@app.get("/library")
def library_history():
    user_id = current_user_id()
    if user_id is None:
        return {"error": "not signed in"}, 401
    with db.connect() as conn:
        return progress.get_user_history(conn, user_id)


@app.get("/books/<int:book_id>/progress")
def get_progress(book_id):
    user_id = current_user_id()
    if user_id is None:
        return {"error": "not signed in"}, 401
    with db.connect() as conn:
        saved = progress.get_progress(conn, user_id, book_id)
    if saved is None:
        return {"position": None}
    return {"position": saved["position"], "format": saved["format"]}


@app.put("/books/<int:book_id>/progress")
def put_progress(book_id):
    user_id = current_user_id()
    if user_id is None:
        return {"error": "not signed in"}, 401
    data = request.get_json(silent=True) or {}
    position = data.get("position")
    fmt = data.get("format")
    if not position or not fmt:
        return {"error": "position and format are required"}, 400
    with db.connect() as conn:
        progress.save_progress(conn, user_id, book_id, position, fmt)
    return {"ok": True}


def _slug(title):
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "book"


def _stem(title):
    base = _slug(title)
    stem, n = base, 1
    while storage.prefix_exists(f"books/{stem}."):
        n += 1
        stem = f"{base}-{n}"
    return stem


def _lines(raw):
    return [line.strip() for line in raw.splitlines() if line.strip()]


@app.post("/books/inspect")
def inspect():
    user_id = current_user_id()
    if user_id is None:
        return {"error": "not signed in"}, 401
    with db.connect() as conn:
        retry_after = rate_limits.consume(conn, "inspect", user_id, 10, 60 * 60)
    if retry_after is not None:
        return rate_limit_response(retry_after)

    file = None
    format_name = None
    for name in ("file", "epub", "pdf", "html"):
        if name in request.files and request.files[name].filename:
            file = request.files[name]
            format_name = name if name in ("epub", "pdf", "html") else None
            break

    if not file:
        for name, f in request.files.items():
            if f.filename:
                file = f
                format_name = name if name in ("epub", "pdf", "html") else None
                break

    if not file:
        return {"error": "no file uploaded"}, 400

    if not format_name:
        ext = os.path.splitext(file.filename)[1].lower().lstrip(".")
        format_name = ext if ext in ingest.SUPPORTED_FORMATS else "epub"

    file_bytes = file.read()
    try:
        ingest.validate_file(file_bytes, format_name)
        meta = ingest.extract_metadata(file_bytes, format_name)
    except ValueError as e:
        return {"error": str(e)}, 400

    clean_name = os.path.splitext(file.filename)[0].replace("-", " ").replace("_", " ").title()
    return {
        "format": format_name,
        "title": meta.get("title") or clean_name,
        "authors": meta.get("authors") or [],
        "description": meta.get("description") or "",
        "language": meta.get("language") or "en",
        "pub_year": meta.get("pub_year"),
        "publisher": meta.get("publisher") or "",
        "topics": meta.get("topics") or [],
        "license_name": meta.get("license_name") or "Open Access",
        "license_url": meta.get("license_url") or "https://creativecommons.org/",
        "has_cover": bool(meta.get("cover_bytes")),
    }


@app.post("/books")
def add():
    user_id = current_user_id()
    if user_id is None:
        return {"error": "not signed in"}, 401
    with db.connect() as conn:
        retry_after = rate_limits.consume(conn, "submit", user_id, 5, 24 * 60 * 60)
    if retry_after is not None:
        return rate_limit_response(retry_after)

    form = request.form
    uploads = {}
    extracted_meta = {}

    # Gather and validate uploaded format files
    for name, f in request.files.items():
        if name != "cover" and f.filename:
            fmt = name.lower().strip()
            # If field name was generic 'file', guess format from extension
            if fmt == "file":
                fmt = os.path.splitext(f.filename)[1].lower().lstrip(".")
            if fmt not in ingest.SUPPORTED_FORMATS:
                return {"error": f"unsupported book format: '{fmt}'"}, 400
            file_bytes = f.read()
            try:
                ingest.validate_file(file_bytes, fmt)
            except ValueError as e:
                return {"error": str(e)}, 400
            uploads[fmt] = file_bytes
            # Extract metadata from primary format
            if not extracted_meta:
                extracted_meta = ingest.extract_metadata(file_bytes, fmt)

    if not uploads:
        return {"error": "at least one valid book file (epub, pdf, html) is required"}, 400

    # Validate uploaded cover if provided
    cover = request.files.get("cover")
    cover_bytes = None
    cover_ext = ".jpg"
    if cover and cover.filename:
        cover_bytes = cover.read()
        try:
            ingest.validate_file(cover_bytes, "cover")
            if cover_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
                cover_ext = ".png"
            elif cover_bytes.startswith(b"RIFF"):
                cover_ext = ".webp"
            else:
                cover_ext = ".jpg"
        except ValueError as e:
            return {"error": str(e)}, 400
    elif extracted_meta.get("cover_bytes"):
        cover_bytes = extracted_meta["cover_bytes"]
        cover_ext = extracted_meta.get("cover_ext") or ".jpg"

    # Merge form overrides with extracted metadata
    title = form.get("title", "").strip() or extracted_meta.get("title")
    if not title:
        return {"error": "title is required and could not be extracted"}, 400

    language = form.get("language", "").strip() or extracted_meta.get("language") or "en"
    language = ingest.normalize_language(language)

    description = form.get("description", "").strip() or extracted_meta.get("description") or None

    year_str = form.get("pub_year", "").strip()
    pub_year = None
    if year_str:
        if not year_str.isdigit():
            return {"error": "pub_year must be a number"}, 400
        pub_year = int(year_str)
    elif extracted_meta.get("pub_year") is not None:
        pub_year = extracted_meta["pub_year"]

    if pub_year is not None and not 1 <= pub_year <= 2100:
        return {"error": "pub_year must be between 1 and 2100"}, 400

    publisher = form.get("publisher", "").strip() or extracted_meta.get("publisher") or None
    edition = form.get("edition", "").strip() or None

    # Authors
    authors_raw = form.get("authors", "").strip()
    if authors_raw:
        authors = _lines(authors_raw)
    else:
        authors = extracted_meta.get("authors") or ["Unknown"]

    # Topics
    topics_raw = form.get("topics", "").strip()
    if topics_raw:
        topics = _lines(topics_raw)
    else:
        topics = extracted_meta.get("topics") or ["General"]

    # License
    license_name = (
        form.get("license_name", "").strip()
        or extracted_meta.get("license_name")
        or "Open Access"
    )
    license_url = (
        form.get("license_url", "").strip()
        or extracted_meta.get("license_url")
        or "https://creativecommons.org/"
    )

    stem = _stem(title)

    # Save format files
    formats = {}
    for fmt, data in uploads.items():
        rel_path = f"books/{stem}.{fmt}"
        storage.put_object(rel_path, data, BOOK_CONTENT_TYPES[fmt])
        formats[fmt] = rel_path

    # Save cover image
    cover_ref = None
    if cover_bytes:
        cover_ref = f"covers/{stem}{cover_ext}"
        content_type = COVER_CONTENT_TYPES.get(cover_ext, "application/octet-stream")
        storage.put_object(cover_ref, cover_bytes, content_type)

    book = {
        "title": title,
        "language": language,
        "description": description,
        "pub_year": pub_year,
        "publisher": publisher,
        "edition": edition,
        "cover_ref": cover_ref,
        "authors": authors,
        "topics": topics,
        "formats": formats,
        "license": {"name": license_name, "url": license_url},
        "moderation_status": "pending",
        "submitted_by": user_id,
    }

    try:
        with db.connect() as conn:
            return {
                "id": books.create_book(conn, book),
                "moderation_status": "pending",
            }, 201
    except (ValueError, KeyError) as e:
        return {"error": str(e)}, 400
