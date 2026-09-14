const HIGHLIGHT_NAME = "xaperio-answer";
const STYLE_ATTRIBUTE = "data-xaperio-answer-highlight";

function normalizedValue(value) {
  return String(value || "").normalize("NFKC").toLocaleLowerCase().replace(/\s+/gu, " ").trim();
}

export function containsText(parts, phrase) {
  const needle = normalizedValue(phrase);
  return needle.length > 0 && normalizedValue(parts.join(" ")).includes(needle);
}

export function findTextRange(root, phrase) {
  const needle = normalizedValue(phrase);
  const documentEl = root?.ownerDocument || root;
  const searchRoot = root?.nodeType === 9 ? root.body : root;
  const view = documentEl?.defaultView;
  if (!needle || !searchRoot || !view) return null;

  const walker = documentEl.createTreeWalker(
    searchRoot,
    view.NodeFilter.SHOW_TEXT,
    {
      acceptNode(node) {
        if (!node.data || !node.data.trim()) return view.NodeFilter.FILTER_REJECT;
        if (node.parentElement?.closest("script, style, template, noscript")) {
          return view.NodeFilter.FILTER_REJECT;
        }
        return view.NodeFilter.FILTER_ACCEPT;
      },
    },
  );
  const characters = [];
  const positions = [];
  let node;
  while ((node = walker.nextNode())) {
    const data = node.data;
    const first = data.match(/\S/u)?.[0];
    const previous = characters.at(-1);
    if (previous && previous !== " " && first && /[\p{L}\p{N}]/u.test(previous) && /[\p{L}\p{N}]/u.test(first)) {
      characters.push(" ");
      positions.push({ node, start: 0, end: 0 });
    }
    let offset = 0;
    for (const character of data) {
      const start = offset;
      offset += character.length;
      const normalized = character.normalize("NFKC").toLocaleLowerCase();
      for (const normalizedCharacter of normalized) {
        if (/\s/u.test(normalizedCharacter)) {
          if (characters.length && characters.at(-1) !== " ") {
            characters.push(" ");
            positions.push({ node, start, end: offset });
          }
        } else {
          characters.push(normalizedCharacter);
          positions.push({ node, start, end: offset });
        }
      }
    }
  }

  while (characters.at(-1) === " ") {
    characters.pop();
    positions.pop();
  }
  const matchStart = characters.join("").indexOf(needle);
  if (matchStart < 0) return null;
  const matchEnd = matchStart + needle.length - 1;
  const start = positions[matchStart];
  const end = positions[matchEnd];
  if (!start || !end) return null;
  const range = documentEl.createRange();
  range.setStart(start.node, start.start);
  range.setEnd(end.node, end.end);
  return range;
}

export function highlightDocument(root, phrase, scroll = true) {
  const range = findTextRange(root, phrase);
  const documentEl = root?.ownerDocument || root;
  const view = documentEl?.defaultView;
  if (!range || !view?.CSS?.highlights || typeof view.Highlight !== "function") return null;

  let style = documentEl.head?.querySelector(`[${STYLE_ATTRIBUTE}]`);
  if (!style && documentEl.head) {
    style = documentEl.createElement("style");
    style.setAttribute(STYLE_ATTRIBUTE, "");
    style.textContent = `::highlight(${HIGHLIGHT_NAME}) { background: #f5e7ac; color: inherit; }`;
    documentEl.head.append(style);
  }
  const highlight = new view.Highlight(range);
  view.CSS.highlights.set(HIGHLIGHT_NAME, highlight);
  if (scroll) {
    view.requestAnimationFrame(() => {
      const bounds = range.getBoundingClientRect();
      view.scrollTo({
        top: (view.scrollY || view.pageYOffset || 0) + bounds.top - view.innerHeight * 0.3,
        behavior: "smooth",
      });
    });
  }
  return () => {
    if (view.CSS.highlights.get(HIGHLIGHT_NAME) === highlight) {
      view.CSS.highlights.delete(HIGHLIGHT_NAME);
    }
  };
}
