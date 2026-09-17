import { useEffect, useRef, useState } from "react";
import ePub from "epubjs";
import PdfView from "./PdfView";
import { findTextRange, highlightDocument } from "./readerHighlight";


export default function Read({ bookId, epub, readFormat, initialLocator, initialHighlight, onBack }) {
  const epubHost = useRef(null);
  const frameRef = useRef(null);
  const rendition = useRef(null);
  const [failed, setFailed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [turning, setTurning] = useState(false);
  const [turnError, setTurnError] = useState(false);
  const [bounds, setBounds] = useState({ start: true, end: true });
  const turnRef = useRef(false);

  async function turn(direction) {
    const r = rendition.current;
    if (!r || loading || failed || turnRef.current) return;
    const location = r.currentLocation();
    if (direction === "prev" ? location?.atStart : location?.atEnd) return;
    turnRef.current = true;
    setTurning(true);
    setTurnError(false);
    try {
      await r[direction]();
    } catch {
      if (rendition.current === r) setTurnError(true);
    } finally {
      turnRef.current = false;
      if (rendition.current === r) setTurning(false);
    }
  }

  useEffect(() => {
    if (!epub || loading || failed) return;
    const r = rendition.current;
    function onKey(event) {
      if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey ||
          event.target?.isContentEditable || event.target?.closest?.("input, textarea, select, button, a")) return;
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      turn(event.key === "ArrowLeft" ? "prev" : "next");
    }
    window.addEventListener("keydown", onKey);
    r?.on("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      r?.off("keydown", onKey);
    };
  }, [epub, loading, failed]);
  const epubHref = typeof initialLocator?.href === "string" ? initialLocator.href : null;
  const epubAnchor = typeof initialLocator?.anchor === "string" ? initialLocator.anchor : null;
  const htmlAnchor =
    readFormat === "html" && typeof initialLocator?.anchor === "string"
      ? initialLocator.anchor
      : null;

  useEffect(() => {
    if (!epub) return;
    let stopped = false;
    let r;
    let saveTimer;
    let lastCfi = null;
    let book;
    let highlightCfi;
    setLoading(true);
    setFailed(false);
    setTurning(false);
    setTurnError(false);
    turnRef.current = false;
    setBounds({ start: true, end: true });

    function flush() {
      if (lastCfi == null) return;
      const cfi = lastCfi;
      lastCfi = null;
      fetch("/books/" + bookId + "/progress", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ position: cfi, format: "epub" }),
      }).catch(() => {});
    }

    async function openBlock() {
      try {
        const res = await fetch("/books/" + bookId + "/read?format=epub", {
          cache: "no-store",
        });
        if (!res.ok) throw new Error(res.status);
        const buf = await res.arrayBuffer();
        if (stopped) return;
        book = ePub(buf);
        await book.ready;
        if (stopped) return;
        let resolvedEpubTarget = null;
        let resolvedEpubSection = null;
        if (epubHref) {
          const indexedHref = epubHref.replace(/^\.\/+/, "");
          const section = book.spine.spineItems.find((item) => {
            let itemHref = item.href.replace(/^\.\/+/, "");
            try {
              itemHref = decodeURI(itemHref);
            } catch {

            }
            return indexedHref === itemHref ||
              indexedHref.endsWith("/" + itemHref) ||
              itemHref.endsWith("/" + indexedHref);
          });
          if (section) {
            resolvedEpubSection = section.href;
            resolvedEpubTarget = resolvedEpubSection +
              (epubAnchor ? "#" + encodeURIComponent(epubAnchor) : "");
          }
        }
        r = book.renderTo(epubHost.current, {
          width: "100%",
          height: "100%",
          spread: "none",
          flow: initialHighlight ? "scrolled-doc" : "paginated",
        });
        if (stopped) {
          r.destroy();
          return;
        }
        rendition.current = r;

        let start = resolvedEpubTarget;
        if (!start && !epubHref) {
          const saved = await fetch("/books/" + bookId + "/progress")
            .then((x) => x.json())
            .catch(() => ({}));
          start = saved && saved.position;
        }

        if (stopped) return;
        r.on("relocated", () => {
          if (stopped) return;
          const loc = r.currentLocation();
          setBounds({ start: !!loc?.atStart, end: !!loc?.atEnd });
          const cfi = loc && loc.start && loc.start.cfi;
          if (!cfi) return;
          lastCfi = cfi;
          clearTimeout(saveTimer);
          saveTimer = setTimeout(flush, 300);
        });
        try {
          await r.display(start || undefined);
        } catch {
          if (resolvedEpubSection && start !== resolvedEpubSection) {
            await r.display(resolvedEpubSection);
          } else if (start) {
            await r.display();
          } else {
            throw new Error("EPUB has no readable section");
          }
        }
        if (stopped) return;
        if (initialHighlight) {
          for (const contents of r.getContents()) {
            const range = findTextRange(contents.document.body, initialHighlight);
            if (!range) continue;
            highlightCfi = contents.cfiFromRange(range);
            await r.display(highlightCfi);
            if (stopped) return;
            r.annotations.highlight(
              highlightCfi,
              {},
              null,
              "xaperio-answer",
              { fill: "#f5d75b", "fill-opacity": "0.38", "mix-blend-mode": "multiply" },
            );
            break;
          }
        }
        const loc = r.currentLocation();
        setBounds({ start: !!loc?.atStart, end: !!loc?.atEnd });
        setLoading(false);
      } catch (err) {
        if (!stopped) { setFailed(true); setLoading(false); }
      }
    }

    openBlock();

    return () => {
      stopped = true;
      rendition.current = null;
      clearTimeout(saveTimer);
      flush();
      if (highlightCfi && r) r.annotations.remove(highlightCfi, "highlight");
      if (book) book.destroy();
    };
  }, [bookId, epub, epubHref, epubAnchor, initialHighlight]);

  useEffect(() => {
    if (epub || readFormat !== "html") return;
    let saveTimer;
    let interval;
    const frame = frameRef.current;
    let saved = null;
    let highlightCleanup;
    let last = null;

    function doc() {
      return frame && frame.contentDocument
        ? frame.contentDocument
        : frame && frame.contentWindow
          ? frame.contentWindow.document
          : null;
    }

    function currentPosition() {
      const documentEl = doc();
      if (!documentEl) return null;
      const win = frame.contentWindow;
      const top = win.scrollY || win.pageYOffset || 0;
      if (top <= 0) return "top:0";
      const sections = Array.from(documentEl.querySelectorAll("h1[id], h2[id], h3[id]"));
      let section = null;
      for (const el of sections) {
        if (el.getBoundingClientRect().top <= 1) section = el;
        else break;
      }
      if (!section) return "top:0";
      return section.id + ":" + (top - section.offsetTop);
    }

    function flush() {
      if (last == null) return;
      const pos = last;
      last = null;
      fetch("/books/" + bookId + "/progress", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ position: String(pos), format: "html" }),
      }).catch(() => {});
    }

    function onChange() {
      const documentEl = doc();
      if (!documentEl) return;
      if (frame.contentDocument && !frame.contentDocument.body) return;
      const position = currentPosition();
      if (position == null) return;
      last = position;
      clearTimeout(saveTimer);
      saveTimer = setTimeout(flush, 300);
    }

    function restore() {
      if (!frame || !frame.contentWindow) return;
      try {
        const win = frame.contentWindow;
        const documentEl = doc();
        if (!documentEl) return;
        if (highlightCleanup) highlightCleanup();
        highlightCleanup = highlightDocument(documentEl.body || documentEl, initialHighlight);
        if (htmlAnchor) {
          const section = documentEl.getElementById(htmlAnchor);
          if (section) win.scrollTo(0, section.offsetTop);
          return;
        }
        if (saved == null) return;
        if (typeof saved === "string" && saved.includes(":")) {
          const i = saved.indexOf(":");
          const section = documentEl.getElementById(saved.slice(0, i));
          if (section) {
            win.scrollTo(0, section.offsetTop + Number(saved.slice(i + 1)) || 0);
            return;
          }
        }
        win.scrollTo(0, Number(saved) || 0);
      } catch {

      }
    }

    if (!htmlAnchor) {
      fetch("/books/" + bookId + "/progress")
        .then((x) => x.json())
        .then((data) => {
          if (data && data.position != null) {
            saved = data.position;
            if (saved !== "") restore();
          }
        })
        .catch(() => {});
    }

    const frameWin = frame && frame.contentWindow;
    const frameDoc = frame && frame.contentDocument;

    frame && frame.addEventListener("load", restore);
    frameWin && frameWin.addEventListener("scroll", onChange);
    frameDoc && frameDoc.addEventListener("scroll", onChange, true);
    interval = setInterval(() => {
      if (last != null) flush();
    }, 2000);

    return () => {
      clearTimeout(saveTimer);
      clearInterval(interval);
      flush();
      if (highlightCleanup) highlightCleanup();
      if (frame) {
        frame.removeEventListener("load", restore);
      }
      if (frameWin) {
        frameWin.removeEventListener("scroll", onChange);
      }
      if (frameDoc) {
        frameDoc.removeEventListener("scroll", onChange, true);
      }
    };
  }, [bookId, epub, readFormat, htmlAnchor, initialHighlight]);

  if (epub) {
    return (
      <div className="reader">
        <p className="reader__bar">
          <button type="button" className="text-btn" onClick={onBack}>
            Back
          </button>
          <span className="reader__spacer" />
          <button
            type="button"
            className="text-btn"
            onClick={() => turn("prev")}
            disabled={loading || failed || turning || bounds.start}
          >
            Previous
          </button>
          <span className="reader__sep">/</span>
          <button
            type="button"
            className="text-btn"
            onClick={() => turn("next")}
            disabled={loading || failed || turning || bounds.end}
          >
            Next
          </button>
        </p>
        {loading && <p className="reader__message" role="status">Opening book…</p>}
        {failed && <p className="reader__message" role="alert">This book could not be opened.</p>}
        {turnError && <p className="reader__message" role="alert">Could not turn the page. Try again.</p>}
        <div ref={epubHost} className="reader__epub" aria-busy={loading} />
      </div>
    );
  }

  if (readFormat === "pdf") {
    return (
      <PdfView
        bookId={bookId}
        initialPage={initialLocator?.page_start}
        initialEndPage={initialLocator?.page_end}
        initialHighlight={initialHighlight}
        onBack={onBack}
      />
    );
  }

  return (
    <div className="reader">
      <p className="reader__bar">
        <button type="button" className="text-btn" onClick={onBack}>
          Back
        </button>
      </p>
      <iframe
        ref={frameRef}
        className="reader__frame"
        src={
          "/books/" +
          bookId +
          "/read?format=html" +
          (htmlAnchor ? "#" + encodeURIComponent(htmlAnchor) : "")
        }
        title="Book"
      />
    </div>
  );
}
