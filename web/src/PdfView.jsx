import { useRef, useState, useEffect, useLayoutEffect, useCallback } from "react";
import * as pdfjsLib from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { containsText, highlightDocument } from "./readerHighlight";

pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl;

export default function PdfView({ bookId, initialPage, initialEndPage, initialHighlight, onBack }) {
  const scrollRef = useRef(null);
  const currentRef = useRef(1);
  const readyRef = useRef(false);
  const [pages, setPages] = useState([]);
  const [page, setPage] = useState(1);
  const [pageInput, setPageInput] = useState("1");
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [errors, setErrors] = useState([]);

  const renderPage = useCallback(async (pd) => {
    if (!pd.active || pd.rendering || pd.rendered === pd.scale) return;
    pd.rendering = true;
    const scale = pd.scale;
    try {
      const viewport = pd.pdfPage.getViewport({ scale });
      const dpr = window.devicePixelRatio || 1;
      pd.canvas.width = Math.floor(viewport.width * dpr);
      pd.canvas.height = Math.floor(viewport.height * dpr);
      pd.task = pd.pdfPage.render({
        canvasContext: pd.canvas.getContext("2d"),
        viewport,
        transform: [dpr, 0, 0, dpr, 0, 0],
      });
      await pd.task.promise;
      pd.rendered = scale;
      if (pd.highlightContent && pd.textLayerEl && pd.textRendered !== scale) {
        if (pd.highlightCleanup) pd.highlightCleanup();
        if (pd.textLayerTask) pd.textLayerTask.cancel();
        pd.textLayerEl.replaceChildren();
        pd.textLayerTask = new pdfjsLib.TextLayer({
          textContentSource: pd.highlightContent,
          container: pd.textLayerEl,
          viewport,
        });
        await pd.textLayerTask.render();
        pd.textRendered = scale;
        pd.highlightCleanup = highlightDocument(pd.textLayerEl, initialHighlight, false);
      }
      if (pd.active) setErrors((items) => items.filter((num) => num !== pd.num));
    } catch (err) {
      if (pd.active && !["AbortException", "RenderingCancelledException"].includes(err.name)) {
        setErrors((items) => items.includes(pd.num) ? items : [...items, pd.num]);
      }
    } finally {
      pd.rendering = false;
      pd.task = null;
      if (pd.active && pd.visible && scale !== pd.scale) renderPage(pd);
    }
  }, [initialHighlight]);

  const goTo = useCallback((num) => {
    if (!Number.isInteger(num) || num < 1 || num > pages.length) return;
    const pd = pages[num - 1];
    if (!pd.el || !scrollRef.current) return;
    scrollRef.current.scrollTop = pd.el.offsetTop;
    currentRef.current = num;
    setPage(num);
    setPageInput(String(num));
    renderPage(pd);
  }, [pages, renderPage]);

  useEffect(() => {
    let stopped = false;
    let task;
    const controller = new AbortController();
    readyRef.current = false;
    setLoading(true);
    setFailed(false);
    setErrors([]);
    setPages([]);

    async function open() {
      try {
        const response = await fetch("/books/" + bookId + "/read?format=pdf", {
          cache: "no-store", signal: controller.signal,
        });
        if (!response.ok) throw new Error(response.status);
        const data = await response.arrayBuffer();
        if (stopped) return;
        task = pdfjsLib.getDocument({ data });
        const pdf = await task.promise;
        const items = [];
        for (let num = 1; num <= pdf.numPages; num++) {
          if (stopped) return;
          const pdfPage = await pdf.getPage(num);
          const viewport = pdfPage.getViewport({ scale: 1 });
          items.push({
            num,
            pdfPage,
            width: viewport.width,
            height: viewport.height,
            scale: 1,
            rendered: null,
            rendering: false,
            active: false,
            visible: false,
            highlightContent: null,
            highlightCleanup: null,
            textLayerTask: null,
            textRendered: null,
          });
        }
        let highlightedPage = null;
        const rangeStart = Number(initialPage);
        const requestedEnd = Number(initialEndPage);
        if (initialHighlight && Number.isInteger(rangeStart) && rangeStart >= 1 && rangeStart <= items.length) {
          const rangeEnd = Number.isInteger(requestedEnd)
            ? Math.min(items.length, requestedEnd, rangeStart + 4)
            : rangeStart;
          for (let num = rangeStart; num <= Math.max(rangeStart, rangeEnd); num++) {
            const textContent = await items[num - 1].pdfPage.getTextContent();
            if (!containsText(textContent.items.map((item) => item.str || ""), initialHighlight)) continue;
            items[num - 1].highlightContent = textContent;
            highlightedPage = num;
            break;
          }
        }
        let start = highlightedPage || Number(initialPage);
        if (!Number.isInteger(start) || start < 1 || start > items.length) {
          const saved = await fetch("/books/" + bookId + "/progress", { signal: controller.signal })
            .then((res) => res.ok ? res.json() : {}).catch(() => ({}));
          start = Number(saved.position);
        }
        if (stopped) return;
        currentRef.current = Number.isInteger(start) && start >= 1 && start <= items.length ? start : 1;
        setPages(items);
      } catch (err) {
        if (!stopped) { setFailed(true); setLoading(false); }
      }
    }
    open();
    return () => {
      stopped = true;
      controller.abort();
      if (task) task.destroy().catch(() => {});
    };
  }, [bookId, initialPage, initialEndPage, initialHighlight]);

  useLayoutEffect(() => {
    if (!pages.length) return;
    const host = scrollRef.current;
    let saveTimer;
    let dirty = false;
    let lastWidth = 0;
    for (const pd of pages) pd.active = true;

    function flush() {
      if (!readyRef.current || !dirty) return;
      dirty = false;
      fetch("/books/" + bookId + "/progress", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ position: String(currentRef.current), format: "pdf" }),
        keepalive: true,
      }).catch(() => {});
    }

    function resize() {
      const width = Math.min(720, Math.max(1, host.clientWidth - 24));
      if (width === lastWidth) return;
      lastWidth = width;
      for (const pd of pages) {
        pd.scale = width / pd.width;
        pd.el.style.height = pd.height * pd.scale + 48 + "px";
        pd.surface.style.width = width + "px";
        pd.surface.style.height = pd.height * pd.scale + "px";
        pd.surface.style.setProperty("--scale-factor", String(pd.scale));
        pd.canvas.style.width = width + "px";
        pd.canvas.style.height = pd.height * pd.scale + "px";
        if (pd.task) pd.task.cancel();
        if (pd.textLayerTask) pd.textLayerTask.cancel();
        pd.textRendered = null;
        if (pd.visible || pd.highlightContent) renderPage(pd);
      }
      goTo(currentRef.current);
    }

    function onScroll() {
      if (!readyRef.current) return;
      let num = 1;
      for (const pd of pages) {
        if (pd.el.offsetTop <= host.scrollTop + 4) num = pd.num;
        else break;
      }
      if (num !== currentRef.current) {
        currentRef.current = num;
        setPage(num);
        setPageInput(String(num));
      }
      dirty = true;
      clearTimeout(saveTimer);
      saveTimer = setTimeout(flush, 300);
    }

    resize();
    readyRef.current = true;
    dirty = true;
    setLoading(false);
    saveTimer = setTimeout(flush, 300);
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        const pd = pages[Number(entry.target.dataset.page) - 1];
        pd.visible = entry.isIntersecting;
        if (pd.visible) renderPage(pd);
      }
    }, { root: host, rootMargin: "800px 0px" });
    for (const pd of pages) observer.observe(pd.el);
    const resizer = new ResizeObserver(resize);
    resizer.observe(host);
    host.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      clearTimeout(saveTimer);
      flush();
      readyRef.current = false;
      observer.disconnect();
      resizer.disconnect();
      host.removeEventListener("scroll", onScroll);
      for (const pd of pages) {
        pd.active = false;
        if (pd.task) pd.task.cancel();
        if (pd.textLayerTask) pd.textLayerTask.cancel();
        if (pd.highlightCleanup) pd.highlightCleanup();
      }
    };
  }, [pages, bookId, goTo, renderPage]);

  return (
    <div className="reader">
      <div className="reader__bar" role="group" aria-label="PDF navigation">
        <button type="button" className="text-btn" onClick={onBack}>Back</button>
        <span className="reader__spacer" />
        <button type="button" className="text-btn" onClick={() => goTo(page - 1)}
          disabled={loading || failed || page <= 1}>Previous</button>
        <button type="button" className="text-btn" onClick={() => goTo(page + 1)}
          disabled={loading || failed || page >= pages.length}>Next</button>
        {!loading && !failed && (
          <form className="reader__page-form" onSubmit={(event) => {
            event.preventDefault();
            goTo(Number(pageInput));
          }}>
            <label htmlFor="reader-page">Page</label>
            <input id="reader-page" type="number" min="1" max={pages.length} step="1"
              required value={pageInput} onChange={(event) => setPageInput(event.target.value)} />
            <span>of {pages.length}</span>
            <button className="text-btn" type="submit">Go</button>
          </form>
        )}
      </div>
      {loading && <p className="reader__message" role="status">Preparing pages…</p>}
      {failed && <p className="reader__message" role="alert">This PDF could not be opened. Go back and try opening it again.</p>}
      <div ref={scrollRef} className="reader__pdf" aria-label="PDF pages" aria-busy={loading}>
        {pages.map((pd) => (
          <div key={pd.num} ref={(el) => { pd.el = el; }} data-page={pd.num} className="reader__pdf-page">
            <div ref={(el) => { pd.surface = el; }} className="reader__pdf-surface">
              <canvas ref={(canvas) => { pd.canvas = canvas; }} className="reader__pdf-canvas"
                role="img" aria-label={`Page ${pd.num}`} />
              <div
                ref={(el) => { pd.textLayerEl = el; }}
                className="reader__pdf-text-layer"
                aria-hidden="true"
              />
            </div>
            {errors.includes(pd.num) && (
              <div className="reader__page-error" role="alert">
                <p>Page {pd.num} could not be displayed.</p>
                <button className="btn" type="button" onClick={() => renderPage(pd)}>Retry page</button>
              </div>
            )}
            <p className="reader__pdf-label">{pd.num}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
