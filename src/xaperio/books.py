import json
import re


READABLE_FORMATS = ("epub", "pdf", "html")
SEARCH_BOOK_LIMIT = 25
SEARCH_CANDIDATE_LIMIT = 100
SEARCH_SECTION_LIMIT = 3
SEARCH_EXCERPT_CHARACTERS = 360
MIN_SEMANTIC_SCORE = 0.30
EXCLUDED_SEARCH_SECTION_TITLES = (
    "contents",
    "glossary",
    "preface",
    "table of contents",
)
METADATA_WEIGHT = 0.45
LEXICAL_WEIGHT = 0.30
SEMANTIC_WEIGHT = 0.25
RERANK_CANDIDATE_LIMIT = 30
RERANK_CHUNK_LIMIT = 10
RERANK_WINDOW_LIMIT = 60
RERANK_PASSAGE_LIMIT = 30
RERANK_PASSAGES_PER_SECTION = 2
RERANK_WINDOWS_PER_CHUNK = 8
RERANK_WINDOW_SENTENCES = 1
RERANK_WINDOW_OVERLAP = 0
MIN_RERANK_SCORE = 0.20
MIN_ANSWER_SCORE = 0.20



def list_submissions(conn, user_id, is_admin):
    condition = "b.moderation_status = 'pending'" if is_admin else "b.submitted_by = %s"
    params = () if is_admin else (user_id,)
    with conn.cursor() as cur:
        cur.execute(f"""
            select b.id, b.title, b.moderation_status, b.review_note, b.submitted_at,
                   (select array_agg(a.name order by a.name)
                      from book_author ba join author a on a.id = ba.author_id
                     where ba.book_id = b.id) as authors
            from books b
            where {condition}
            order by b.submitted_at desc
        """, params)
        rows = cur.fetchall()
        return [
            {
                **row,
                "submitted_at": row["submitted_at"].isoformat(),
            }
            for row in rows
        ]


def review_submission(conn, book_id, status, review_note, metadata):
    if status not in ("approved", "rejected"):
        raise ValueError("status must be approved or rejected")
    if not isinstance(review_note, str):
        raise ValueError("review_note must be text")

    review_note = review_note.strip()
    if status == "rejected" and not review_note:
        raise ValueError("a rejection reason is required")

    title_value = metadata.get("title", "")
    language_value = metadata.get("language", "")
    author_values = metadata.get("authors", [])
    topic_values = metadata.get("topics", [])
    if not isinstance(title_value, str) or not isinstance(language_value, str):
        raise ValueError("title and language must be text")
    if not isinstance(author_values, list) or not all(isinstance(name, str) for name in author_values):
        raise ValueError("authors must be a list of names")
    if not isinstance(topic_values, list) or not all(isinstance(name, str) for name in topic_values):
        raise ValueError("topics must be a list of names")

    title = title_value.strip()
    language = language_value.strip().lower()
    authors = [name.strip() for name in author_values if name.strip()]
    topics = [name.strip() for name in topic_values if name.strip()]
    pub_year = metadata.get("pub_year")
    if not title:
        raise ValueError("title is required")
    if not 2 <= len(language) <= 3 or not language.isalpha():
        raise ValueError("language must be a 2 or 3 letter code")
    if not authors:
        raise ValueError("book has no authors")
    if not topics:
        raise ValueError("book has no topics")
    if pub_year is not None:
        try:
            pub_year = int(pub_year)
        except (TypeError, ValueError):
            raise ValueError("pub_year must be a number")
        if not 1 <= pub_year <= 2100:
            raise ValueError("pub_year must be between 1 and 2100")

    description_value = metadata.get("description", "")
    publisher_value = metadata.get("publisher", "")
    license_name_value = metadata.get("license_name", "")
    license_url_value = metadata.get("license_url", "")
    if not all(
        isinstance(value, str)
        for value in (description_value, publisher_value, license_name_value, license_url_value)
    ):
        raise ValueError("description, publisher and license fields must be text")
    description = description_value.strip() or None
    publisher = publisher_value.strip() or None
    license_name = license_name_value.strip() or "Open Access"
    license_url = license_url_value.strip() or "https://creativecommons.org/"

    with conn.transaction(), conn.cursor() as cur:
        cur.execute("""
            select b.title, b.description, b.language, b.pub_year, b.publisher,
                   l.name as license_name, l.license_url,
                   (select array_agg(a.name order by a.name)
                      from book_author ba join author a on a.id = ba.author_id
                     where ba.book_id = b.id) as authors,
                   (select array_agg(t.name order by t.name)
                      from book_topic bt join topic t on t.id = bt.topic_id
                     where bt.book_id = b.id) as topics
            from books b
            join license l on l.id = b.license_id
            where b.id = %s and b.moderation_status = 'pending'
            for update
        """, (book_id,))
        existing = cur.fetchone()
        if existing is None:
            return False

        changed = (
            existing["title"] != title
            or existing["description"] != description
            or existing["language"] != language
            or existing["pub_year"] != pub_year
            or existing["publisher"] != publisher
            or existing["license_name"] != license_name
            or existing["license_url"] != license_url
            or sorted(name.lower() for name in existing["authors"]) != sorted(name.lower() for name in authors)
            or sorted(name.lower() for name in existing["topics"]) != sorted(name.lower() for name in topics)
        )
        if status == "approved" and changed and not review_note:
            raise ValueError("a review note is required when approving with changes")

        cur.execute(
            """
            insert into license (name, license_url) values (%s, %s)
            on conflict (lower(name)) do update set license_url = excluded.license_url
            returning id
            """,
            (license_name, license_url),
        )
        license_id = cur.fetchone()["id"]

        cur.execute("delete from book_author where book_id = %s", (book_id,))
        for name in authors:
            author_id = _reuse_or_create(cur, "author", name)
            cur.execute(
                "insert into book_author (book_id, author_id) values (%s, %s) on conflict do nothing",
                (book_id, author_id),
            )

        cur.execute("delete from book_topic where book_id = %s", (book_id,))
        for name in topics:
            topic_id = _reuse_or_create(cur, "topic", name)
            cur.execute(
                "insert into book_topic (book_id, topic_id) values (%s, %s) on conflict do nothing",
                (book_id, topic_id),
            )

        cur.execute("""
            update books
               set title = %s, description = %s, language = %s, pub_year = %s,
                   publisher = %s, license_id = %s, moderation_status = %s,
                   review_note = %s, reviewed_at = now(),
                   index_status = case when %s = 'approved' then 'pending' else 'unindexed' end,
                   index_error = null, index_version = null, indexed_at = null
             where id = %s
        """, (
            title, description, language, pub_year, publisher, license_id,
            status, review_note or None, status, book_id,
        ))
        return True


def list_books(conn):
    with conn.cursor() as cur:
        cur.execute("""
            select b.id, b.title, b.cover_ref, array_agg(a.name order by a.name) as authors
            from books b
            join book_author ba on ba.book_id = b.id
            join author a on a.id = ba.author_id
            where b.moderation_status = 'approved'
            group by b.id
            order by b.title
        """)
        return cur.fetchall()


def get_book(conn, book_id):
    with conn.cursor() as cur:
        cur.execute("""
            select b.id, b.title, b.description, b.language, b.pub_year,
                   b.publisher, b.edition, b.source_url, b.moderation_status,
                   b.submitted_by, l.name as license_name, l.license_url,
                   (select array_agg(a.name order by a.name)
                      from book_author ba join author a on a.id = ba.author_id
                     where ba.book_id = b.id) as authors,
                   (select array_agg(t.name order by t.name)
                      from book_topic bt join topic t on t.id = bt.topic_id
                     where bt.book_id = b.id) as topics,
                   (select array_agg(f.name order by f.name)
                      from book_format bf join format f on f.id = bf.format_id
                     where bf.book_id = b.id) as formats,
                   (select f.name
                      from book_format bf join format f on f.id = bf.format_id
                     where bf.book_id = b.id and f.name = any(%s)
                     order by f.priority
                     limit 1) as read_format
            from books b
            join license l on l.id = b.license_id
            where b.id = %s
        """, (list(READABLE_FORMATS), book_id))
        return cur.fetchone()


def _metadata_matches(cur, query):
    cur.execute(
        """
        select b.id, b.title, b.cover_ref,
               (select array_agg(a.name order by a.name)
                  from book_author ba join author a on a.id = ba.author_id
                 where ba.book_id = b.id) as authors,
               least(
                   1.0,
                   case when lower(b.title) = lower(%(query)s) then 1.0
                        when b.title ilike %(like)s then 0.8 else 0 end
                   + case when exists (
                              select 1
                              from book_author ba join author a on a.id = ba.author_id
                              where ba.book_id = b.id and lower(a.name) = lower(%(query)s)
                          ) then 0.8
                          when exists (
                              select 1
                              from book_author ba join author a on a.id = ba.author_id
                              where ba.book_id = b.id and a.name ilike %(like)s
                          ) then 0.6 else 0 end
                   + case when exists (
                              select 1
                              from book_topic bt join topic t on t.id = bt.topic_id
                              where bt.book_id = b.id and lower(t.name) = lower(%(query)s)
                          ) then 0.7
                          when exists (
                              select 1
                              from book_topic bt join topic t on t.id = bt.topic_id
                              where bt.book_id = b.id and t.name ilike %(like)s
                          ) then 0.5 else 0 end
                   + case when b.description ilike %(like)s then 0.3 else 0 end
               )::double precision as metadata_score
        from books b
        where b.moderation_status = 'approved'
          and (
               b.title ilike %(like)s
            or b.description ilike %(like)s
            or exists (
                select 1
                from book_author ba join author a on a.id = ba.author_id
                where ba.book_id = b.id and a.name ilike %(like)s
            )
            or exists (
                select 1
                from book_topic bt join topic t on t.id = bt.topic_id
                where bt.book_id = b.id and t.name ilike %(like)s
            )
          )
        order by metadata_score desc, b.title
        limit %(limit)s
        """,
        {
            "query": query,
            "like": f"%{query}%",
            "limit": SEARCH_CANDIDATE_LIMIT,
        },
    )
    return cur.fetchall()


def _lexical_matches(cur, query):
    cur.execute(
        """
        select b.id, b.title, b.cover_ref,
               (select array_agg(a.name order by a.name)
                  from book_author ba join author a on a.id = ba.author_id
                 where ba.book_id = b.id) as authors,
               bc.section_order, bc.chunk_order, bc.section_title, bc.locator, bc.content,
               f.name as format,
               ts_rank_cd(bc.search_vector, q.value, 32)::double precision as lexical_score
        from book_chunk bc
        join books b on b.id = bc.book_id
        join format f on f.id = bc.format_id
        cross join websearch_to_tsquery('english', %(query)s) as q(value)
        where b.moderation_status = 'approved'
          and b.index_status = 'indexed'
          and coalesce(lower(btrim(bc.section_title)), '') <> all(%(excluded_titles)s)
          and bc.search_vector @@ q.value
        order by lexical_score desc, b.id, bc.section_order, bc.chunk_order
        limit %(limit)s
        """,
        {
            "query": query,
            "excluded_titles": list(EXCLUDED_SEARCH_SECTION_TITLES),
            "limit": SEARCH_CANDIDATE_LIMIT,
        },
    )
    return cur.fetchall()


def _semantic_matches(cur, query_embedding, model_version):
    cur.execute(
        """
        select b.id, b.title, b.cover_ref,
               (select array_agg(a.name order by a.name)
                  from book_author ba join author a on a.id = ba.author_id
                 where ba.book_id = b.id) as authors,
               bc.section_order, bc.chunk_order, bc.section_title, bc.locator, bc.content,
               f.name as format,
               greatest(0, 1 - (bc.embedding <=> %(embedding)s::vector))::double precision
                   as semantic_score
        from book_chunk bc
        join books b on b.id = bc.book_id
        join format f on f.id = bc.format_id
        where b.moderation_status = 'approved'
          and b.index_status = 'indexed'
          and bc.model_version = %(model_version)s
          and coalesce(lower(btrim(bc.section_title)), '') <> all(%(excluded_titles)s)
        order by bc.embedding <=> %(embedding)s::vector,
                 b.id, bc.section_order, bc.chunk_order
        limit %(limit)s
        """,
        {
            "embedding": json.dumps(query_embedding, separators=(",", ":")),
            "model_version": model_version,
            "excluded_titles": list(EXCLUDED_SEARCH_SECTION_TITLES),
            "limit": SEARCH_CANDIDATE_LIMIT,
        },
    )
    return [
        row
        for row in cur.fetchall()
        if float(row["semantic_score"]) >= MIN_SEMANTIC_SCORE
    ]


def _book_result(results, row):
    if row["id"] not in results:
        results[row["id"]] = {
            "id": row["id"],
            "title": row["title"],
            "cover_ref": row["cover_ref"],
            "authors": row["authors"],
            "metadata_score": 0.0,
            "sections": {},
        }
    return results[row["id"]]


def _add_section_match(result, row, score_name):
    key = (row["format"], row["section_order"], row["chunk_order"])
    section = result["sections"].setdefault(
        key,
        {
            "section_order": row["section_order"],
            "chunk_order": row["chunk_order"],
            "section_title": row["section_title"],
            "locator": row["locator"],
            "format": row["format"],
            "content": row["content"],
            "lexical_score": 0.0,
            "semantic_score": 0.0,
        },
    )
    score = max(0.0, min(1.0, float(row[score_name])))
    if score > section[score_name]:
        section[score_name] = score
        section["section_title"] = row["section_title"]
        section["locator"] = row["locator"]
        section["format"] = row["format"]
        section["content"] = row["content"]


def _excerpt(text):
    text = " ".join(text.split())
    if len(text) <= SEARCH_EXCERPT_CHARACTERS:
        return text
    shortened = text[: SEARCH_EXCERPT_CHARACTERS - 3].rsplit(" ", 1)[0]
    return f"{shortened}..."


def _passage_windows(section):
    text = " ".join(section["content"].split())
    sentences = [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", text) if sentence.strip()]
    if not sentences:
        return []
    step = RERANK_WINDOW_SENTENCES - RERANK_WINDOW_OVERLAP
    windows = []
    for start in range(0, len(sentences), step):
        window = dict(section)
        window["content"] = " ".join(sentences[start : start + RERANK_WINDOW_SENTENCES])
        windows.append(window)
        if start + RERANK_WINDOW_SENTENCES >= len(sentences):
            break
    return windows


def _overlaps_existing(passage, selected):
    words = set(re.findall(r"\w+", passage["content"].lower()))
    for existing in selected:
        if (
            existing["id"] != passage["id"]
            or existing["format"] != passage["format"]
            or existing["section_order"] != passage["section_order"]
        ):
            continue
        existing_words = set(re.findall(r"\w+", existing["content"].lower()))
        union = words | existing_words
        if union and len(words & existing_words) / len(union) >= 0.70:
            return True
    return False


def _rerank_passages(query, results, reranker):
    candidates = []
    for result in results.values():
        for section in result["sections"].values():
            candidates.append(
                {
                    **section,
                    "id": result["id"],
                    "retrieval_score": (
                        LEXICAL_WEIGHT * section["lexical_score"]
                        + SEMANTIC_WEIGHT * section["semantic_score"]
                    ),
                }
            )
    candidates.sort(key=lambda candidate: candidate["retrieval_score"], reverse=True)
    candidates = candidates[:RERANK_CANDIDATE_LIMIT]
    if not candidates:
        return []
    chunk_scores = reranker(query, [candidate["content"] for candidate in candidates])
    for candidate, score in zip(candidates, chunk_scores, strict=True):
        candidate["rerank_score"] = score
    candidates.sort(
        key=lambda candidate: (candidate["rerank_score"], candidate["retrieval_score"]),
        reverse=True,
    )
    windows = []
    for candidate in candidates[:RERANK_CHUNK_LIMIT]:
        windows.extend(_passage_windows(candidate)[:RERANK_WINDOWS_PER_CHUNK])
    windows = windows[:RERANK_WINDOW_LIMIT]
    if not windows:
        return []
    window_scores = reranker(query, [window["content"] for window in windows])
    for window, score in zip(windows, window_scores, strict=True):
        window["rerank_score"] = score
    windows = [window for window in windows if window["rerank_score"] >= MIN_RERANK_SCORE]
    windows.sort(
        key=lambda window: (window["rerank_score"], window["retrieval_score"]),
        reverse=True,
    )
    selected = []
    section_counts = {}
    for window in windows:
        section_key = (window["id"], window["format"], window["section_order"])
        if section_counts.get(section_key, 0) >= RERANK_PASSAGES_PER_SECTION:
            continue
        if _overlaps_existing(window, selected):
            continue
        selected.append(window)
        section_counts[section_key] = section_counts.get(section_key, 0) + 1
        if len(selected) == RERANK_PASSAGE_LIMIT:
            break
    return selected


def _answer_excerpt(text, answer_start, answer_end):
    sentence_start = 0
    for boundary in re.finditer(r"[.!?](?:\s+|$)", text[:answer_start]):
        sentence_start = boundary.end()
    sentence_end_match = re.search(r"[.!?](?:\s+|$)", text[answer_end:])
    sentence_end = (
        answer_end + sentence_end_match.start() + 1
        if sentence_end_match
        else len(text)
    )
    while sentence_start < answer_start and text[sentence_start].isspace():
        sentence_start += 1
    passage = text[sentence_start:sentence_end]
    if len(passage) >= SEARCH_EXCERPT_CHARACTERS:
        return (
            passage,
            answer_start - sentence_start,
            answer_end - sentence_start,
            passage,
            0,
            len(passage),
        )
    surrounding = max(0, SEARCH_EXCERPT_CHARACTERS - len(passage) - 6)
    excerpt_start = max(0, sentence_start - surrounding // 2)
    excerpt_end = min(len(text), sentence_end + surrounding - (sentence_start - excerpt_start))
    excerpt_start = max(0, excerpt_end - len(passage) - surrounding)
    prefix = "..." if excerpt_start else ""
    suffix = "..." if excerpt_end < len(text) else ""
    excerpt = f"{prefix}{text[excerpt_start:excerpt_end]}{suffix}"
    offset = len(prefix) - excerpt_start
    return (
        excerpt,
        answer_start + offset,
        answer_end + offset,
        passage,
        sentence_start + offset,
        sentence_end + offset,
    )


def _extract_passage_answers(query, passages, answerer):
    answers = answerer(query, [passage["content"] for passage in passages])
    for passage, answer in zip(passages, answers, strict=True):
        if answer["answer"] and answer["score"] >= MIN_ANSWER_SCORE:
            passage["answer"] = answer["answer"]
            passage["answer_score"] = answer["score"]
            passage["answer_start"] = answer["start"]
            passage["answer_end"] = answer["end"]
    passages.sort(
        key=lambda passage: (
            "answer" in passage,
            passage.get("answer_score", 0.0),
            passage.get("rerank_score", 0.0),
            passage.get("retrieval_score", 0.0),
        ),
        reverse=True,
    )
    return passages


def _passage_match(passage):
    match = {
        "section_title": passage["section_title"],
        "locator": passage["locator"],
        "format": passage["format"],
    }
    if "answer" not in passage:
        excerpt = _excerpt(passage["content"])
        supporting_passage = excerpt[:-3] if excerpt.endswith("...") else excerpt
        match["excerpt"] = excerpt
        match["passage"] = supporting_passage
        match["passage_start"] = 0
        match["passage_end"] = len(supporting_passage)
        return match
    (
        excerpt,
        answer_start,
        answer_end,
        supporting_passage,
        passage_start,
        passage_end,
    ) = _answer_excerpt(
        passage["content"],
        passage["answer_start"],
        passage["answer_end"],
    )
    match.update(
        {
            "excerpt": excerpt,
            "answer": passage["answer"],
            "answer_score": passage["answer_score"],
            "answer_start": answer_start,
            "answer_end": answer_end,
            "passage": supporting_passage,
            "passage_start": passage_start,
            "passage_end": passage_end,
        }
    )
    return match


def search_books(
    conn,
    q,
    query_embedding=None,
    model_version=None,
    reranker=None,
    answerer=None,
    content_search=True,
):
    query = q.strip()
    if not query:
        return []
    with conn.cursor() as cur:
        metadata_rows = _metadata_matches(cur, query)
        lexical_rows = _lexical_matches(cur, query) if content_search else []
        semantic_rows = (
            _semantic_matches(cur, query_embedding, model_version)
            if content_search and query_embedding is not None
            else []
        )

    results = {}
    for row in metadata_rows:
        result = _book_result(results, row)
        result["metadata_score"] = max(result["metadata_score"], float(row["metadata_score"]))
    for row in lexical_rows:
        _add_section_match(_book_result(results, row), row, "lexical_score")
    for row in semantic_rows:
        _add_section_match(_book_result(results, row), row, "semantic_score")

    reranked_passages = _rerank_passages(query, results, reranker) if content_search and reranker else None
    if reranked_passages and answerer:
        reranked_passages = _extract_passage_answers(query, reranked_passages, answerer)
    passages_by_book = {}
    if reranked_passages is not None:
        for passage in reranked_passages:
            passages_by_book.setdefault(passage["id"], []).append(passage)

    ranked = []
    for result in results.values():
        sections = list(result.pop("sections").values())
        for section in sections:
            section["score"] = (
                LEXICAL_WEIGHT * section["lexical_score"]
                + SEMANTIC_WEIGHT * section["semantic_score"]
            )
        sections.sort(key=lambda section: section["score"], reverse=True)
        best_lexical = max((section["lexical_score"] for section in sections), default=0.0)
        best_semantic = max((section["semantic_score"] for section in sections), default=0.0)
        metadata_score = result.pop("metadata_score")
        passages = passages_by_book.get(result["id"], []) if reranked_passages is not None else sections
        best_rerank = max((passage.get("rerank_score", 0.0) for passage in passages), default=0.0)
        result["score"] = (
            METADATA_WEIGHT * metadata_score + best_rerank
            if reranked_passages is not None
            else (
                METADATA_WEIGHT * metadata_score
                + LEXICAL_WEIGHT * best_lexical
                + SEMANTIC_WEIGHT * best_semantic
            )
        )
        result["matches"] = [
            _passage_match(passage)
            for passage in passages[:SEARCH_SECTION_LIMIT]
        ]
        ranked.append(result)

    ranked.sort(key=lambda result: (-result["score"], result["title"].lower()))
    for result in ranked:
        result.pop("score")
    return ranked[:SEARCH_BOOK_LIMIT]


def get_book_file(conn, book_id, fmt=None):
    with conn.cursor() as cur:
        if fmt:
            cur.execute("""
                select bf.location
                from book_format bf
                join format f on f.id = bf.format_id
                where bf.book_id = %s and f.name = %s
            """, (book_id, fmt))
        else:
            cur.execute("""
                select bf.location
                from book_format bf
                join format f on f.id = bf.format_id
                where bf.book_id = %s
                order by f.priority
                limit 1
            """, (book_id,))
        row = cur.fetchone()
        return row and row["location"]


def get_book_cover(conn, book_id):
    with conn.cursor() as cur:
        cur.execute("select cover_ref from books where id = %s", (book_id,))
        row = cur.fetchone()
        return row and row["cover_ref"]


def _reuse_or_create(cur, table, name):
    cur.execute(f"insert into {table} (name) values (%s) on conflict (lower(name)) do nothing", (name,))
    cur.execute(f"select id from {table} where lower(name) = lower(%s)", (name,))
    return cur.fetchone()["id"]


def create_book(conn, book):
    for field in ("authors", "topics", "formats"):
        if not book.get(field):
            raise ValueError(f"book has no {field}")

    license_data = book.get("license") or {"name": "Open Access", "url": "https://creativecommons.org/"}
    lic_name = license_data.get("name") or "Open Access"
    lic_url = license_data.get("url") or "https://creativecommons.org/"

    moderation_status = book.get("moderation_status", "approved")
    index_status = book.get(
        "index_status", "pending" if moderation_status == "approved" else "unindexed"
    )

    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "insert into license (name, license_url) values (%s, %s) on conflict (lower(name)) do nothing",
            (lic_name, lic_url),
        )
        cur.execute("select id from license where lower(name) = lower(%s)", (lic_name,))
        license_id = cur.fetchone()["id"]

        cur.execute("""
            insert into books (
                title, description, language, pub_year, publisher, edition,
                source_url, cover_ref, license_id, moderation_status, submitted_by,
                index_status
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            returning id
        """, (book["title"], book.get("description"), book["language"], book.get("pub_year"),
              book.get("publisher"), book.get("edition"), book.get("source_url"),
              book.get("cover_ref"), license_id, moderation_status, book.get("submitted_by"),
              index_status))
        book_id = cur.fetchone()["id"]

        for name in book["authors"]:
            author_id = _reuse_or_create(cur, "author", name)
            cur.execute("insert into book_author (book_id, author_id) values (%s, %s) on conflict (book_id, author_id) do nothing", (book_id, author_id))

        for name in book["topics"]:
            topic_id = _reuse_or_create(cur, "topic", name)
            cur.execute("insert into book_topic (book_id, topic_id) values (%s, %s) on conflict (book_id, topic_id) do nothing", (book_id, topic_id))

        for name, location in book["formats"].items():
            cur.execute("select id from format where name = %s", (name,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"unknown format: {name}")
            cur.execute("insert into book_format (book_id, format_id, location) values (%s, %s, %s)",
                        (book_id, row["id"], location))

    return book_id


def add_book_format(conn, book_id, format_name, location):
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("select id from format where name = %s", (format_name,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"unknown format: {format_name}")
        cur.execute(
            """
            insert into book_format (book_id, format_id, location)
            values (%s, %s, %s)
            on conflict (book_id, format_id)
            do update set location = excluded.location
            """,
            (book_id, row["id"], location),
        )


def delete_book(conn, book_id):
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("select cover_ref from books where id = %s for update", (book_id,))
        book = cur.fetchone()
        if book is None:
            return None
        cur.execute("select location from book_format where book_id = %s", (book_id,))
        keys = [row["location"] for row in cur.fetchall()]
        if book["cover_ref"]:
            keys.append(book["cover_ref"])
        cur.execute("delete from books where id = %s", (book_id,))
        return keys
