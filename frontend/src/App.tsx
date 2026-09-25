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
import CameraSetupWizard from "./pages/CameraSetupWizard";
import IncidentsPage from "./pages/IncidentsPage";
import OverviewPage from "./pages/OverviewPage";
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
      <Sidebar operator={operator} onSignOut={signOut} />
      <div className="app-main">
      <Routes>
        <Route path="/" element={<OverviewPage />} />
        <Route path="/cameras" element={<DashboardPage />} />
        <Route path="/setup-camera" element={<CameraSetupWizard />} />
        <Route path="/incidents" element={<IncidentsPage />} />
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
    </div>
  );
}

function Sidebar({ operator, onSignOut }: { operator: string; onSignOut: () => void }) {
  const navigate = useNavigate();
  const [awaiting, setAwaiting] = useState<number | null>(null);

  // The badge reads the same event records the incident inbox does, so the two
  // can never disagree about how much is waiting.
  useEffect(() => {
    let cancelled = false;
    const poll = () => api.listEvents({ decision: "unreviewed", limit: 1 })
      .then((page) => { if (!cancelled) setAwaiting(page.total); })
      .catch(() => { if (!cancelled) setAwaiting(null); });
    poll();
    const timer = window.setInterval(poll, 60000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);

  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true">N</span>
        <span>Numenor</span>
      </div>

      <nav aria-label="Main">
        <span className="nav-group">Monitor</span>
        <NavLink to="/" end>Factory overview</NavLink>
        <NavLink to="/incidents">
          Incidents
          {awaiting ? <span className="nav-count">{awaiting}</span> : null}
        </NavLink>

        <span className="nav-group">Set up</span>
        <NavLink to="/setup-camera">Camera setup</NavLink>
        <NavLink to="/cameras">All cameras</NavLink>
        <NavLink to="/workspace">Factory scene</NavLink>
        <NavLink to="/align">Align live view</NavLink>

        <span className="nav-group">Advanced</span>
        <NavLink to="/setup">Guided calibration</NavLink>
        <NavLink to="/relationships">Camera links</NavLink>
        <NavLink to="/readiness">Readiness</NavLink>
      </nav>

      <div className="sidebar-foot">
        <span className="badge badge-neutral" title="All processing stays on this host">
          <span aria-hidden="true">⌂</span>On-premise
        </span>
        <span>{operator}</span>
        <button className="btn btn-sm btn-ghost" type="button"
                onClick={() => { onSignOut(); navigate("/"); }}>
          Sign out
        </button>
      </div>
    </aside>
  );
}
