export function formatTimeAgo(isoDate) {
  if (!isoDate) return "";
  const date = new Date(isoDate);
  const now = new Date();
  const diffMs = now - date;
  const diffSec = Math.floor(diffMs / 1000);
  const diffMin = Math.floor(diffSec / 60);
  const diffHours = Math.floor(diffMin / 60);
  const diffDays = Math.floor(diffHours / 24);

  if (diffSec < 60) return "Just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays === 1) return "Yesterday";
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export default function ContinueReading({ history, onResume }) {
  if (!history || history.length === 0) return null;

  const current = history[0];
  const timeAgo = formatTimeAgo(current.updated_at);

  return (
    <section className="continue-reading" aria-labelledby="continue-reading-title">
      <h2 className="list-head" id="continue-reading-title">Continue reading</h2>
      <button
        type="button"
        className="continue-reading__book"
        onClick={() => onResume(current)}
      >
        {current.cover_ref ? (
          <img
            className="continue-reading__cover"
            src={"/books/" + current.id + "/cover"}
            alt=""
            loading="lazy"
          />
        ) : (
          <span className="continue-reading__cover continue-reading__cover--placeholder" aria-hidden="true" />
        )}
        <span className="continue-reading__info">
          {timeAgo && <span className="continue-reading__time">Last opened {timeAgo.toLowerCase()}</span>}
          <span className="continue-reading__title">{current.title}</span>
          <span className="continue-reading__authors">
            {current.authors && current.authors.join(", ")}
          </span>
        </span>
        <span className="continue-reading__action" aria-hidden="true">›</span>
      </button>
    </section>
  );
}
