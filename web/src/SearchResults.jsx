const SEARCH_STOP_WORDS = new Set([
  "a",
  "an",
  "and",
  "are",
  "as",
  "at",
  "be",
  "been",
  "being",
  "by",
  "can",
  "could",
  "did",
  "do",
  "does",
  "for",
  "from",
  "had",
  "has",
  "have",
  "how",
  "if",
  "in",
  "into",
  "is",
  "it",
  "its",
  "may",
  "might",
  "must",
  "no",
  "not",
  "of",
  "on",
  "or",
  "shall",
  "should",
  "that",
  "the",
  "their",
  "them",
  "then",
  "there",
  "these",
  "they",
  "this",
  "those",
  "to",
  "was",
  "were",
  "what",
  "when",
  "where",
  "which",
  "who",
  "why",
  "will",
  "with",
  "would",
  "you",
  "your",
]);

function queryTerms(query) {
  return Array.from(new Set(query.match(/[\p{L}\p{N}]+/gu) || []))
    .filter((term) => term.length > 1 && !SEARCH_STOP_WORDS.has(term.toLocaleLowerCase()))
    .sort((first, second) => second.length - first.length);
}

function highlightExcerpt(excerpt, query, answer, answerStart, answerEnd) {
  const hasAnswer =
    typeof excerpt === "string" &&
    typeof answer === "string" &&
    Number.isInteger(answerStart) &&
    Number.isInteger(answerEnd) &&
    answerStart >= 0 &&
    answerEnd > answerStart &&
    answerEnd <= excerpt.length &&
    excerpt.slice(answerStart, answerEnd) === answer;

  if (hasAnswer) {
    return (
      <>
        {excerpt.slice(0, answerStart)}
        <mark className="search-match__answer">{answer}</mark>
        {excerpt.slice(answerEnd)}
      </>
    );
  }

  const terms = queryTerms(query);
  if (terms.length === 0) return excerpt;
  const escaped = terms.map((term) => {
    const lower = term.toLocaleLowerCase();
    if (/[^aeiou]ies$/u.test(lower)) {
      return `${lower.slice(0, -3).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?:y|ies)`;
    }
    if (/(?:ses|xes|zes|ches|shes)$/u.test(lower)) {
      return `${lower.slice(0, -2).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?:es)?`;
    }
    if (/s$/u.test(lower) && !/(?:ss|us|is)$/u.test(lower)) {
      return `${lower.slice(0, -1).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}s?`;
    }
    if (/[^aeiou]y$/u.test(lower)) {
      return `${lower.slice(0, -1).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?:y|ies)`;
    }
    const safe = lower.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    if (/(?:s|x|z|ch|sh)$/u.test(lower)) return `${safe}(?:es)?`;
    return `${safe}s?`;
  });
  const pattern = new RegExp(
    `(?<![\\p{L}\\p{N}])(${escaped.join("|")})(?![\\p{L}\\p{N}])`,
    "giu",
  );
  return excerpt.split(pattern).map((part, index) =>
    index % 2 === 1 ? <mark key={index}>{part}</mark> : part,
  );
}

function pageLabel(match) {
  if (match.format !== "pdf") return null;
  const start = Number(match.locator?.page_start);
  const end = Number(match.locator?.page_end);
  if (!Number.isInteger(start) || start < 1) return null;
  if (!Number.isInteger(end) || end <= start) return `Page ${start}`;
  return `Pages ${start}-${end}`;
}

function canOpenMatch(match) {
  if (match.format === "pdf") {
    const page = Number(match.locator?.page_start);
    return Number.isInteger(page) && page >= 1;
  }
  if (match.format === "epub") {
    return typeof match.locator?.href === "string" && match.locator.href.trim().length > 0;
  }
  if (match.format === "html") {
    return typeof match.locator?.anchor === "string" && match.locator.anchor.trim().length > 0;
  }
  return false;
}

export default function SearchResults({ books, query, onSelectBook, onSelectMatch }) {
  return (
    <ol className="search-results">
      {books.map((book) => {
        const matches = book.matches || [];
        return (
          <li className="search-result" key={book.id}>
            <article>
              <header className="search-result__head">
                {book.cover_ref ? (
                  <img
                    className="search-result__cover"
                    src={"/books/" + book.id + "/cover"}
                    alt=""
                    loading="lazy"
                  />
                ) : (
                  <span
                    className="search-result__cover search-result__cover--placeholder"
                    aria-hidden="true"
                  />
                )}
                <div className="search-result__book">
                  <h3 className="search-result__title">
                    <button type="button" onClick={() => onSelectBook(book.id)}>
                      {book.title}
                    </button>
                  </h3>
                  <p className="search-result__authors">{book.authors.join(", ")}</p>
                </div>
              </header>

              {matches.length > 0 && (
                <ol className="search-matches" aria-label={`Relevant passages from ${book.title}`}>
                  {matches.map((match, index) => {
                    const pages = pageLabel(match);
                    const supported = canOpenMatch(match);
                    return (
                      <li className="search-match" key={`${match.format}-${index}`}>
                        <div className="search-match__head">
                          <h4>{match.section_title || "Relevant section"}</h4>
                          <span className="search-match__location">
                            {match.format.toUpperCase()}
                            {pages && `, ${pages}`}
                          </span>
                        </div>
                        <p className="search-match__excerpt">
                          {highlightExcerpt(
                            match.excerpt,
                            query,
                            match.answer,
                            match.answer_start,
                            match.answer_end,
                          )}
                        </p>
                        {supported && (
                          <button
                            type="button"
                            className="text-btn search-match__open"
                            onClick={() => onSelectMatch(book.id, match)}
                          >
                            Open passage
                          </button>
                        )}
                      </li>
                    );
                  })}
                </ol>
              )}
            </article>
          </li>
        );
      })}
    </ol>
  );
}
