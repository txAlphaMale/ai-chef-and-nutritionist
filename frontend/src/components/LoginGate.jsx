import { useState } from "react";
import { api } from "../api";

// Backlog B9.4 (via B10.2, 2026-08-01) -- shown by App.jsx instead of
// the app whenever /api/auth/status reports enabled but not yet
// authenticated. A successful login just re-checks status (the backend
// already set the session cookie); there's no client-side token to
// store, the httponly cookie is the whole mechanism.
export default function LoginGate({ onSuccess }) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function handleSubmit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/auth/login", { password });
      setPassword("");
      onSuccess();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-gate">
      <form className="login-gate-card card" onSubmit={handleSubmit}>
        <h2>Chef</h2>
        <p className="hint">Enter the household password to continue.</p>
        <label>
          Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoFocus required />
        </label>
        {error && <p className="error-text">{error}</p>}
        <button className="btn btn-primary" type="submit" disabled={busy || !password}>
          {busy ? "Checking..." : "Unlock"}
        </button>
      </form>
    </div>
  );
}
