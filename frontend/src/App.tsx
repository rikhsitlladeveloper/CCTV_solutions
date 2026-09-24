import { useCallback, useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { api, getToken, setToken } from "./lib/api";
import CameraCalibrationPage from "./pages/CameraCalibrationPage";
import CameraDetailPage from "./pages/CameraDetailPage";
import DashboardPage from "./pages/DashboardPage";
import FactoryMapPage from "./pages/FactoryMapPage";
import FloorPlanEditorPage from "./pages/FloorPlanEditorPage";
import LoginPage from "./pages/LoginPage";
import MultiCameraCheckPage from "./pages/MultiCameraCheckPage";
import RegisterWizardPage from "./pages/RegisterWizardPage";
import RelationshipsPage from "./pages/RelationshipsPage";
import ReadinessPage from "./pages/ReadinessPage";
import SetupPage from "./pages/SetupPage";
import AlignPage from "./pages/AlignPage";
import WorkspacePage from "./pages/WorkspacePage";

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
        <Route path="/cameras/:id/calibration" element={<CameraCalibrationPage />} />
        <Route path="/workspace" element={<WorkspacePage />} />
        <Route path="/align" element={<AlignPage />} />
        <Route path="/readiness" element={<ReadinessPage />} />
        <Route path="/setup" element={<SetupPage />} />
        <Route path="/relationships" element={<RelationshipsPage />} />
        <Route path="/factory-map" element={<FactoryMapPage />} />
        <Route path="/multi-camera-check" element={<MultiCameraCheckPage />} />
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
        <NavLink to="/workspace">Workspace</NavLink>
        <NavLink to="/" end>Cameras</NavLink>
        <NavLink to="/setup">Guided setup</NavLink>
        <NavLink to="/relationships">Connections</NavLink>
        <NavLink to="/readiness">Readiness</NavLink>
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
