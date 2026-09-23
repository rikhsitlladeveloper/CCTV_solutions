import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, api } from "../lib/api";
import FloorPlanCanvas from "../components/FloorPlanCanvas";
import PreviewPanel from "../components/PreviewPanel";
import { Notice, Spinner, StatusBadge } from "../components/ui";
import type { CameraDraft, FloorPlan, Site, TestResult } from "../lib/types";
import type { CoordinateSystem } from "../lib/calibrationTypes";

const STEPS = [
  "Camera & connection",
  "Physical location",
  "Factory position",
  "Floor-plan marker",
  "Review & save",
];

const EMPTY_DRAFT: CameraDraft = {
  name: "", host: "", connection_type: "onvif",
  onvif_port: 80, onvif_path: "/onvif/device_service",
  rtsp_port: 554, stream_path: "",
  username: "", password: "",
  manufacturer: "", model: "", notes: "",
  selected_profile_token: null, selected_profile_name: null,
  profile_resolution: null, profile_encoding: null,
  site: "", site_id: null, building: "", building_id: null,
  floor: "", floor_id: null, area: "", area_id: null,
  installation_description: "", mounting_height_m: "", photo: null,
  positioning_method: "skip",
  coordinate_system_id: null,
  world_x: "0", world_y: "0", world_z: "4",
  world_roll: "-90", world_pitch: "0", world_yaw: "0",
  world_hfov: "78", world_range: "12",
  placement: null,
};

export default function RegisterWizardPage() {
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [draft, setDraft] = useState<CameraDraft>(EMPTY_DRAFT);
  const [test, setTest] = useState<TestResult | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const set = useCallback(<K extends keyof CameraDraft>(key: K, value: CameraDraft[K]) => {
    setDraft((d) => ({ ...d, [key]: value }));
  }, []);

  const step1Valid = draft.name.trim().length > 0 && draft.host.trim().length > 0;
  const step2Valid = Boolean((draft.site_id || draft.site.trim()) && (draft.building_id || draft.building.trim())
    && (draft.floor_id || draft.floor.trim()) && (draft.area_id || draft.area.trim()));

  const save = async () => {
    setSaving(true); setSaveError(null);
    try {
      const area = await api.resolveLocation({
        site: draft.site || undefined, site_id: draft.site_id ?? undefined,
        building: draft.building || undefined, building_id: draft.building_id ?? undefined,
        floor: draft.floor || undefined, floor_id: draft.floor_id ?? undefined,
        area: draft.area || undefined, area_id: draft.area_id ?? undefined,
      });

      const camera = await api.createCamera({
        name: draft.name.trim(),
        host: draft.host.trim(),
        connection_type: draft.connection_type,
        onvif_port: draft.onvif_port,
        onvif_path: draft.onvif_path || "/onvif/device_service",
        rtsp_port: draft.rtsp_port,
        stream_path: draft.stream_path || null,
        username: draft.username || null,
        password: draft.password || null,
        manufacturer: draft.manufacturer || null,
        model: draft.model || null,
        notes: draft.notes || null,
        area_id: area.id,
        installation_description: draft.installation_description || null,
        mounting_height_m: draft.mounting_height_m ? Number(draft.mounting_height_m) : null,
        selected_profile_token: draft.selected_profile_token,
        selected_profile_name: draft.selected_profile_name,
        profile_resolution: draft.profile_resolution,
        profile_encoding: draft.profile_encoding,
      });

      // The password is now encrypted server-side; drop it from frontend state.
      setDraft((d) => ({ ...d, password: "" }));

      if (draft.photo) {
        try { await api.uploadPhoto(camera.id, draft.photo); } catch { /* non-fatal */ }
      }

      // Record the verification result from step 1 against the saved camera.
      if (test) {
        try { await api.testCamera(camera.id); } catch { /* the saved status stays "not tested" */ }
      }

      if (draft.positioning_method === "approximate" && draft.coordinate_system_id) {
        try {
          await api.saveManualPose(camera.id, {
            coordinate_system_id: draft.coordinate_system_id,
            x: Number(draft.world_x), y: Number(draft.world_y), z: Number(draft.world_z),
            roll_deg: Number(draft.world_roll), pitch_deg: Number(draft.world_pitch),
            yaw_deg: Number(draft.world_yaw),
            approx_hfov_deg: draft.world_hfov ? Number(draft.world_hfov) : null,
            approx_range_m: draft.world_range ? Number(draft.world_range) : null,
            activate: true,
          });
        } catch {
          // A failed placement must not lose the registered camera.
        }
      }

      if (draft.placement?.floor_plan_id) {
        await api.savePlacement(camera.id, {
          floor_plan_id: draft.placement.floor_plan_id,
          norm_x: draft.placement.norm_x,
          norm_y: draft.placement.norm_y,
          heading_deg: draft.placement.heading_deg,
          fov_deg: draft.placement.fov_deg,
          view_distance_m: draft.placement.view_distance_m,
          mounting_height_m: draft.mounting_height_m ? Number(draft.mounting_height_m) : null,
          review_status: "confirmed",
        });
      }

      navigate(`/cameras/${camera.id}`);
    } catch (e) {
      setSaveError(e instanceof ApiError ? e.message : "The camera could not be saved.");
      setSaving(false);
    }
  };

  return (
    <main className="page">
      <div className="page-head">
        <div className="grow">
          <h1>Register camera</h1>
          <p className="page-sub">
            Connection details, installation location, and an optional position on a floor plan.
          </p>
        </div>
        <button className="btn" type="button" onClick={() => navigate("/")}>Cancel</button>
      </div>

      <div className="stepper">
        {STEPS.map((label, i) => (
          <button key={label} type="button"
                  className={`step${i === step ? " is-current" : ""}${i < step ? " is-done" : ""}`}
                  disabled={i > step && !(i === 1 && step1Valid)}
                  onClick={() => { if (i <= step) setStep(i); }}>
            <span className="step-num">{i < step ? "✓" : i + 1}</span>
            {label}
          </button>
        ))}
      </div>

      <div className="card">
        <div className="card-pad">
          {step === 0 && (
            <StepConnection draft={draft} set={set} test={test} setTest={setTest} />
          )}
          {step === 1 && <StepLocation draft={draft} set={set} />}
          {step === 2 && <StepFactoryPosition draft={draft} set={set} />}
          {step === 3 && <StepPlacement draft={draft} set={set} />}
          {step === 4 && (
            <StepReview draft={draft} test={test} onEditStep={setStep} error={saveError} />
          )}
        </div>

        <div className="wizard-actions">
          <button className="btn" type="button" disabled={step === 0 || saving}
                  onClick={() => setStep((s) => Math.max(0, s - 1))}>
            Back
          </button>
          <div className="grow" />
          {step === 0 && !step1Valid && (
            <span className="small faint">A camera name and address are required.</span>
          )}
          {step === 1 && !step2Valid && (
            <span className="small faint">Site, building, floor and area are required.</span>
          )}
          {step < 4 ? (
            <button className="btn btn-primary" type="button"
                    disabled={(step === 0 && !step1Valid) || (step === 1 && !step2Valid)}
                    onClick={() => setStep((s) => s + 1)}>
              Continue
            </button>
          ) : (
            <button className="btn btn-primary" type="button" onClick={save} disabled={saving}>
              {saving ? "Saving…" : "Save camera"}
            </button>
          )}
        </div>
      </div>
    </main>
  );
}

/* ---------------- Step 1: camera details and connection ---------------- */

function StepConnection({ draft, set, test, setTest }: {
  draft: CameraDraft;
  set: <K extends keyof CameraDraft>(k: K, v: CameraDraft[K]) => void;
  test: TestResult | null;
  setTest: (t: TestResult | null) => void;
}) {
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState<"test" | "profiles" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const body = useMemo(() => ({
    host: draft.host.trim(),
    connection_type: draft.connection_type,
    onvif_port: draft.onvif_port,
    onvif_path: draft.onvif_path,
    rtsp_port: draft.rtsp_port,
    stream_path: draft.stream_path || null,
    username: draft.username || null,
    password: draft.password || null,
    profile_token: draft.selected_profile_token,
  }), [draft]);

  const run = async (kind: "test" | "profiles") => {
    setBusy(kind); setError(null);
    try {
      const result = kind === "test" ? await api.testUnsaved(body) : await api.discoverProfiles(body);
      setTest(result);
      if (result.manufacturer && !draft.manufacturer) set("manufacturer", result.manufacturer);
      if (result.model && !draft.model) set("model", result.model);
      if (result.profiles.length && !draft.selected_profile_token) {
        const chosen = result.profiles.find((p) => p.token === result.selected_profile_token)
          ?? result.profiles[0];
        set("selected_profile_token", chosen.token);
        set("selected_profile_name", chosen.name);
        set("profile_resolution", chosen.resolution);
        set("profile_encoding", chosen.encoding);
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The test could not be run.");
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <div className="section-title">Camera details</div>
      <div className="field-row">
        <div className="field">
          <label htmlFor="cam-name">Camera name <span className="req" aria-hidden="true">*</span></label>
          <input id="cam-name" type="text" value={draft.name} required
                 placeholder="Packaging line north"
                 onChange={(e) => set("name", e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="cam-host">IP address or hostname <span className="req" aria-hidden="true">*</span></label>
          <input id="cam-host" type="text" value={draft.host} required
                 placeholder="192.168.10.42" className="mono"
                 onChange={(e) => set("host", e.target.value)} />
          <span className="hint">Enter the address only, with no scheme or path.</span>
        </div>
      </div>

      <div className="field-row">
        <div className="field">
          <label htmlFor="cam-user">Username / login</label>
          <input id="cam-user" type="text" value={draft.username} autoComplete="off"
                 onChange={(e) => set("username", e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="cam-pass">Password</label>
          <div className="input-group">
            <input id="cam-pass" type={showPassword ? "text" : "password"} value={draft.password}
                   autoComplete="new-password" onChange={(e) => set("password", e.target.value)} />
            <button className="btn" type="button" onClick={() => setShowPassword((v) => !v)}
                    aria-pressed={showPassword} aria-label={showPassword ? "Hide password" : "Show password"}>
              {showPassword ? "Hide" : "Show"}
            </button>
          </div>
          <span className="hint">Encrypted on the server when you save. It is never sent back to this browser.</span>
        </div>
      </div>

      <div className="section-title">Connection method</div>
      <div className="radio-row" role="radiogroup" aria-label="Connection method">
        <label className={`radio-card${draft.connection_type === "onvif" ? " is-selected" : ""}`}>
          <input type="radio" name="conn" checked={draft.connection_type === "onvif"}
                 onChange={() => set("connection_type", "onvif")} />
          <span>
            <strong>ONVIF</strong>
            <span>Discovers stream profiles and snapshot support automatically.</span>
          </span>
        </label>
        <label className={`radio-card${draft.connection_type === "rtsp" ? " is-selected" : ""}`}>
          <input type="radio" name="conn" checked={draft.connection_type === "rtsp"}
                 onChange={() => set("connection_type", "rtsp")} />
          <span>
            <strong>Manual RTSP</strong>
            <span>Enter the stream path yourself. Use this when ONVIF is unavailable.</span>
          </span>
        </label>
      </div>

      {draft.connection_type === "onvif" ? (
        <div className="field-row" style={{ marginTop: 14 }}>
          <div className="field">
            <label htmlFor="onvif-port">ONVIF port</label>
            <input id="onvif-port" type="number" min={1} max={65535} value={draft.onvif_port}
                   onChange={(e) => set("onvif_port", Number(e.target.value))} />
          </div>
          <div className="field">
            <label htmlFor="onvif-path">ONVIF endpoint path</label>
            <input id="onvif-path" type="text" className="mono" value={draft.onvif_path}
                   onChange={(e) => set("onvif_path", e.target.value)} />
          </div>
        </div>
      ) : (
        <div className="field-row" style={{ marginTop: 14 }}>
          <div className="field">
            <label htmlFor="rtsp-port">RTSP port</label>
            <input id="rtsp-port" type="number" min={1} max={65535} value={draft.rtsp_port}
                   onChange={(e) => set("rtsp_port", Number(e.target.value))} />
          </div>
          <div className="field">
            <label htmlFor="rtsp-path">Stream path</label>
            <input id="rtsp-path" type="text" className="mono" placeholder="/Streaming/Channels/101"
                   value={draft.stream_path} onChange={(e) => set("stream_path", e.target.value)} />
          </div>
        </div>
      )}

      <div className="section-title">Verify the connection</div>
      {error && <Notice tone="danger" title="Test failed">{error}</Notice>}
      <div className="row" style={{ marginBottom: 12 }}>
        <button className="btn" type="button" disabled={!draft.host.trim() || busy !== null}
                onClick={() => run("test")}>
          {busy === "test" ? "Testing…" : "Test connection"}
        </button>
        {draft.connection_type === "onvif" && (
          <button className="btn" type="button" disabled={!draft.host.trim() || busy !== null}
                  onClick={() => run("profiles")}>
            {busy === "profiles" ? "Fetching…" : "Fetch stream profiles"}
          </button>
        )}
        {busy && <Spinner />}
        {test && <StatusBadge status={test.status} />}
      </div>

      {test && (
        <>
          <Notice tone={test.ok ? "ok" : test.login_ok ? "warn" : "danger"} title={test.summary}>
            {test.error}
            {test.login_ok && !test.stream_ok && (
              <div style={{ marginTop: 6 }}>
                The ONVIF login worked, so the camera is reachable and the credentials are right.
                Only the video stream failed. You can still save this camera and fix the stream later,
                or switch to manual RTSP and enter the stream path directly.
              </div>
            )}
          </Notice>

          {test.profiles.length > 0 && (
            <div className="field">
              <label htmlFor="profile-select">Stream profile</label>
              <select id="profile-select" value={draft.selected_profile_token ?? ""}
                      onChange={(e) => {
                        const p = test.profiles.find((x) => x.token === e.target.value);
                        set("selected_profile_token", p?.token ?? null);
                        set("selected_profile_name", p?.name ?? null);
                        set("profile_resolution", p?.resolution ?? null);
                        set("profile_encoding", p?.encoding ?? null);
                      }}>
                {test.profiles.map((p) => (
                  <option key={p.token} value={p.token}>
                    {p.name} — {p.resolution ?? "resolution unknown"} {p.encoding ?? ""}
                    {p.fps ? ` @ ${p.fps} fps` : ""}
                  </option>
                ))}
              </select>
            </div>
          )}

          <div className="section-title">Preview</div>
          <PreviewPanel draftBody={body} compact />
        </>
      )}

      {!test && (
        <Notice tone="info" title="Testing is optional">
          You can save a camera that has not been verified. It will be listed as
          “Not tested” until a connection test succeeds.
        </Notice>
      )}

      <div className="section-title">Optional details</div>
      <div className="field-row">
        <div className="field">
          <label htmlFor="cam-make">Manufacturer</label>
          <input id="cam-make" type="text" value={draft.manufacturer}
                 onChange={(e) => set("manufacturer", e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="cam-model">Model</label>
          <input id="cam-model" type="text" value={draft.model}
                 onChange={(e) => set("model", e.target.value)} />
        </div>
      </div>
      <div className="field">
        <label htmlFor="cam-notes">Notes</label>
        <textarea id="cam-notes" value={draft.notes} onChange={(e) => set("notes", e.target.value)} />
      </div>
    </>
  );
}

/* ---------------- Step 2: physical location ---------------- */

function StepLocation({ draft, set }: {
  draft: CameraDraft;
  set: <K extends keyof CameraDraft>(k: K, v: CameraDraft[K]) => void;
}) {
  const [sites, setSites] = useState<Site[]>([]);
  useEffect(() => { api.tree().then(setSites).catch(() => setSites([])); }, []);

  const site = sites.find((s) => s.id === draft.site_id);
  const building = site?.buildings.find((b) => b.id === draft.building_id);
  const floor = building?.floors.find((f) => f.id === draft.floor_id);

  return (
    <>
      <div className="section-title">Where is this camera installed?</div>
      <p className="hint" style={{ marginTop: -4, marginBottom: 14 }}>
        Pick an existing name or type a new one — new sites, buildings, floors and areas are created
        when you save.
      </p>

      <div className="field-row">
        <ComboField
          label="Factory / site" required id="loc-site"
          options={sites.map((s) => ({ id: s.id, name: s.name }))}
          selectedId={draft.site_id} text={draft.site}
          onSelect={(id, name) => {
            set("site_id", id); set("site", name);
            set("building_id", null); set("building", "");
            set("floor_id", null); set("floor", "");
            set("area_id", null); set("area", "");
          }}
          onText={(v) => { set("site", v); set("site_id", null); }}
        />
        <ComboField
          label="Building" required id="loc-building"
          options={(site?.buildings ?? []).map((b) => ({ id: b.id, name: b.name }))}
          selectedId={draft.building_id} text={draft.building}
          disabled={!draft.site_id && !draft.site.trim()}
          onSelect={(id, name) => {
            set("building_id", id); set("building", name);
            set("floor_id", null); set("floor", "");
            set("area_id", null); set("area", "");
          }}
          onText={(v) => { set("building", v); set("building_id", null); }}
        />
      </div>

      <div className="field-row">
        <ComboField
          label="Floor" required id="loc-floor"
          options={(building?.floors ?? []).map((f) => ({ id: f.id, name: f.name }))}
          selectedId={draft.floor_id} text={draft.floor}
          disabled={!draft.building_id && !draft.building.trim()}
          onSelect={(id, name) => {
            set("floor_id", id); set("floor", name);
            set("area_id", null); set("area", "");
          }}
          onText={(v) => { set("floor", v); set("floor_id", null); }}
        />
        <ComboField
          label="Area / zone" required id="loc-area"
          options={(floor?.areas ?? []).map((a) => ({ id: a.id, name: a.name }))}
          selectedId={draft.area_id} text={draft.area}
          disabled={!draft.floor_id && !draft.floor.trim()}
          onSelect={(id, name) => { set("area_id", id); set("area", name); }}
          onText={(v) => { set("area", v); set("area_id", null); }}
        />
      </div>

      <div className="section-title">Installation</div>
      <div className="field">
        <label htmlFor="loc-desc">Installation description</label>
        <input id="loc-desc" type="text" value={draft.installation_description}
               placeholder="Column B3, above packaging line"
               onChange={(e) => set("installation_description", e.target.value)} />
      </div>
      <div className="field-row">
        <div className="field">
          <label htmlFor="loc-height">Mounting height (metres)</label>
          <input id="loc-height" type="number" step="0.1" min="0" max="100" value={draft.mounting_height_m}
                 placeholder="4.2" onChange={(e) => set("mounting_height_m", e.target.value)} />
          <span className="hint">Optional.</span>
        </div>
        <div className="field">
          <label htmlFor="loc-photo">Installation photo</label>
          <input id="loc-photo" type="file" accept="image/png,image/jpeg"
                 onChange={(e) => set("photo", e.target.files?.[0] ?? null)} />
          <span className="hint">Optional. PNG or JPEG, stored on this server.</span>
        </div>
      </div>
    </>
  );
}

function ComboField({ label, id, options, selectedId, text, onSelect, onText, disabled, required }: {
  label: string; id: string;
  options: { id: number; name: string }[];
  selectedId: number | null; text: string;
  onSelect: (id: number | null, name: string) => void;
  onText: (value: string) => void;
  disabled?: boolean; required?: boolean;
}) {
  const [creating, setCreating] = useState(false);
  const isNew = creating || (!selectedId && text.trim().length > 0) || options.length === 0;

  return (
    <div className="field">
      <label htmlFor={id}>
        {label}{required && <span className="req" aria-hidden="true">*</span>}
      </label>
      {isNew ? (
        <div className="input-group">
          <input id={id} type="text" value={text} disabled={disabled} placeholder={`New ${label.toLowerCase()}`}
                 onChange={(e) => onText(e.target.value)} />
          {options.length > 0 && (
            <button className="btn" type="button"
                    onClick={() => { setCreating(false); onText(""); }}>
              Pick existing
            </button>
          )}
        </div>
      ) : (
        <div className="input-group">
          <select id={id} value={selectedId ?? ""} disabled={disabled}
                  onChange={(e) => {
                    const opt = options.find((o) => String(o.id) === e.target.value);
                    onSelect(opt?.id ?? null, opt?.name ?? "");
                  }}>
            <option value="">Select…</option>
            {options.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
          </select>
          <button className="btn" type="button" disabled={disabled}
                  onClick={() => { setCreating(true); onSelect(null, ""); }}>
            ＋ New
          </button>
        </div>
      )}
    </div>
  );
}


/* ---------------- Step 3: factory position ---------------- */

function StepFactoryPosition({ draft, set }: {
  draft: CameraDraft;
  set: <K extends keyof CameraDraft>(k: K, v: CameraDraft[K]) => void;
}) {
  const [systems, setSystems] = useState<CoordinateSystem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.listCoordinateSystems()
      .then((list) => {
        setSystems(list);
        if (list.length && draft.coordinate_system_id === null) {
          set("coordinate_system_id", list[0].id);
        }
      })
      .catch(() => setSystems([]))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (loading) return <Spinner label="Loading coordinate systems…" />;

  return (
    <>
      <div className="section-title">Position in the factory coordinate system</div>
      <p className="hint" style={{ marginTop: -4, marginBottom: 14 }}>
        This is the metric frame — X and Y on the floor, Z up, in metres — that downstream services
        use. It is separate from the floor-plan marker in the next step, and entirely optional now.
      </p>

      {systems.length === 0 ? (
        <Notice tone="info" title="No coordinate system exists yet">
          You can finish registering this camera without one. Create a frame on the Factory map
          page afterwards, then position the camera from its Position &amp; Calibration tab.
        </Notice>
      ) : (
        <>
          <div className="field">
            <label htmlFor="fp-cs">Coordinate system</label>
            <select id="fp-cs" value={draft.coordinate_system_id ?? ""}
                    onChange={(e) => set("coordinate_system_id", Number(e.target.value))}>
              {systems.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </div>

          <div className="radio-row" role="radiogroup" aria-label="Positioning method"
               style={{ flexDirection: "column" }}>
            <label className={`radio-card${draft.positioning_method === "skip" ? " is-selected" : ""}`}>
              <input type="radio" name="positioning" checked={draft.positioning_method === "skip"}
                     onChange={() => set("positioning_method", "skip")} />
              <span>
                <strong>Not yet</strong>
                <span>Register the camera now and position it later. It will show as Unconfigured.</span>
              </span>
            </label>
            <label className={`radio-card${draft.positioning_method === "approximate" ? " is-selected" : ""}`}>
              <input type="radio" name="positioning" checked={draft.positioning_method === "approximate"}
                     onChange={() => set("positioning_method", "approximate")} />
              <span>
                <strong>Approximate placement</strong>
                <span>
                  Type where the camera is and roughly where it points. Saved as
                  <em> Approximate</em> — good for getting it on the map, not for measuring.
                </span>
              </span>
            </label>
            <label className={`radio-card${draft.positioning_method === "calibrate_later" ? " is-selected" : ""}`}>
              <input type="radio" name="positioning" checked={draft.positioning_method === "calibrate_later"}
                     onChange={() => set("positioning_method", "calibrate_later")} />
              <span>
                <strong>Calibrate properly after saving</strong>
                <span>
                  Go straight to the calibration workflow once the camera exists, where you mark
                  measured reference points and solve a real pose or floor mapping.
                </span>
              </span>
            </label>
          </div>

          {draft.positioning_method === "approximate" && (
            <>
              <div className="section-title">Approximate pose</div>
              <div className="field-row">
                {([["world_x", "X (m)"], ["world_y", "Y (m)"], ["world_z", "Height Z (m)"]] as const)
                  .map(([key, label]) => (
                    <div className="field" key={key}>
                      <label htmlFor={`fp-${key}`}>{label}</label>
                      <input id={`fp-${key}`} type="number" step="0.1" value={draft[key]}
                             onChange={(e) => set(key, e.target.value)} />
                    </div>
                  ))}
              </div>
              <div className="field-row">
                {([["world_roll", "Roll (°)"], ["world_pitch", "Pitch (°)"], ["world_yaw", "Yaw (°)"]] as const)
                  .map(([key, label]) => (
                    <div className="field" key={key}>
                      <label htmlFor={`fp-${key}`}>{label}</label>
                      <input id={`fp-${key}`} type="number" step="1" value={draft[key]}
                             onChange={(e) => set(key, e.target.value)} />
                    </div>
                  ))}
              </div>
              <Notice tone="info" title="What zero means here">
                Rotation is R = Rz(yaw)·Ry(pitch)·Rx(roll) applied to the optical frame
                (X right, Y down, Z forward). All zeros points the camera straight up at the
                ceiling. A wall-mounted camera looking level along world +Y is roll −90°, pitch 0°,
                yaw 0°. Yaw turns counter-clockwise from above; map heading runs clockwise.
              </Notice>
              <div className="field-row">
                <div className="field">
                  <label htmlFor="fp-hfov">Approximate horizontal FOV (°)</label>
                  <input id="fp-hfov" type="number" step="1" value={draft.world_hfov}
                         onChange={(e) => set("world_hfov", e.target.value)} />
                  <span className="hint">Nominal figure for drawing. Not measured intrinsics.</span>
                </div>
                <div className="field">
                  <label htmlFor="fp-range">Approximate viewing distance (m)</label>
                  <input id="fp-range" type="number" step="1" value={draft.world_range}
                         onChange={(e) => set("world_range", e.target.value)} />
                </div>
              </div>
            </>
          )}
        </>
      )}
    </>
  );
}

/* ---------------- Step 4: floor-plan marker ---------------- */


function StepPlacement({ draft, set }: {
  draft: CameraDraft;
  set: <K extends keyof CameraDraft>(k: K, v: CameraDraft[K]) => void;
}) {
  const [sites, setSites] = useState<Site[]>([]);
  const [plan, setPlan] = useState<FloorPlan | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { api.tree().then(setSites).catch(() => setSites([])); }, []);

  /** Only an existing, saved floor can already have a plan. */
  const floorId = draft.floor_id;

  const loadPlan = useCallback(async () => {
    if (!floorId) { setPlan(null); return; }
    setLoading(true);
    try {
      const plans = await api.listFloorPlans(floorId);
      setPlan(plans[0] ?? null);
    } catch {
      setPlan(null);
    } finally {
      setLoading(false);
    }
  }, [floorId]);

  useEffect(() => { loadPlan(); }, [loadPlan, sites.length]);

  const upload = async (file: File) => {
    if (!floorId) return;
    setError(null); setLoading(true);
    try {
      const uploaded = await api.uploadFloorPlan(floorId, file);
      setPlan(uploaded);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The floor plan could not be uploaded.");
    } finally {
      setLoading(false);
    }
  };

  const placement = draft.placement;
  const updatePlacement = (patch: Partial<NonNullable<CameraDraft["placement"]>>) => {
    if (!plan) return;
    set("placement", {
      floor_plan_id: plan.id,
      norm_x: placement?.norm_x ?? 0.5,
      norm_y: placement?.norm_y ?? 0.5,
      heading_deg: placement?.heading_deg ?? 0,
      fov_deg: placement?.fov_deg ?? 90,
      view_distance_m: placement?.view_distance_m ?? null,
      ...patch,
    });
  };

  if (!floorId) {
    return (
      <Notice tone="info" title="This floor has not been saved yet">
        You chose a new floor name in the previous step, so there is no floor plan to place the
        camera on yet. Save the camera now, then upload a plan from the Floor plans page and place
        the camera there. Placement is always optional.
      </Notice>
    );
  }

  return (
    <>
      <div className="section-title">Floor plan</div>
      {error && <Notice tone="danger" title="Upload failed">{error}</Notice>}

      {!plan ? (
        <>
          <Notice tone="info" title="No floor plan for this floor yet">
            Upload a PNG or JPEG plan to position this camera, or skip this step and place the
            camera later — a floor plan is optional.
          </Notice>
          <div className="field">
            <label htmlFor="plan-upload">Upload floor plan (PNG or JPEG)</label>
            <input id="plan-upload" type="file" accept="image/png,image/jpeg"
                   onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); }} />
          </div>
          {loading && <Spinner label="Uploading…" />}
        </>
      ) : (
        <>
          <div className="row" style={{ marginBottom: 12 }}>
            <span className="small muted">
              {plan.original_filename} · {plan.width_px}×{plan.height_px} px
              {plan.scale_px_per_metre
                ? ` · scale set (${plan.scale_px_per_metre.toFixed(1)} px/m)`
                : " · no map scale set"}
            </span>
            <div className="grow" />
            {!placement && <span className="badge badge-warn"><span aria-hidden="true">!</span>Click the plan to place</span>}
          </div>

          <div className="editor-layout">
            <FloorPlanCanvas
              plan={plan}
              height={480}
              markers={placement ? [{
                id: -1,
                label: draft.name || "New camera",
                norm_x: placement.norm_x,
                norm_y: placement.norm_y,
                heading_deg: placement.heading_deg,
                fov_deg: placement.fov_deg,
                view_distance_m: placement.view_distance_m,
              }] : []}
              selectedId={placement ? -1 : null}
              placingMode={!placement}
              onPlaceAt={(x, y) => updatePlacement({ norm_x: x, norm_y: y })}
              onMove={(_, x, y) => updatePlacement({ norm_x: x, norm_y: y })}
              onRotate={(_, h) => updatePlacement({ heading_deg: h })}
            />

            <div className="side-panel">
              <div className="card card-pad">
                <h3 style={{ marginBottom: 10 }}>Position & direction</h3>
                {!placement ? (
                  <p className="small muted">Click anywhere on the plan to drop the camera marker.</p>
                ) : (
                  <>
                    <div className="field">
                      <span className="field-label">Viewing direction (heading)</span>
                      <div className="slider-row">
                        <input type="range" min={0} max={359} value={Math.round(placement.heading_deg)}
                               aria-label="Viewing direction in degrees"
                               onChange={(e) => updatePlacement({ heading_deg: Number(e.target.value) })} />
                        <span className="slider-val">{Math.round(placement.heading_deg)}°</span>
                      </div>
                      <span className="hint">0° points to the top of the plan and increases clockwise.
                        You can also drag the handle on the marker.</span>
                    </div>

                    <div className="field">
                      <span className="field-label">Horizontal field of view</span>
                      <div className="slider-row">
                        <input type="range" min={10} max={360} step={5} value={placement.fov_deg ?? 90}
                               aria-label="Horizontal field of view in degrees"
                               onChange={(e) => updatePlacement({ fov_deg: Number(e.target.value) })} />
                        <span className="slider-val">{placement.fov_deg ?? 90}°</span>
                      </div>
                      <span className="hint">Optional.</span>
                    </div>

                    <div className="field">
                      <label htmlFor="view-dist">Estimated viewing distance (m)</label>
                      <input id="view-dist" type="number" min={0} step={0.5}
                             value={placement.view_distance_m ?? ""}
                             onChange={(e) => updatePlacement({
                               view_distance_m: e.target.value ? Number(e.target.value) : null,
                             })} />
                      <span className="hint">
                        {plan.scale_px_per_metre
                          ? "Drawn to scale on the plan."
                          : "No map scale is set, so the sector length on the plan is indicative only."}
                      </span>
                    </div>

                    <button className="btn btn-sm btn-danger btn-block" type="button"
                            onClick={() => set("placement", null)}>
                      Remove marker
                    </button>
                  </>
                )}
              </div>

              <Notice tone="warn" title="Approximate viewing area">
                The shaded sector shows roughly where this camera points. It is not calibrated
                coverage and does not measure the floor area the camera can actually see.
              </Notice>

              <div className="card card-pad">
                <h3 style={{ marginBottom: 8 }}>Replace plan</h3>
                <input type="file" accept="image/png,image/jpeg"
                       onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); }} />
                <p className="hint" style={{ marginTop: 8 }}>
                  Replacing the image flags every existing placement on this floor for review.
                </p>
              </div>
            </div>
          </div>
        </>
      )}
    </>
  );
}

/* ---------------- Step 4: review ---------------- */

function StepReview({ draft, test, onEditStep, error }: {
  draft: CameraDraft; test: TestResult | null;
  onEditStep: (step: number) => void; error: string | null;
}) {
  return (
    <>
      {error && <Notice tone="danger" title="Save failed">{error}</Notice>}

      <div className="row" style={{ marginBottom: 4 }}>
        <div className="section-title grow" style={{ margin: 0, border: 0 }}>Connection</div>
        <button className="btn btn-sm btn-ghost" type="button" onClick={() => onEditStep(0)}>Edit</button>
      </div>
      <dl className="kv" style={{ marginBottom: 18 }}>
        <dt>Name</dt><dd>{draft.name || <span className="faint">Not set</span>}</dd>
        <dt>Address</dt><dd className="mono">{draft.host}</dd>
        <dt>Method</dt>
        <dd>
          {draft.connection_type === "onvif"
            ? `ONVIF · port ${draft.onvif_port} · ${draft.onvif_path}`
            : `Manual RTSP · port ${draft.rtsp_port} · ${draft.stream_path || "no path set"}`}
        </dd>
        <dt>Username</dt><dd>{draft.username || <span className="faint">None</span>}</dd>
        <dt>Password</dt>
        <dd>{draft.password ? "•••••••• (encrypted on save, never displayed again)" : <span className="faint">None set</span>}</dd>
        <dt>Stream profile</dt>
        <dd>
          {draft.selected_profile_name
            ? `${draft.selected_profile_name}${draft.profile_resolution ? ` — ${draft.profile_resolution}` : ""}${draft.profile_encoding ? ` ${draft.profile_encoding}` : ""}`
            : <span className="faint">Not selected</span>}
        </dd>
        <dt>Connection test</dt>
        <dd>
          {test ? <StatusBadge status={test.status} /> : <span className="badge badge-neutral"><span aria-hidden="true">○</span>Not tested</span>}
          {test && <div className="small muted" style={{ marginTop: 4 }}>{test.summary}</div>}
        </dd>
        <dt>Preview</dt>
        <dd>
          {test?.stream_ok ? "Live video verified" : test?.snapshot_supported ? "Snapshot available" : "Not verified"}
        </dd>
      </dl>

      <div className="row" style={{ marginBottom: 4 }}>
        <div className="section-title grow" style={{ margin: 0, border: 0 }}>Location</div>
        <button className="btn btn-sm btn-ghost" type="button" onClick={() => onEditStep(1)}>Edit</button>
      </div>
      <dl className="kv" style={{ marginBottom: 18 }}>
        <dt>Site</dt><dd>{draft.site || <span className="faint">Not set</span>}</dd>
        <dt>Building</dt><dd>{draft.building || <span className="faint">Not set</span>}</dd>
        <dt>Floor</dt><dd>{draft.floor || <span className="faint">Not set</span>}</dd>
        <dt>Area / zone</dt><dd>{draft.area || <span className="faint">Not set</span>}</dd>
        <dt>Installation</dt>
        <dd>{draft.installation_description || <span className="faint">Not described</span>}</dd>
        <dt>Mounting height</dt>
        <dd>{draft.mounting_height_m ? `${draft.mounting_height_m} m` : <span className="faint">Not set</span>}</dd>
        <dt>Photo</dt><dd>{draft.photo ? draft.photo.name : <span className="faint">None</span>}</dd>
      </dl>

      <div className="row" style={{ marginBottom: 4 }}>
        <div className="section-title grow" style={{ margin: 0, border: 0 }}>Factory position</div>
        <button className="btn btn-sm btn-ghost" type="button" onClick={() => onEditStep(2)}>Edit</button>
      </div>
      {draft.positioning_method === "approximate" && draft.coordinate_system_id ? (
        <dl className="kv" style={{ marginBottom: 18 }}>
          <dt>Position</dt>
          <dd className="mono">{draft.world_x}, {draft.world_y}, {draft.world_z} m</dd>
          <dt>Orientation</dt>
          <dd className="mono">
            roll {draft.world_roll}°, pitch {draft.world_pitch}°, yaw {draft.world_yaw}°
          </dd>
          <dt>Status it will get</dt>
          <dd>
            <span className="badge badge-warn"><span aria-hidden="true">≈</span>Approximate</span>
            <div className="small muted" style={{ marginTop: 4 }}>
              Hand-entered, so it will not be treated as measured geometry.
            </div>
          </dd>
        </dl>
      ) : (
        <div style={{ marginBottom: 18 }}>
          <Notice tone="info" title={draft.positioning_method === "calibrate_later"
            ? "Calibration to follow"
            : "No factory position"}>
            {draft.positioning_method === "calibrate_later"
              ? "The camera will be saved as Unconfigured; open its Position & Calibration tab next to mark reference points and solve."
              : "This camera will show as Unconfigured in the factory map until it is positioned."}
          </Notice>
        </div>
      )}

      <div className="row" style={{ marginBottom: 4 }}>
        <div className="section-title grow" style={{ margin: 0, border: 0 }}>Floor-plan marker</div>
        <button className="btn btn-sm btn-ghost" type="button" onClick={() => onEditStep(3)}>Edit</button>
      </div>
      {draft.placement ? (
        <dl className="kv">
          <dt>Position</dt>
          <dd className="mono">
            x {draft.placement.norm_x.toFixed(3)}, y {draft.placement.norm_y.toFixed(3)} (normalized)
          </dd>
          <dt>Heading</dt><dd>{Math.round(draft.placement.heading_deg)}° (0° = top of plan, clockwise)</dd>
          <dt>Field of view</dt>
          <dd>{draft.placement.fov_deg ? `${draft.placement.fov_deg}°` : <span className="faint">Not set</span>}</dd>
          <dt>Viewing distance</dt>
          <dd>{draft.placement.view_distance_m ? `${draft.placement.view_distance_m} m (estimate)` : <span className="faint">Not set</span>}</dd>
        </dl>
      ) : (
        <Notice tone="info" title="No map placement">
          This camera will be listed as awaiting placement. You can place it any time from the
          Floor plans page.
        </Notice>
      )}
    </>
  );
}
