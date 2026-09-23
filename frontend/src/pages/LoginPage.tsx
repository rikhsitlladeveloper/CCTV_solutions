import { useState } from "react";
import { ApiError, api, setToken } from "../lib/api";
import { Notice } from "../components/ui";

export default function LoginPage({ onSignedIn }: { onSignedIn: (username: string) => void }) {
  const [username, setUsername] = useState("operator");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const res = await api.login(username, password);
      setToken(res.access_token);
      setPassword("");
      onSignedIn(res.username);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sign-in failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-shell">
      <div className="card login-card">
        <div className="login-head">
          <div className="brand-mark" aria-hidden="true">N</div>
          <h1>Numenor</h1>
          <p className="page-sub">Camera registration and location setup</p>
        </div>
        <form className="card-pad" onSubmit={submit}>
          {error && <Notice tone="danger" title="Sign-in failed">{error}</Notice>}
          <div className="field">
            <label htmlFor="login-user">Operator</label>
            <input id="login-user" type="text" value={username} autoComplete="username"
                   onChange={(e) => setUsername(e.target.value)} required />
          </div>
          <div className="field">
            <label htmlFor="login-pass">Password</label>
            <div className="input-group">
              <input id="login-pass" type={showPassword ? "text" : "password"} value={password}
                     autoComplete="current-password" onChange={(e) => setPassword(e.target.value)} required />
              <button className="btn" type="button" onClick={() => setShowPassword((v) => !v)}
                      aria-pressed={showPassword}>
                {showPassword ? "Hide" : "Show"}
              </button>
            </div>
          </div>
          <button className="btn btn-primary btn-block" type="submit" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
          <p className="hint" style={{ marginTop: 12, textAlign: "center" }}>
            The operator account is created on first start-up and printed to the server console.
          </p>
        </form>
      </div>
    </div>
  );
}
