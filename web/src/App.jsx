import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation, useMatch, useNavigate } from "react-router-dom";
import AddBook from "./AddBook";
import Auth from "./Auth";
import Book from "./Book";
import BookList from "./BookList";
import ContinueReading from "./ContinueReading";
import Library from "./Library";
import Read from "./Read";
import ReviewSubmission from "./ReviewSubmission";
import Search from "./Search";
import SearchResults from "./SearchResults";
import Submissions from "./Submissions";

export default function App() {
  const [user, setUser] = useState(null);
  const [checking, setChecking] = useState(true);
  const [query, setQuery] = useState("");
  const [books, setBooks] = useState(null);
  const [failed, setFailed] = useState(false);
  const [bookSession, setBookSession] = useState(0);
  const [history, setHistory] = useState(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyFailed, setHistoryFailed] = useState(false);
  const [addingBook, setAddingBook] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();
  const bookMatch = useMatch("/books/:bookId");
  const readerMatch = useMatch("/books/:bookId/read/:format");
  const reviewMatch = useMatch("/submissions/:bookId/review");
  const mode =
    location.pathname === "/"
      ? "browse"
      : location.pathname === "/library"
        ? "library"
        : location.pathname === "/search"
          ? "search"
          : location.pathname === "/submissions"
            ? "submissions"
            : null;

  useEffect(() => {
    fetch("/auth/me")
      .then((response) => response.json())
      .then((data) => setUser(data.user))
      .catch(() => setUser(null))
      .finally(() => setChecking(false));
  }, []);

  function loadHistory() {
    setHistoryLoading(true);
    setHistoryFailed(false);
    fetch("/books/history")
      .then((response) => {
        if (!response.ok) throw new Error(response.status);
        return response.json();
      })
      .then(setHistory)
      .catch(() => setHistoryFailed(true))
      .finally(() => setHistoryLoading(false));
  }

  function load(path) {
    setBooks(null);
    setFailed(false);
    fetch(path)
      .then((response) => {
        if (!response.ok) throw new Error(response.status);
        return response.json();
      })
      .then(setBooks)
      .catch(() => setFailed(true));
  }

  useEffect(() => {
    if (user) {
      loadHistory();
    } else {
      setHistory(null);
    }
  }, [user, bookSession]);

  useEffect(() => {
    if (mode === "browse" && user) load("/books");
  }, [mode, user]);

  function search(text) {
    setQuery(text);
    load("/search?q=" + encodeURIComponent(text));
  }

  function show(next) {
    setQuery("");
    if (next === "search") setBooks(null);
    if (next === "library") loadHistory();
    navigate(next === "browse" ? "/" : "/" + next);
  }

  function sourceRoute() {
    if (mode) return location.pathname;
    return location.state?.from || "/";
  }

  function openBook(id) {
    navigate("/books/" + encodeURIComponent(id), { state: { from: sourceRoute() } });
  }

  function openSearchMatch(bookId, match) {
    navigate(
      "/books/" + encodeURIComponent(bookId) + "/read/" + encodeURIComponent(match.format),
      {
        state: {
          from: "/search",
          searchResult: true,
          locator: match.locator,
          highlight: match.passage || null,
        },
      },
    );
  }

  function resumeBook(book) {
    const isEpub =
      book.progress_format === "epub" ||
      (book.formats && book.formats.includes("epub") && !book.progress_format);
    const format = book.progress_format || (isEpub ? "epub" : "pdf");
    navigate("/books/" + encodeURIComponent(book.id) + "/read/" + format, {
      state: { from: sourceRoute() },
    });
  }

  function signOut() {
    fetch("/auth/logout", { method: "POST" });
    setUser(null);
    setHistory(null);
    navigate("/", { replace: true });
  }

  if (checking) return <p className="status status--page">Loading your library…</p>;

  if (!user || !user.email) {
    return (
      <div className="page">
        <div className="auth-heading">
          <h1 className="masthead">Xaperio</h1>
          <p>Your open technical knowledge base.</p>
        </div>
        <Auth onSignedIn={setUser} />
      </div>
    );
  }

  if (readerMatch) {
    const { bookId, format } = readerMatch.params;
    const supported = ["epub", "pdf", "html"].includes(format);

    return (
      <Routes>
        <Route
          path="/books/:bookId/read/:format"
          element={
            supported ? (
              <Read
                bookId={bookId}
                epub={format === "epub"}
                readFormat={format}
                initialLocator={location.state?.locator}
                initialHighlight={location.state?.highlight}
                onBack={() => {
                  setBookSession((number) => number + 1);
                  if (location.state?.review) {
                    navigate("/submissions/" + encodeURIComponent(bookId) + "/review", {
                      replace: true,
                    });
                  } else if (location.state?.searchResult) {
                    navigate(location.state.from || "/search", { replace: true });
                  } else {
                    navigate("/books/" + encodeURIComponent(bookId), {
                      replace: true,
                      state: { from: location.state?.from || "/" },
                    });
                  }
                }}
              />
            ) : (
              <Navigate to={"/books/" + encodeURIComponent(bookId)} replace />
            )
          }
        />
      </Routes>
    );
  }

  const waiting = mode === "search" && !query;
  const results = (
    <section
      className="results"
      aria-live="polite"
      aria-busy={books === null && !waiting && !failed}
    >
      {!waiting && failed && (
        <p className="status">
          {mode === "search"
            ? "Search could not be completed. Try again."
            : "Could not load the library."}
        </p>
      )}
      {!waiting && !failed && books === null && (
        <p className="status">{mode === "search" ? "Searching..." : "Loading."}</p>
      )}
      {!waiting && !failed && books !== null && books.length === 0 && (
        <p className="status">
          {mode === "search" ? `Nothing matches ${query}.` : "There are no books yet."}
        </p>
      )}
      {!waiting && !failed && books !== null && books.length > 0 && (
        <>
          {mode === "browse" ? (
            <div className="library__head">
              <h2 className="list-head">All books</h2>
              <span className="library__count">{books.length} {books.length === 1 ? "book" : "books"}</span>
            </div>
          ) : (
            <p className="status search-summary">
              <strong>{books.length}</strong> {books.length === 1 ? "book" : "books"} for “{query}”
            </p>
          )}
          {mode === "search" ? (
            <SearchResults
              books={books}
              query={query}
              onSelectBook={openBook}
              onSelectMatch={openSearchMatch}
            />
          ) : (
            <BookList books={books} onSelect={openBook} />
          )}
        </>
      )}
    </section>
  );

  return (
    <div className="page">
      <div className="masthead-row">
        <div>
          <h1 className="masthead">Xaperio</h1>
          <p className="masthead-note">Your technical library.</p>
        </div>
        <span className="who">
          <button
            type="button"
            className="submit-book-btn"
            onClick={() => setAddingBook(true)}
            aria-label="Submit a book"
            data-tooltip="Submit a book"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M12 4v16M4 12h16" />
            </svg>
          </button>
          <button type="button" className="text-btn" onClick={signOut}>
            Sign out
          </button>
        </span>
      </div>

      {mode && (
        <nav className="modes" aria-label="Library sections">
          <button
            className={mode === "browse" ? "mode mode--on" : "mode"}
            onClick={() => show("browse")}
            aria-pressed={mode === "browse"}
          >
            Browse
          </button>
          <button
            className={mode === "library" ? "mode mode--on" : "mode"}
            onClick={() => show("library")}
            aria-pressed={mode === "library"}
          >
            My Library
          </button>
          <button
            className={mode === "search" ? "mode mode--on" : "mode"}
            onClick={() => show("search")}
            aria-pressed={mode === "search"}
          >
            Search
          </button>
          <button
            className={mode === "submissions" ? "mode mode--on" : "mode"}
            onClick={() => show("submissions")}
            aria-pressed={mode === "submissions"}
          >
            {user.is_admin ? "Review" : "Submissions"}
          </button>
        </nav>
      )}

      <Routes>
        <Route
          path="/"
          element={
            <>
              <ContinueReading history={history} onResume={resumeBook} onSelect={openBook} />
              {results}
            </>
          }
        />
        <Route
          path="/library"
          element={
            <Library
              history={history}
              loading={historyLoading}
              failed={historyFailed}
              onResume={resumeBook}
              onBrowse={() => show("browse")}
            />
          }
        />
        <Route
          path="/search"
          element={
            <>
              <section className="search-view">
                <h2 className="list-head">Search the library</h2>
                <p className="search-view__intro">
                  Find a specific book or concept.
                </p>
                <Search onSearch={search} initialValue={query} />
              </section>
              {results}
            </>
          }
        />
        <Route
          path="/books/:bookId"
          element={
            <Book
              key={bookSession}
              bookId={bookMatch?.params.bookId}
              canDelete={user.is_admin}
              onRead={(_, format) =>
                navigate(
                  "/books/" + encodeURIComponent(bookMatch.params.bookId) + "/read/" + format,
                  { state: { from: location.state?.from || "/" } },
                )
              }
              onBack={() => navigate(location.state?.from || "/")}
              onDeleted={() => {
                loadHistory();
                navigate("/", { replace: true });
              }}
            />
          }
        />
        <Route
          path="/submissions"
          element={
            <Submissions
              isAdmin={user.is_admin}
              onReview={(id) => navigate("/submissions/" + encodeURIComponent(id) + "/review")}
            />
          }
        />
        <Route
          path="/submissions/:bookId/review"
          element={
            user.is_admin ? (
              <ReviewSubmission
                bookId={reviewMatch?.params.bookId}
                onBack={() => navigate("/submissions")}
                onRead={(format) =>
                  navigate(
                    "/books/" + encodeURIComponent(reviewMatch.params.bookId) + "/read/" + format,
                    { state: { review: true } },
                  )
                }
                onReviewed={() => navigate("/submissions", { replace: true })}
              />
            ) : (
              <Navigate to="/submissions" replace />
            )
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>

      {addingBook && (
        <AddBook
          onClose={() => setAddingBook(false)}
          onBookAdded={(newId) => {
            setAddingBook(false);
            navigate("/submissions", { state: { submitted: newId } });
          }}
        />
      )}
    </div>
  );
}
