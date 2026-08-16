import { useEffect, useState } from "react";
import { api } from "../api";
import InfoTip from "./InfoTip";

/** Nutritionist knowledge file management -- upload a PDF/txt/md, see
 * whether text extraction succeeded (has_content), toggle it active
 * (only active files ground meal-plan generation), and delete it. */
export default function KnowledgeFilesPanel() {
  const [files, setFiles] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [description, setDescription] = useState("");
  const [uploading, setUploading] = useState(false);
  // Audit P2-6 -- retrieval no longer silently rebuilds a stale index on
  // the way past (that turned the next chat message into a full re-embed
  // on the single job worker, blocking every other AI feature). So the
  // staleness has to be visible and fixable here instead.
  const [indexStatus, setIndexStatus] = useState(null);
  const [reindexing, setReindexing] = useState(false);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const [rows, status] = await Promise.all([
        api.get("/knowledge-files"),
        api.get("/knowledge-files/index-status"),
      ]);
      setFiles(rows);
      setIndexStatus(status);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function handleUpload(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setError(null);
    try {
      const formData = new FormData();
      formData.append("file", file);
      if (description.trim()) formData.append("description", description.trim());
      await api.post("/knowledge-files", formData);
      setDescription("");
      refresh();
    } catch (err) {
      setError(err.message);
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  }

  async function toggleActive(kf) {
    await api.patch(`/knowledge-files/${kf.id}`, { is_active: !kf.is_active });
    refresh();
  }

  async function removeFile(kf) {
    if (!window.confirm(`Remove "${kf.filename}"?`)) return;
    await api.del(`/knowledge-files/${kf.id}`);
    refresh();
  }

  async function handleReindex() {
    setReindexing(true);
    setError(null);
    try {
      await api.post("/knowledge-files/reindex", {});
    } catch (e) {
      setError(e.message);
    } finally {
      setReindexing(false);
    }
  }

  return (
    <div className="card">
      <h3>
        Nutritionist knowledge files
        <InfoTip label="Knowledge files" wikiEntry="knowledge-files">
          Reference documents Chef retrieves from when planning a week or answering a question, so its advice
          comes from something you chose rather than from whatever the model remembers. Bundled defaults ship
          switched OFF.
        </InfoTip>
      </h3>
      <p className="hint">
        Upload reference material (a doctor's guidance sheet, a specific diet plan, PDF/txt/md) to ground meal-plan
        generation and chat. Only active files are used; only the most relevant excerpts are retrieved for a given
        request, not the whole file every time.
      </p>
      {error && <p className="error-text">{error}</p>}
      {indexStatus?.stale_count > 0 && (
        <div className="index-stale-notice">
          <strong>
            {indexStatus.stale_count} file{indexStatus.stale_count === 1 ? "" : "s"} not indexed for the current
            embedding model ({indexStatus.embed_model}).
          </strong>{" "}
          Chat and meal-plan generation will not use {indexStatus.stale_count === 1 ? "it" : "them"} until you
          reindex. This runs in the background and can take a while for large files.
          <div className="form-actions">
            <button className="btn btn-secondary" disabled={reindexing} onClick={handleReindex}>
              {reindexing ? "Queued..." : "Reindex now"}
            </button>
          </div>
        </div>
      )}
      {loading ? (
        <p>Loading...</p>
      ) : files.length === 0 ? (
        <p>No knowledge files uploaded yet.</p>
      ) : (
        <ul className="knowledge-file-list">
          {files.map((kf) => (
            <li key={kf.id} className="knowledge-file-item">
              <div>
                <strong>{kf.filename}</strong>
                {!kf.has_content && <span className="tag">text not extracted</span>}
                {kf.has_content && (
                  <span className="tag">{kf.chunk_count > 0 ? `${kf.chunk_count} chunk${kf.chunk_count === 1 ? "" : "s"} indexed` : "indexing..."}</span>
                )}
                {!kf.is_active && <span className="tag">inactive</span>}
                {kf.description && <p className="hint">{kf.description}</p>}
                {kf.content_excerpt && <p className="hint knowledge-excerpt">&ldquo;{kf.content_excerpt}&hellip;&rdquo;</p>}
              </div>
              <div className="knowledge-file-actions">
                <button className="btn-link" onClick={() => toggleActive(kf)}>
                  {kf.is_active ? "Deactivate" : "Activate"}
                </button>
                <button className="btn-link btn-link-danger" onClick={() => removeFile(kf)}>
                  Delete
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
      <div className="form-row">
        <input
          placeholder="Description (optional)"
          aria-label="Knowledge file description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          className="u-flex-1"
        />
        <label className="btn btn-secondary file-btn">
          {uploading ? "Uploading..." : "Upload file"}
          <input type="file" accept=".pdf,.txt,.md" onChange={handleUpload} disabled={uploading} hidden />
        </label>
      </div>
    </div>
  );
}
