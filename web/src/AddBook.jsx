import { useState } from "react";

export default function AddBook({ onClose, onBookAdded }) {
  const [file, setFile] = useState(null);
  const [cover, setCover] = useState(null);
  const [inspecting, setInspecting] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(null);
  const [uploadedBookId, setUploadedBookId] = useState(null);
  const [error, setError] = useState(null);

  const [title, setTitle] = useState("");
  const [authors, setAuthors] = useState("");
  const [topics, setTopics] = useState("");
  const [language, setLanguage] = useState("en");
  const [pubYear, setPubYear] = useState("");
  const [publisher, setPublisher] = useState("");
  const [description, setDescription] = useState("");
  const [licenseName, setLicenseName] = useState("Open Access");
  const [licenseUrl, setLicenseUrl] = useState("https://creativecommons.org/");
  const [hasEmbeddedCover, setHasEmbeddedCover] = useState(false);

  async function handleFileChange(e) {
    const selected = e.target.files && e.target.files[0];
    if (!selected) return;

    setFile(selected);
    setError(null);
    setInspecting(true);

    const formData = new FormData();
    formData.append("file", selected);

    try {
      const res = await fetch("/books/inspect", {
        method: "POST",
        body: formData,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || "Failed to inspect file");

      if (data.title) setTitle(data.title);
      if (data.authors && data.authors.length > 0) {
        setAuthors(data.authors.join("\n"));
      }
      if (data.topics && data.topics.length > 0) {
        setTopics(data.topics.join("\n"));
      }
      if (data.language) setLanguage(data.language);
      if (data.pub_year) setPubYear(String(data.pub_year));
      if (data.publisher) setPublisher(data.publisher);
      if (data.description) setDescription(data.description);
      if (data.license_name) setLicenseName(data.license_name);
      if (data.license_url) setLicenseUrl(data.license_url);
      setHasEmbeddedCover(Boolean(data.has_cover));
    } catch (err) {
      setError(err.message);
    } finally {
      setInspecting(false);
    }
  }

  function handleSubmit(e) {
    e.preventDefault();
    if (!file) {
      setError("Please select a book file (.epub, .pdf, or .html)");
      return;
    }

    const formData = new FormData();
    const selectedExtension = file.name.split(".").pop().toLowerCase();
    const ext = selectedExtension === "htm" ? "html" : selectedExtension;
    formData.append(ext, file);

    if (cover) {
      formData.append("cover", cover);
    }

    formData.append("title", title);
    formData.append("language", language);
    formData.append("authors", authors || "Unknown");
    formData.append("topics", topics || "General");
    if (description) formData.append("description", description);
    if (pubYear) formData.append("pub_year", pubYear);
    if (publisher) formData.append("publisher", publisher);
    formData.append("license_name", licenseName || "Open Access");
    formData.append("license_url", licenseUrl || "https://creativecommons.org/");

    setSubmitting(true);
    setUploadProgress(0);
    setError(null);

    const request = new XMLHttpRequest();
    request.open("POST", "/books");
    request.responseType = "json";

    request.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        setUploadProgress(Math.round((event.loaded / event.total) * 100));
      }
    };

    request.onload = () => {
      setSubmitting(false);
      const data = request.response || {};
      if (request.status >= 200 && request.status < 300) {
        setUploadProgress(100);
        setUploadedBookId(data.id);
        return;
      }
      setUploadProgress(null);
      setError(data.error || `Upload failed (${request.status})`);
    };

    request.onerror = () => {
      setSubmitting(false);
      setUploadProgress(null);
      setError("The upload could not reach the server. Check your connection and try again.");
    };

    request.send(formData);
  }

  if (uploadedBookId) {
    return (
      <div className="modal-backdrop" role="dialog" aria-modal="true">
        <div className="modal modal--confirmation">
          <p className="upload-confirmation__mark" aria-hidden="true">✓</p>
          <h2 className="modal__title">Submission received</h2>
          <p className="upload-confirmation__text">
            Your book has been submitted for review. We’ll let you know if it has been accepted.
          </p>
          <button type="button" className="btn" onClick={() => onBookAdded(uploadedBookId)}>
            View submission
          </button>
        </div>
      </div>
    );
  }

  return (
    <div
      className="modal-backdrop"
      onClick={submitting ? undefined : onClose}
      role="dialog"
      aria-modal="true"
    >
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal__head">
          <h2 className="modal__title">Submit a book</h2>
          <button
            type="button"
            className="text-btn modal__close"
            onClick={onClose}
            aria-label="Close"
            disabled={submitting}
          >
            ✕
          </button>
        </div>

        <form onSubmit={handleSubmit} className="modal__form">
          <div className="dropzone">
            <label className="dropzone__label">
              <input
                type="file"
                accept=".epub,.pdf,.html,.htm"
                onChange={handleFileChange}
                className="dropzone__input"
                disabled={inspecting || submitting}
              />
              <span className="dropzone__prompt">
                {file ? (
                  <strong>Selected: {file.name}</strong>
                ) : (
                  "Choose or drop a book file (.epub, .pdf, .html)"
                )}
              </span>
              <span className="dropzone__hint">Metadata will be automatically extracted</span>
            </label>
          </div>

          {inspecting && <p className="status">Extracting metadata from file...</p>}
          {submitting && (
            <div className="upload-progress" aria-live="polite">
              <div className="upload-progress__row">
                <span>{uploadProgress === 100 ? "Finishing upload..." : "Uploading book..."}</span>
                {uploadProgress !== null && <span>{uploadProgress}%</span>}
              </div>
              <progress
                className="upload-progress__bar"
                value={uploadProgress ?? undefined}
                max="100"
              />
            </div>
          )}
          {error && <p className="auth__error" role="alert">{error}</p>}

          <div className="form-grid">
            <label className="field">
              <span>Title *</span>
              <input
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                required
                placeholder="Book title"
              />
            </label>

            <label className="field">
              <span>Language Code (2-3 letters) *</span>
              <input
                type="text"
                value={language}
                onChange={(e) => setLanguage(e.target.value.toLowerCase().trim())}
                required
                maxLength={3}
                placeholder="e.g. en"
              />
            </label>
          </div>

          <div className="form-grid">
            <label className="field">
              <span>Author(s) (one per line)</span>
              <textarea
                rows={2}
                value={authors}
                onChange={(e) => setAuthors(e.target.value)}
                placeholder="Author name"
              />
            </label>

            <label className="field">
              <span>Topic(s) / Subjects (one per line)</span>
              <textarea
                rows={2}
                value={topics}
                onChange={(e) => setTopics(e.target.value)}
                placeholder="e.g. Programming, Fiction"
              />
            </label>
          </div>

          <div className="form-grid">
            <label className="field">
              <span>Publication Year</span>
              <input
                type="number"
                value={pubYear}
                onChange={(e) => setPubYear(e.target.value)}
                placeholder="e.g. 2024"
              />
            </label>

            <label className="field">
              <span>Publisher</span>
              <input
                type="text"
                value={publisher}
                onChange={(e) => setPublisher(e.target.value)}
                placeholder="Publisher name"
              />
            </label>
          </div>

          <label className="field">
            <span>Description</span>
            <textarea
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Book summary or description"
            />
          </label>

          <div className="form-grid">
            <label className="field">
              <span>License Name</span>
              <input
                type="text"
                value={licenseName}
                onChange={(e) => setLicenseName(e.target.value)}
                placeholder="e.g. CC BY-SA 4.0 or Open Access"
              />
            </label>

            <label className="field">
              <span>License URL</span>
              <input
                type="url"
                value={licenseUrl}
                onChange={(e) => setLicenseUrl(e.target.value)}
                placeholder="https://creativecommons.org/..."
              />
            </label>
          </div>

          <label className="field">
            <span>Cover Image (Optional)</span>
            {hasEmbeddedCover && !cover && (
              <span className="field__hint">✓ Cover detected or generated from book file</span>
            )}
            <input
              type="file"
              accept="image/*"
              onChange={(e) => setCover(e.target.files && e.target.files[0])}
            />
          </label>

          <div className="modal__actions">
            <button type="button" className="text-btn" onClick={onClose} disabled={submitting}>
              Cancel
            </button>
            <button type="submit" className="btn" disabled={!file || submitting || inspecting}>
              {submitting ? "Submitting..." : "Submit"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
