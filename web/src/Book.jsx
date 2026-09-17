import { useEffect, useState } from "react";

export default function Book({ bookId, canDelete, onRead, onBack, onDeleted }) {
  const [book, setBook] = useState(null);
  const [failed, setFailed] = useState(false);
  const [missing, setMissing] = useState(false);
  const [coverHidden, setCoverHidden] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteFailed, setDeleteFailed] = useState(null);

  useEffect(() => {
    setBook(null);
    setFailed(false);
    setMissing(false);
    setCoverHidden(false);
    setDeleting(false);
    setDeleteFailed(null);
    fetch("/books/" + bookId)
      .then((response) => {
        if (response.status === 404) {
          setMissing(true);
          return null;
        }
        if (!response.ok) throw new Error(response.status);
        return response.json();
      })
      .then((data) => {
        if (data) setBook(data);
      })
      .catch(() => setFailed(true));
  }, [bookId]);

  async function deleteBook() {
    if (!window.confirm(`Delete ${book.title}? This cannot be undone.`)) return;
    setDeleting(true);
    setDeleteFailed(null);
    try {
      const response = await fetch("/books/" + bookId, { method: "DELETE" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || "The book could not be deleted.");
      onDeleted();
    } catch (error) {
      setDeleteFailed(error.message);
    } finally {
      setDeleting(false);
    }
  }

  if (failed || missing || book === null) {
    return (
      <article className="detail">
        <p className="detail__nav">
          <button type="button" className="text-btn" onClick={onBack}>
            ← Back to library
          </button>
        </p>
        <p className="status">
          {failed
            ? "Could not load this book."
            : missing
              ? "This book is not in the library."
              : "Loading."}
        </p>
      </article>
    );
  }

  const defaultFormat =
    (book.progress && book.progress.format) || book.read_format || (book.formats && book.formats[0]);

  return (
    <article className="detail">
      <p className="detail__nav">
        <button type="button" className="text-btn" onClick={onBack}>
          ← Back to library
        </button>
      </p>

      <div className="detail__layout">
        <div className="detail__visual">
          {!coverHidden ? (
          <img
            className="detail__cover"
            src={"/books/" + bookId + "/cover"}
            alt={`Cover of ${book.title}`}
            onError={() => setCoverHidden(true)}
          />
          ) : (
            <span className="detail__cover detail__cover--placeholder" aria-hidden="true" />
          )}
          {book.formats && book.formats.length > 0 && (
            <p className="detail__available">Available as {book.formats.map((format) => format.toUpperCase()).join(" · ")}</p>
          )}
        </div>

        <div className="detail__content">
          <header className="detail__heading">
            <p className="detail__eyebrow">Book details</p>
            <h2 className="detail__title">{book.title}</h2>
            <p className="detail__authors">by {book.authors.join(", ")}</p>
          </header>
          <div className="detail__actions">
            {defaultFormat ? (
              <>
                <button
                  type="button"
                  className="btn"
                  onClick={() => onRead(defaultFormat === "epub", defaultFormat)}
                >
                  {book.progress ? "Continue reading" : "Read"}
                </button>
                {book.formats && book.formats.length > 1 && (
                  <div className="detail__format-options">
                    <span className="detail__format-label">Or read in:</span>
                    {book.formats
                      .filter((f) => f !== defaultFormat)
                      .map((f) => (
                        <button
                          key={f}
                          type="button"
                          className="text-btn text-btn--format"
                          onClick={() => onRead(f === "epub", f)}
                        >
                          {f.toUpperCase()}
                        </button>
                      ))}
                  </div>
                )}
              </>
            ) : (
              <span className="detail__note">
                This book has no format available to read.
              </span>
            )}
          </div>
        </div>
      </div>

      <div className="detail__body">
        {book.description && (
          <section className="detail__description" aria-labelledby="about-book">
            <h3 id="about-book">About this book</h3>
            <p>{book.description}</p>
          </section>
        )}

        <dl className="detail__metadata">
          {book.publisher && <div><dt>Publisher</dt><dd>{book.publisher}</dd></div>}
          {book.pub_year && <div><dt>Published</dt><dd>{book.pub_year}</dd></div>}
          {book.edition && <div><dt>Edition</dt><dd>{book.edition}</dd></div>}
          {book.language && <div><dt>Language</dt><dd>{book.language}</dd></div>}
          {book.topics && book.topics.length > 0 && <div><dt>Topics</dt><dd>{book.topics.join(", ")}</dd></div>}
          <div><dt>License</dt><dd><a href={book.license_url}>{book.license_name}</a></dd></div>
          {book.source_url && <div><dt>Source</dt><dd><a href={book.source_url}>Original publication</a></dd></div>}
        </dl>
      </div>

      {canDelete && (
        <div className="detail__admin">
          <button
            type="button"
            className="text-btn text-btn--delete"
            disabled={deleting}
            onClick={deleteBook}
          >
            {deleting ? "Deleting…" : "Delete book"}
          </button>
          {deleteFailed && <p className="auth__error">{deleteFailed}</p>}
        </div>
      )}
    </article>
  );
}
