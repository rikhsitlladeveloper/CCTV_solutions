import { useCallback, useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { api, getToken, setToken } from "./lib/api";
import CameraDetailPage from "./pages/CameraDetailPage";
import DashboardPage from "./pages/DashboardPage";
import FloorPlanEditorPage from "./pages/FloorPlanEditorPage";
import LoginPage from "./pages/LoginPage";
import RegisterWizardPage from "./pages/RegisterWizardPage";

export default function App() {
  const [operator, setOperator] = useState<string | null>(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    if (!getToken()) { setChecking(false); return; }
    api.me()
      .then((r) => setOperator(r.username))
      .catch(() => setOperator(null))
      .finally(() => setChecking(false));
  }, []);

  useEffect(() => {
    const onSignedOut = () => setOperator(null);
    window.addEventListener("numenor:signed-out", onSignedOut);
    return () => window.removeEventListener("numenor:signed-out", onSignedOut);
  }, []);

  const signOut = useCallback(() => {
    setToken(null);
    setOperator(null);
  }, []);

  if (checking) {
    return <div className="login-shell"><span className="spinner" aria-label="Loading" /></div>;
  }
  if (!operator) return <LoginPage onSignedIn={setOperator} />;

  return (
    <div className="app-shell">
      <TopBar operator={operator} onSignOut={signOut} />
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/cameras/new" element={<RegisterWizardPage />} />
        <Route path="/cameras/:id" element={<CameraDetailPage />} />
        <Route path="/floor-plans" element={<FloorPlanEditorPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </div>
  );
}

function TopBar({ operator, onSignOut }: { operator: string; onSignOut: () => void }) {
  const navigate = useNavigate();
  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true">N</span>
        <span>Numenor</span>
        <span className="brand-sub">Camera Setup</span>
      </div>
      <nav className="topnav">
        <NavLink to="/" end>Dashboard</NavLink>
        <NavLink to="/floor-plans">Floor plans</NavLink>
      </nav>
      <div className="topbar-spacer" />
      <div className="topbar-user">
        <span className="badge badge-neutral" title="All processing stays on this host">
          <span aria-hidden="true">⌂</span>On-premise
        </span>
        <span>{operator}</span>
        <button className="btn btn-sm btn-ghost" onClick={() => { onSignOut(); navigate("/"); }} type="button">
          Sign out
        </button>
      </div>
    </header>
  );
}
