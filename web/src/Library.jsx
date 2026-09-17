import { formatTimeAgo } from "./ContinueReading";

export default function Library({ history, loading, failed, onResume, onBrowse }) {
  if (loading) {
    return <p className="status">Loading your library...</p>;
  }

  if (failed) {
    return <p className="status">Oops! We ran into a problem 😔</p>;
  }

  if (!history || history.length === 0) {
    return (
      <div className="library-empty">
        <h2 className="list-head">Your Library</h2>
        <p className="status">Open some books please!!!! 😂</p>
        <p>
          <button type="button" className="btn" onClick={onBrowse}>
            Explore Books
          </button>
        </p>
      </div>
    );
  }

  return (
    <section className="library" aria-label="Reading history">
      <div className="library__head">
        <h2 className="list-head">Reading History</h2>
        <span className="library__count">
          {history.length} {history.length === 1 ? "book" : "books"} started
        </span>
      </div>

      <ul className="books">
        {history.map((book) => {
          const timeAgo = formatTimeAgo(book.updated_at);

          return (
            <li key={book.id} className="book">
              <button
                type="button"
                className="book__select"
                onClick={() => onResume(book)}
              >
                {book.cover_ref ? (
                  <img
                    className="book__cover"
                    src={"/books/" + book.id + "/cover"}
                    alt=""
                    loading="lazy"
                  />
                ) : (
                  <span className="book__cover book__cover--placeholder" aria-hidden="true" />
                )}

                <span className="book__text">
                  <span className="book__title">{book.title}</span>
                  <span className="book__authors">
                    {book.authors && book.authors.join(", ")}
                    {timeAgo && ` · Read ${timeAgo}`}
                  </span>
                </span>
                <span className="book__arrow" aria-hidden="true">›</span>
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
