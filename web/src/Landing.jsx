import { Link } from "react-router-dom";

export default function Landing() {
  return (
    <div className="page landing">
      <header className="landing__header">
        <Link className="masthead landing__brand" to="/" aria-label="Xaperio home">Xaperio</Link>
        <nav className="landing__nav" aria-label="Main navigation">
          <Link className="text-btn" to="/login">Sign in</Link>
          <Link className="btn" to="/register">Create an account</Link>
        </nav>
      </header>

      <main>
        <section className="landing__hero" aria-labelledby="landing-title">
          <div className="landing__hero-copy">
            <h1 id="landing-title">Quick technical reference</h1>
            <p className="landing__lead">
              Xaperio lets you quickly search through openly licensed technical books
              to find answers to your lingering doubts or confusion about a technical concept.
            </p>
            <div className="landing__actions">
              <Link className="btn" to="/register">Ask a question</Link>
              <Link className="text-btn" to="/login">Browse the library</Link>
            </div>
          </div>
        </section>

        <section className="landing__purpose" aria-labelledby="purpose-title">
          <h2 className="list-head" id="purpose-title">For the curious mind.</h2>
          <div>
            <p>
              Ever asked an AI a question and ended up less sure of the answer?
              It might disagree with something you know, then change its answer
              when you push back. You’re still left wondering which explanation
              to trust.
            </p>
            <p>
              That’s why Xaperio exists. It helps you find what technical books
              say about the question, so you have something to read, compare,
              and think through for yourself.
            </p>
          </div>
        </section>

        <section className="landing__section landing__split" aria-labelledby="searching-title">
          <h2 className="list-head" id="searching-title">Find the passage not just the book.</h2>
          <p>
            Google is useful when you are searching the web. But even after
            you find a good book, you would still have to find the part that talks about or answers
            your question. Xaperio searches inside the books for you and returns passages from different books
            that can answer your question
          </p>
        </section>

        <section className="landing__section landing__split" aria-labelledby="sources-title">
          <h2 className="list-head" id="sources-title">Know what you are reading.</h2>
          <p>
            The library is a collection of openly licensed and freely distributable
            books on computing and related topics. Search results show text excerpts 
            together with its page from relevant books. You can always
            open the book and read beyond the excerpt.
          </p>
        </section>

        <section className="landing__closing" aria-labelledby="closing-title">
          <h2 id="closing-title">what's your next find?</h2>
          <p>Start with a question or browse the library for something to read.</p>
          <div className="landing__actions">
            <Link className="btn" to="/register">Ask a question</Link>
            <Link className="text-btn" to="/login">Browse the library</Link>
          </div>
        </section>
      </main>

      <footer className="landing__footer">
        <p>Xaperio. Your technical library.</p>
        <Link className="text-btn" to="/login">Sign in <span aria-hidden="true">›</span></Link>
      </footer>
    </div>
  );
}
