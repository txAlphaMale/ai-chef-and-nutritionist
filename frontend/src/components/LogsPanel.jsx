import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import InfoTip from "./InfoTip";
import { formatTimestamp } from "../utils/datetime";

const PAGE_SIZE = 100;
const LEVELS = ["error", "warning", "info", "debug"];

/** Settings > Logs (capstone review 2026-08-16, backlog B24.2).
 *
 * Until this existed, every operational question about this app -- why an
 * import produced nothing, whether Ollama timed out, what the model
 * actually returned -- required `docker compose logs` and a shell on the
 * host machine. Nothing was visible from inside the app, which is the
 * concrete reason a separate text "import health report" had to be built
 * back in August: none of that session's data defects could be seen in the
 * UI.
 *
 * Paginated from the first version, unlike the recipes list was. The log is
 * the one table here guaranteed to outgrow "just fetch them all", and
 * B24.1 was a recent enough lesson not to repeat.
 */
export default function LogsPanel() {
  const [page, setPage] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [level, setLevel] = useState("");
  const [source, setSource] = useState("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [clearing, setClearing] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
      if (level) params.set("level", level);
      if (source) params.set("source", source);
      if (search.trim()) params.set("search", search.trim());
      setPage(await api.get(`/system/logs?${params}`));
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [level, source, search, offset]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleClear() {
    // A plain window.confirm rather than a custom modal: this is
    // destructive, rare, and the browser's own dialog is the one thing a
    // user cannot mistake for part of the page.
    if (!window.confirm("Delete every log entry? This cannot be undone.")) return;
    setClearing(true);
    try {
      await api.del("/system/logs");
      setOffset(0);
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setClearing(false);
    }
  }

  const total = page?.total ?? 0;
  const shown = page?.entries?.length ?? 0;
  const hasPrev = offset > 0;
  const hasNext = offset + shown < total;

  return (
    <div className="card">
      <div className="page-toolbar">
        <h3 className="u-no-margin">
          Logs
          <InfoTip label="Logs" wikiEntry="application-logs">
            What the app has been doing, kept in the database rather than only in the container&apos;s output. Start
            with <strong>error</strong> and <strong>warning</strong> when something has gone wrong; <strong>debug</strong>{" "}
            carries the model request and response sizes.
          </InfoTip>
        </h3>
        <button className="btn btn-secondary btn-sm" onClick={load} disabled={loading}>
          Refresh
        </button>
      </div>

      <p className="hint">
        Kept for 30 days or 20,000 entries, whichever comes first, then trimmed automatically. Model responses are
        recorded by <strong>shape</strong> -- lengths and short previews -- never in full, because a reply in this app
        can contain your bloodwork.
      </p>

      <div className="form-row">
        <label>
          Level
          <select
            value={level}
            onChange={(e) => {
              setOffset(0);
              setLevel(e.target.value);
            }}
          >
            <option value="">All levels</option>
            {LEVELS.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <label>
          Source
          <select
            value={source}
            onChange={(e) => {
              setOffset(0);
              setSource(e.target.value);
            }}
          >
            <option value="">All sources</option>
            {/* Discovered from the data rather than hardcoded, so a new
                service appears here without a frontend change. */}
            {(page?.sources || []).map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label>
          Contains
          <input
            type="search"
            value={search}
            placeholder="Search message text"
            onChange={(e) => {
              setOffset(0);
              setSearch(e.target.value);
            }}
          />
        </label>
      </div>

      {error && <p className="error-text">{error}</p>}

      {loading && !page ? (
        <p>Loading logs...</p>
      ) : total === 0 ? (
        <p className="hint">
          Nothing logged yet{level || source || search ? " for this filter" : ""}. Entries appear as the app works --
          importing a recipe or generating a meal plan is the quickest way to see some.
        </p>
      ) : (
        <>
          <table className="data-table log-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Level</th>
                <th>Source</th>
                <th>Message</th>
              </tr>
            </thead>
            <tbody>
              {page.entries.map((entry) => (
                <tr key={entry.id}>
                  <td data-label="Time" className="log-time">
                    {formatTimestamp(entry.created_at)}
                  </td>
                  <td data-label="Level">
                    <span className={`tag log-level log-level-${entry.level}`}>{entry.level}</span>
                  </td>
                  <td data-label="Source">{entry.source}</td>
                  <td data-label="Message" className="log-message">
                    {entry.message}
                    {entry.job_id && <div className="hint">job {entry.job_id}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="page-toolbar">
            <span className="hint">
              {offset + 1}&ndash;{offset + shown} of {total}
            </span>
            <span>
              <button
                className="btn btn-secondary btn-sm"
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                disabled={!hasPrev || loading}
              >
                Newer
              </button>{" "}
              <button
                className="btn btn-secondary btn-sm"
                onClick={() => setOffset(offset + PAGE_SIZE)}
                disabled={!hasNext || loading}
              >
                Older
              </button>
            </span>
          </div>
        </>
      )}

      <p className="hint">
        <button className="btn btn-delete-selected btn-sm" onClick={handleClear} disabled={clearing || total === 0}>
          {clearing ? "Clearing..." : "Clear all logs"}
        </button>
      </p>
    </div>
  );
}
