import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import RegionEditor, { type Region, regionIsComplete } from "../components/RegionEditor";
import PreviewPanel from "../components/PreviewPanel";
import { Notice, Spinner, StatusBadge } from "../components/ui";
import { ApiError, api } from "../lib/api";
import type { Camera, FloorPlan, Site, TestResult } from "../lib/types";
import type { CameraFunction, FunctionCatalogueEntry, FunctionKind } from "../lib/sceneTypes";
import type {
  CameraMonitoringState, DiscoveredDevice, RuleSummary,
} from "../lib/monitoringTypes";

/**
 * Guided camera setup: connect, name, choose analytics, optionally put it on
 * the map, then validate and activate.
 *
 * Progress lives on the server, keyed to the camera, so closing the tab or
 * handing the job to someone else loses nothing. Each step writes its own real
 * records as it completes — the draft only holds answers that have nowhere
 * else to live yet.
 *
 * Nothing here simulates success. A connection test that did not run says so; a
 * camera with no reachable stream cannot be activated; and analytics are saved
 * as configuration, never described as running, because no detection service
 * ships with this system.
 */

type StepId = "connect" | "assign" | "analytics" | "map" | "activate";

const STEPS: Array<{ id: StepId; title: string; blurb: string }> = [
  { id: "connect", title: "Connect", blurb: "Find the camera and prove we can see its video." },
  { id: "assign", title: "Name and assign", blurb: "What it is called and where it is." },
  { id: "analytics", title: "Analytics", blurb: "What it should watch for, drawn on the picture." },
  { id: "map", title: "Factory map", blurb: "Optional: place what it sees on the floor plan." },
  { id: "activate", title: "Validate and activate", blurb: "Check it over, then switch it on." },
];

export default function CameraSetupWizard() {
  const [params, setParams] = useSearchParams();
  const cameraIdParam = params.get("camera");
  const [cameraId, setCameraId] = useState<number | null>(
    cameraIdParam ? Number(cameraIdParam) : null);
  const [camera, setCamera] = useState<Camera | null>(null);
  const [step, setStep] = useState<StepId>("connect");
  const [completed, setCompleted] = useState<StepId[]>([]);
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [said, setSaid] = useState<string | null>(null);
  const restored = useRef(false);

  /* ---------------- progress, saved on the server ---------------- */

  const loadCamera = useCallback(async (id: number) => {
    setLoading(true);
    try {
      const [cam, progress] = await Promise.all([
        api.getCamera(id),
        api.getSetupProgress(id).catch(() => null),
      ]);
      setCamera(cam);
      if (progress && !restored.current) {
        setStep((progress.step as StepId) || "connect");
        setCompleted((progress.completed as StepId[]) ?? []);
        setDraft(progress.draft ?? {});
        restored.current = true;
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not load that camera.");
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { if (cameraId) loadCamera(cameraId); }, [cameraId, loadCamera]);

  useEffect(() => {
    setParams((p) => {
      if (cameraId) p.set("camera", String(cameraId)); else p.delete("camera");
      return p;
    }, { replace: true });
  }, [cameraId, setParams]);

  const persist = useCallback(async (next: {
    step?: StepId; completed?: StepId[]; draft?: Record<string, unknown>;
  }) => {
    if (!cameraId) return;
    try {
      await api.saveSetupProgress(cameraId, {
        step: next.step ?? step,
        completed: next.completed ?? completed,
        draft: next.draft ?? draft,
      });
    } catch { /* progress is a convenience; never block the installer on it */ }
  }, [cameraId, step, completed, draft]);

  const goTo = async (id: StepId, markDone?: StepId) => {
    const nextCompleted = markDone && !completed.includes(markDone)
      ? [...completed, markDone] : completed;
    setCompleted(nextCompleted);
    setStep(id);
    setSaid(null);
    await persist({ step: id, completed: nextCompleted });
  };

  const updateDraft = (patch: Record<string, unknown>) => {
    const next = { ...draft, ...patch };
    setDraft(next);
    void persist({ draft: next });
  };

  const index = STEPS.findIndex((s) => s.id === step);
  const current = STEPS[index];

  return (
    <main className="page">
      <div className="page-head">
        <div className="grow">
          <h1>Camera setup</h1>
          <p className="sub">
            {camera ? camera.name : "Connect a camera, then say what it should watch for."}
          </p>
        </div>
        {camera && <StatusBadge status={camera.last_test_status} />}
      </div>

      {error && <Notice tone="danger" title="Something went wrong">{error}</Notice>}
      {said && <Notice tone="ok" title={said} />}

      <ol className="wizard-steps">
        {STEPS.map((s, i) => {
          const done = completed.includes(s.id);
          const reachable = !!cameraId || s.id === "connect";
          return (
            <li key={s.id}
                className={`wizard-step${s.id === step ? " is-current" : ""}${done ? " is-done" : ""}`}>
              <button type="button" disabled={!reachable}
                      onClick={() => reachable && goTo(s.id)}>
                <span className="wizard-num">{done ? "✓" : i + 1}</span>
                <span>
                  <strong>{s.title}</strong>
                  <span className="small faint" style={{ display: "block" }}>{s.blurb}</span>
                </span>
              </button>
            </li>
          );
        })}
      </ol>

      {loading && !camera ? <Spinner label="Loading…" /> : (
        <div className="card card-pad wizard-body">
          <h2 className="wizard-title">{current.title}</h2>

          {step === "connect" && (
            <StepConnect
              camera={camera}
              onConnected={async (cam) => {
                setCamera(cam);
                setCameraId(cam.id);
                restored.current = true;
                await api.saveSetupProgress(cam.id, {
                  step: "assign", completed: ["connect"], draft,
                }).catch(() => undefined);
                setCompleted(["connect"]);
                setStep("assign");
              }}
              onError={setError}
            />
          )}

          {step === "assign" && camera && (
            <StepAssign camera={camera} draft={draft} onDraft={updateDraft}
                        onSaved={async (cam) => { setCamera(cam); await goTo("analytics", "assign"); }}
                        onError={setError} />
          )}

          {step === "analytics" && camera && (
            <StepAnalytics camera={camera}
                           onDone={async () => { await goTo("map", "analytics"); }}
                           onSay={setSaid} onError={setError} />
          )}

          {step === "map" && camera && (
            <StepMap camera={camera} draft={draft} onDraft={updateDraft}
                     onDone={async () => { await goTo("activate", "map"); }} />
          )}

          {step === "activate" && camera && (
            <StepActivate camera={camera} onSay={setSaid} onError={setError}
                          onActivated={async () => { await goTo("activate", "activate"); }} />
          )}

          <div className="wizard-nav">
            <button className="btn" type="button" disabled={index === 0}
                    onClick={() => goTo(STEPS[Math.max(0, index - 1)].id)}>Back</button>
            <div className="grow" />
            {step !== "activate" && (
              <button className="btn" type="button" disabled={!cameraId}
                      onClick={() => goTo(STEPS[Math.min(STEPS.length - 1, index + 1)].id)}>
                Skip for now
              </button>
            )}
          </div>
        </div>
      )}
    </main>
  );
}

/* ================= Step A — connect ================= */

function StepConnect({ camera, onConnected, onError }: {
  camera: Camera | null;
  onConnected: (camera: Camera) => void;
  onError: (m: string) => void;
}) {
  const [method, setMethod] = useState<"discover" | "manual">("discover");
  const [devices, setDevices] = useState<DiscoveredDevice[] | null>(null);
  const [discovering, setDiscovering] = useState(false);
  const [discoveryNote, setDiscoveryNote] = useState<string | null>(null);
  const [discoveryFailed, setDiscoveryFailed] = useState<string | null>(null);

  const [form, setForm] = useState({
    name: "", host: "", connection_type: "onvif" as "onvif" | "rtsp",
    onvif_port: "80", rtsp_port: "554", stream_path: "",
    username: "", password: "",
  });
  const [result, setResult] = useState<TestResult | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);

  const set = (k: keyof typeof form, v: string) => setForm((f) => ({ ...f, [k]: v }));

  const runDiscovery = async () => {
    setDiscovering(true); setDiscoveryFailed(null);
    try {
      const body = await api.discoverOnvif(4);
      setDevices(body.devices);
      setDiscoveryNote(body.note);
    } catch (e) {
      setDiscoveryFailed(e instanceof ApiError
        ? e.message : "Discovery could not run on this host.");
      setDevices([]);
    } finally { setDiscovering(false); }
  };

  const test = async () => {
    setTesting(true); setResult(null);
    try {
      setResult(await api.testUnsaved({
        host: form.host,
        connection_type: form.connection_type,
        onvif_port: Number(form.onvif_port) || undefined,
        rtsp_port: Number(form.rtsp_port) || undefined,
        stream_path: form.stream_path || undefined,
        username: form.username || undefined,
        password: form.password || undefined,
      }));
    } catch (e) {
      onError(e instanceof ApiError ? e.message : "The connection test could not run.");
    } finally { setTesting(false); }
  };

  const save = async () => {
    setSaving(true);
    try {
      const created = await api.createCamera({
        name: form.name.trim() || form.host,
        host: form.host,
        connection_type: form.connection_type,
        onvif_port: Number(form.onvif_port) || undefined,
        rtsp_port: Number(form.rtsp_port) || undefined,
        stream_path: form.stream_path || undefined,
        username: form.username || undefined,
        password: form.password || undefined,
        selected_profile_token: result?.selected_profile_token ?? undefined,
      });
      await api.testCamera(created.id).catch(() => undefined);
      onConnected(await api.getCamera(created.id));
    } catch (e) {
      onError(e instanceof ApiError ? e.message : "Could not save the camera.");
      setSaving(false);
    }
  };

  if (camera) {
    return (
      <>
        <Notice tone="ok" title={`${camera.name} is connected`}>
          Registered at {camera.host}. Re-test it any time from its detail page.
        </Notice>
        <PreviewPanel cameraId={camera.id} />
        <p className="hint">Continue to give it a name and a place in the factory.</p>
      </>
    );
  }

  return (
    <>
      <div className="path-cards">
        <button type="button" className={`path-card${method === "discover" ? " is-active" : ""}`}
                onClick={() => setMethod("discover")}>
          <strong>Find cameras on the network</strong>
          <span className="small muted">
            Asks this network which ONVIF cameras are present. Nothing is scanned — devices
            choose to answer.
          </span>
        </button>
        <button type="button" className={`path-card${method === "manual" ? " is-active" : ""}`}
                onClick={() => setMethod("manual")}>
          <strong>Enter an address</strong>
          <span className="small muted">
            An IP address for ONVIF, or a full RTSP stream path. Use this for a camera on
            another VLAN, or an NVR channel.
          </span>
        </button>
      </div>

      {method === "discover" && (
        <div className="section">
          <button className="btn btn-primary" type="button" disabled={discovering}
                  onClick={runDiscovery}>
            {discovering ? "Searching for about 4 seconds…" : "Search the network"}
          </button>

          {discoveryFailed && (
            <Notice tone="warn" title="Discovery could not run">
              {discoveryFailed} Enter the address by hand instead.
            </Notice>
          )}

          {devices && devices.length === 0 && !discoveryFailed && (
            <Notice tone="info" title="No cameras answered">
              {discoveryNote}
            </Notice>
          )}

          {devices && devices.length > 0 && (
            <>
              <p className="small muted">{discoveryNote}</p>
              <div className="stack" style={{ gap: 6 }}>
                {devices.map((device) => (
                  <div key={device.address} className="tray-card" style={{ cursor: "default" }}>
                    <div className="tray-meta">
                      <strong>{device.name ?? device.address}</strong>
                      <div className="faint" style={{ fontSize: 11 }}>
                        {device.address}{device.hardware ? ` · ${device.hardware}` : ""}
                      </div>
                    </div>
                    {device.already_registered ? (
                      <Link className="btn btn-sm"
                            to={`/setup-camera?camera=${device.registered_camera_id}`}>
                        Already registered
                      </Link>
                    ) : (
                      <button className="btn btn-sm btn-primary" type="button"
                              onClick={() => {
                                setMethod("manual");
                                setForm((f) => ({
                                  ...f, host: device.address,
                                  name: device.name ?? device.address,
                                  connection_type: "onvif",
                                }));
                              }}>
                        Use this
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      )}

      {method === "manual" && (
        <div className="section">
          <div className="field-row">
            <div className="field">
              <label htmlFor="c-name">Name</label>
              <input id="c-name" value={form.name} onChange={(e) => set("name", e.target.value)}
                     placeholder="Packing line north" />
            </div>
            <div className="field">
              <label htmlFor="c-host">Address</label>
              <input id="c-host" value={form.host} onChange={(e) => set("host", e.target.value)}
                     placeholder="10.112.22.9" />
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <label htmlFor="c-type">How it connects</label>
              <select id="c-type" value={form.connection_type}
                      onChange={(e) => set("connection_type", e.target.value)}>
                <option value="onvif">ONVIF (finds its own stream)</option>
                <option value="rtsp">RTSP path (including an NVR channel)</option>
              </select>
            </div>
            {form.connection_type === "onvif" ? (
              <div className="field">
                <label htmlFor="c-oport">ONVIF port</label>
                <input id="c-oport" value={form.onvif_port}
                       onChange={(e) => set("onvif_port", e.target.value)} />
              </div>
            ) : (
              <div className="field">
                <label htmlFor="c-rport">RTSP port</label>
                <input id="c-rport" value={form.rtsp_port}
                       onChange={(e) => set("rtsp_port", e.target.value)} />
              </div>
            )}
          </div>

          {form.connection_type === "rtsp" && (
            <div className="field">
              <label htmlFor="c-path">Stream path</label>
              <input id="c-path" value={form.stream_path}
                     onChange={(e) => set("stream_path", e.target.value)}
                     placeholder="/Streaming/Channels/101" />
              <span className="hint">
                On an NVR this selects the channel. The path only — the address goes above.
              </span>
            </div>
          )}

          <div className="field-row">
            <div className="field">
              <label htmlFor="c-user">Username</label>
              <input id="c-user" value={form.username} autoComplete="off"
                     onChange={(e) => set("username", e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="c-pass">Password</label>
              <input id="c-pass" type="password" value={form.password} autoComplete="new-password"
                     onChange={(e) => set("password", e.target.value)} />
              <span className="hint">
                Encrypted before it is stored, and never sent back to this browser.
              </span>
            </div>
          </div>

          <div className="row" style={{ gap: 6 }}>
            <button className="btn" type="button" disabled={!form.host || testing} onClick={test}>
              {testing ? "Testing…" : "Test connection"}
            </button>
            <button className="btn btn-primary" type="button"
                    disabled={!form.host || saving || !result?.ok} onClick={save}>
              {saving ? "Saving…" : "Save and continue"}
            </button>
          </div>

          {result && <ConnectionResult result={result} />}
        </div>
      )}
    </>
  );
}

function ConnectionResult({ result }: { result: TestResult }) {
  if (result.ok) {
    return (
      <Notice tone="ok" title="Video confirmed">
        <p style={{ margin: "4px 0" }}>{result.summary}</p>
        {result.profiles.length > 0 && (
          <ul className="plain-list small">
            {result.profiles.map((p) => (
              <li key={p.token}>
                {p.name} — {p.resolution ?? "resolution unknown"}
                {p.encoding ? `, ${p.encoding}` : ""}{p.fps ? `, ${p.fps} fps` : ""}
              </li>
            ))}
          </ul>
        )}
      </Notice>
    );
  }
  const title = !result.login_ok && result.status === "auth_failed" ? "The login was refused"
    : result.status === "unreachable" ? "The camera did not answer"
    : result.status === "timeout" ? "The camera took too long to answer"
    : result.login_ok && !result.stream_ok ? "Logged in, but the video did not decode"
    : "The connection test failed";
  const advice = result.status === "auth_failed"
      ? "Check the username and password on the camera itself. Many cameras lock out after "
        + "repeated failures."
    : result.status === "unreachable"
      ? "Check the address, and that this machine can route to that network."
    : result.login_ok && !result.stream_ok
      ? "The credentials work. The stream path or profile is probably wrong, or the encoding "
        + "is not one this system can decode."
      : "See the message above.";
  return (
    <Notice tone="danger" title={title}>
      <p style={{ margin: "4px 0" }}>{result.error ?? result.summary}</p>
      <p className="small" style={{ margin: 0 }}>{advice}</p>
    </Notice>
  );
}

/* ================= Step B — name and assign ================= */

function StepAssign({ camera, draft, onDraft, onSaved, onError }: {
  camera: Camera;
  draft: Record<string, unknown>;
  onDraft: (patch: Record<string, unknown>) => void;
  onSaved: (camera: Camera) => void;
  onError: (m: string) => void;
}) {
  const [sites, setSites] = useState<Site[]>([]);
  const [name, setName] = useState(camera.name);
  const [areaId, setAreaId] = useState<string>(
    camera.location.area_id ? String(camera.location.area_id) : "");
  const [context, setContext] = useState<string>(
    (draft.context as string) ?? camera.installation_description ?? "");
  const [busy, setBusy] = useState(false);

  useEffect(() => { api.tree().then(setSites).catch(() => undefined); }, []);

  const areas = useMemo(() => sites.flatMap((s) =>
    s.buildings.flatMap((b) => b.floors.flatMap((f) =>
      f.areas.map((a) => ({
        id: a.id, label: `${b.name} · ${f.name} · ${a.name}`,
      }))))), [sites]);

  const save = async () => {
    setBusy(true);
    try {
      const updated = await api.updateCamera(camera.id, {
        name: name.trim() || camera.name,
        area_id: areaId ? Number(areaId) : null,
        installation_description: context || null,
      });
      onSaved(updated);
    } catch (e) {
      onError(e instanceof ApiError ? e.message : "Could not save.");
      setBusy(false);
    }
  };

  return (
    <>
      <div className="field">
        <label htmlFor="a-name">What do people call this camera?</label>
        <input id="a-name" value={name} onChange={(e) => setName(e.target.value)} />
        <span className="hint">
          A name someone standing on the floor would recognise, not a model number.
        </span>
      </div>

      <div className="field">
        <label htmlFor="a-area">Where is it?</label>
        <select id="a-area" value={areaId} onChange={(e) => setAreaId(e.target.value)}>
          <option value="">Not assigned yet</option>
          {areas.map((a) => <option key={a.id} value={a.id}>{a.label}</option>)}
        </select>
        <span className="hint">
          Building, floor and area. Events are grouped by this, and a camera cannot be
          activated without it. <Link to="/">Add an area</Link> if the right one is missing.
        </span>
      </div>

      <div className="field">
        <label htmlFor="a-ctx">What is it watching? (optional)</label>
        <input id="a-ctx" value={context}
               onChange={(e) => { setContext(e.target.value); onDraft({ context: e.target.value }); }}
               placeholder="Workstation 4, the palletiser, or loading bay 2" />
      </div>

      <button className="btn btn-primary" type="button" disabled={busy} onClick={save}>
        {busy ? "Saving…" : "Save and continue"}
      </button>
    </>
  );
}

/* ================= Step C — analytics and regions ================= */

const SHAPE_FOR: Partial<Record<FunctionKind, "polygon" | "line">> = {
  restricted_zone: "polygon",
  ppe_monitoring: "polygon",
  workstation_occupancy: "polygon",
  people_counting: "line",
  product_counting: "line",
};

function StepAnalytics({ camera, onDone, onSay, onError }: {
  camera: Camera;
  onDone: () => void;
  onSay: (m: string) => void;
  onError: (m: string) => void;
}) {
  const [catalogue, setCatalogue] = useState<FunctionCatalogueEntry[]>([]);
  const [functions, setFunctions] = useState<CameraFunction[]>([]);
  const [rules, setRules] = useState<RuleSummary[]>([]);
  const [adding, setAdding] = useState(false);

  const refresh = useCallback(async () => {
    const [fs, rs] = await Promise.all([
      api.listFunctions(camera.id),
      api.ruleSummaries(camera.id).then((r) => r.rules).catch(() => []),
    ]);
    setFunctions(fs);
    setRules(rs);
  }, [camera.id]);

  useEffect(() => {
    api.functionCatalogue().then((c) => setCatalogue(c.functions)).catch(() => undefined);
    refresh();
  }, [refresh]);

  return (
    <>
      <Notice tone="warn" title="No detection service is connected">
        Settings are stored and exported so a processing service can pick them up. Nothing in
        this deployment is analysing video, and the interface will not pretend otherwise.
      </Notice>

      {functions.length === 0 ? (
        <p className="small muted">
          Nothing configured yet. Counting and zone watching work on the picture and need no
          calibration at all.
        </p>
      ) : (
        <div className="stack" style={{ gap: 8, marginBottom: 12 }}>
          {functions.map((f) => {
            const rule = rules.find((r) => r.function_id === f.id);
            return (
              <div key={f.id} className="card card-pad">
                <div className="row" style={{ alignItems: "baseline" }}>
                  <strong className="grow">{f.name}</strong>
                  <span className="badge badge-neutral">{f.label}</span>
                  <button className="btn btn-sm btn-ghost" type="button"
                          onClick={async () => {
                            await api.deleteFunction(f.id);
                            await refresh();
                          }}>Remove</button>
                </div>
                {rule && (
                  <p className="rule-summary">{rule.summary}</p>
                )}
                {rule && !rule.geometry_valid && (
                  <Notice tone="warn" title="This region is not usable yet">
                    {rule.geometry_problem}
                  </Notice>
                )}
              </div>
            );
          })}
        </div>
      )}

      {adding ? (
        <AnalyticEditor camera={camera} catalogue={catalogue}
                        onCancel={() => setAdding(false)}
                        onSaved={async () => {
                          setAdding(false);
                          await refresh();
                          onSay("Analytic saved.");
                        }}
                        onError={onError} />
      ) : (
        <div className="row" style={{ gap: 6 }}>
          <button className="btn" type="button" onClick={() => setAdding(true)}>
            ＋ Add an analytic
          </button>
          <button className="btn btn-primary" type="button" onClick={onDone}>
            Continue
          </button>
        </div>
      )}
    </>
  );
}

function AnalyticEditor({ camera, catalogue, onCancel, onSaved, onError }: {
  camera: Camera;
  catalogue: FunctionCatalogueEntry[];
  onCancel: () => void;
  onSaved: () => void;
  onError: (m: string) => void;
}) {
  const [kind, setKind] = useState<FunctionCatalogueEntry | null>(null);
  const [name, setName] = useState("");
  const [regions, setRegions] = useState<Region[]>([]);
  const [frame, setFrame] = useState<{ w: number; h: number } | null>(null);
  const [direction, setDirection] = useState("a_to_b");
  const [ppe, setPpe] = useState<string[]>(["hi_vis"]);
  const [workstation, setWorkstation] = useState("");
  const [schedule, setSchedule] = useState("");
  const [threshold, setThreshold] = useState("2");
  const [cooldown, setCooldown] = useState("30");
  const [notify, setNotify] = useState("dashboard");
  const [busy, setBusy] = useState(false);

  const shape = kind ? SHAPE_FOR[kind.kind] : undefined;
  const primary = regions.find((r) => r.kind === shape && regionIsComplete(r)) ?? null;
  const exclusions = regions.filter((r) => r.kind === "exclusion" && regionIsComplete(r));
  const ready = !!kind && (!shape || !!primary);

  const save = async () => {
    if (!kind) return;
    setBusy(true);
    try {
      const w = frame?.w ?? 1920;
      const h = frame?.h ?? 1080;
      const config: Record<string, unknown> = {
        schedule_label: schedule || undefined,
        cooldown_seconds: Number(cooldown) || undefined,
        notify: notify === "none" ? undefined : [notify],
        exclusions: exclusions.map((r) => r.points),
      };
      if (shape === "line") {
        config.line = primary!.points;
        config.direction = direction;
      }
      if (shape === "polygon") {
        config.polygon = primary!.points;
        if (Number(threshold) > 0) config.threshold_seconds = Number(threshold);
      }
      if (kind.kind === "ppe_monitoring") config.required_ppe = ppe;
      if (kind.kind === "workstation_occupancy") {
        config.workstation_name = workstation || name || "Workstation";
      }

      await api.addFunction(camera.id, {
        kind: kind.kind, name: name.trim() || kind.label, space: kind.space,
        image_width: w, image_height: h, config,
      });
      onSaved();
    } catch (e) {
      onError(e instanceof ApiError ? e.message : "Could not save that analytic.");
      setBusy(false);
    }
  };

  return (
    <div className="card card-pad" style={{ background: "var(--surface-sunken)" }}>
      {!kind ? (
        <>
          <strong>What should this camera watch for?</strong>
          <div className="stack" style={{ gap: 8, marginTop: 10 }}>
            {catalogue.map((entry) => (
              <label key={entry.kind} className="radio-card" style={{ minWidth: 0 }}>
                <input type="radio" name="analytic" checked={false}
                       onChange={() => { setKind(entry); setName(entry.label); }} />
                <span>
                  <strong>{entry.label}</strong>
                  <span>{entry.configuration}</span>
                  <span style={{ display: "block", marginTop: 5 }}>
                    <span className="badge badge-neutral">
                      {entry.space === "image" ? "works on the picture" : "needs floor geometry"}
                    </span>
                    {!entry.processing_available && (
                      <span className="badge badge-warn" style={{ marginLeft: 5 }}>
                        no model installed
                      </span>
                    )}
                  </span>
                </span>
              </label>
            ))}
          </div>
          <button className="btn btn-sm" type="button" style={{ marginTop: 10 }}
                  onClick={onCancel}>Cancel</button>
        </>
      ) : (
        <>
          <div className="row chosen-function">
            <span className="grow">
              <strong>{kind.label}</strong>
              <span className="faint" style={{ display: "block", fontSize: 11 }}>
                {kind.configuration}
              </span>
            </span>
            <button className="btn btn-sm" type="button" onClick={() => setKind(null)}>
              Choose a different one
            </button>
          </div>

          <div className="field">
            <label htmlFor="an-name">Name this rule</label>
            <input id="an-name" value={name} onChange={(e) => setName(e.target.value)}
                   placeholder="Conveyor Access" />
          </div>

          {shape && (
            <div className="field">
              <span className="field-label">
                {shape === "line" ? "Draw the counting line" : "Draw the area to watch"}
              </span>
              <RegionEditor cameraId={camera.id} regions={regions} onChange={setRegions}
                            onFrame={(w, h) => setFrame({ w, h })} height={420}
                            initialTool={shape} />
              <span className="hint">
                You can also add exclusion regions — parts of the picture this rule should
                ignore, such as a walkway behind a guard rail.
              </span>
            </div>
          )}

          {shape === "line" && (
            <div className="field">
              <label htmlFor="an-dir">Which way should it count?</label>
              <select id="an-dir" value={direction} onChange={(e) => setDirection(e.target.value)}>
                <option value="a_to_b">A to B</option>
                <option value="b_to_a">B to A</option>
                <option value="both">Both directions</option>
              </select>
              <span className="hint">A and B are marked on the arrow in the picture.</span>
            </div>
          )}

          {shape === "polygon" && kind.kind !== "ppe_monitoring" && (
            <div className="field">
              <label htmlFor="an-thr">How long before it counts? (seconds)</label>
              <input id="an-thr" type="number" min="0" step="0.5" value={threshold}
                     onChange={(e) => setThreshold(e.target.value)} />
              <span className="hint">
                Zero reports the moment someone enters. A second or two avoids reporting people
                walking straight through.
              </span>
            </div>
          )}

          {kind.kind === "ppe_monitoring" && (
            <div className="field">
              <span className="field-label">Required PPE</span>
              <div className="row" style={{ flexWrap: "wrap" }}>
                {["hi_vis", "helmet", "gloves", "eye_protection", "ear_protection"].map((item) => (
                  <label key={item} className="check small">
                    <input type="checkbox" checked={ppe.includes(item)}
                           onChange={(e) => setPpe((p) =>
                             e.target.checked ? [...p, item] : p.filter((x) => x !== item))} />
                    {item.replace("_", " ")}
                  </label>
                ))}
              </div>
            </div>
          )}

          {kind.kind === "workstation_occupancy" && (
            <div className="field">
              <label htmlFor="an-ws">Which workstation?</label>
              <input id="an-ws" value={workstation} placeholder="Assembly bench 2"
                     onChange={(e) => setWorkstation(e.target.value)} />
            </div>
          )}

          <div className="field-row">
            <div className="field">
              <label htmlFor="an-sched">When does it apply?</label>
              <input id="an-sched" value={schedule} placeholder="Shift A, or leave blank for always"
                     onChange={(e) => setSchedule(e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="an-cool">Wait before reporting again (seconds)</label>
              <input id="an-cool" type="number" min="0" value={cooldown}
                     onChange={(e) => setCooldown(e.target.value)} />
            </div>
          </div>

          <div className="field">
            <label htmlFor="an-notify">Where should events go?</label>
            <select id="an-notify" value={notify} onChange={(e) => setNotify(e.target.value)}>
              <option value="dashboard">The incident inbox in this system</option>
              <option value="none">Record only, no notification</option>
            </select>
            <span className="hint">
              Email and messaging destinations need a notification service, which is not
              installed here.
            </span>
          </div>

          <RulePreview kind={kind.kind} name={name} schedule={schedule} threshold={threshold}
                       direction={direction} cooldown={cooldown} ppe={ppe}
                       workstation={workstation} />

          <div className="row" style={{ gap: 6, marginTop: 10 }}>
            <button className="btn btn-primary" type="button" disabled={!ready || busy}
                    onClick={save}>{busy ? "Saving…" : "Save this analytic"}</button>
            <button className="btn" type="button" onClick={onCancel}>Cancel</button>
            {!ready && shape && (
              <span className="small muted">
                {shape === "line" ? "Draw both ends of the line first."
                                  : "Draw an area with at least three corners first."}
              </span>
            )}
          </div>
        </>
      )}
    </div>
  );
}

/** The same sentence the backend will store, shown before saving. */
function RulePreview({ kind, name, schedule, threshold, direction, cooldown, ppe, workstation }: {
  kind: FunctionKind; name: string; schedule: string; threshold: string;
  direction: string; cooldown: string; ppe: string[]; workstation: string;
}) {
  const where = name || "this region";
  const when = schedule ? `During ${schedule}` : "At any time";
  const secs = (v: string) => {
    const n = Number(v);
    if (!n) return "";
    return n >= 60 && n % 60 === 0
      ? `${n / 60} minute${n / 60 !== 1 ? "s" : ""}`
      : `${n} second${n !== 1 ? "s" : ""}`;
  };
  let condition: string;
  if (kind === "restricted_zone") {
    condition = `a person remains inside ${where}`
      + (Number(threshold) ? ` for more than ${secs(threshold)}` : "");
  } else if (kind === "people_counting" || kind === "product_counting") {
    const subject = kind === "people_counting" ? "a person" : "a product";
    const dir = direction === "both" ? "in either direction"
      : direction === "a_to_b" ? "from side A to side B" : "from side B to side A";
    condition = `${subject} crosses ${where} ${dir}`;
  } else if (kind === "workstation_occupancy") {
    condition = `${workstation || where} is occupied`
      + (Number(threshold) ? ` for more than ${secs(threshold)}` : "");
  } else if (kind === "ppe_monitoring") {
    const wearing = ppe.map((p) => p.replace("_", " ")).join(", ") || "the required PPE";
    condition = `a person inside ${where} is not wearing ${wearing}`;
  } else {
    condition = `${where} reports something`;
  }
  return (
    <div className="rule-preview">
      <span className="field-label">In plain words</span>
      <p className="rule-summary">
        {when}, create an event when {condition}.
        {Number(cooldown) ? ` Then wait ${secs(cooldown)} before reporting it again.` : ""}
      </p>
    </div>
  );
}

/* ================= Step D — optional map alignment ================= */

function StepMap({ camera, draft, onDraft, onDone }: {
  camera: Camera;
  draft: Record<string, unknown>;
  onDraft: (patch: Record<string, unknown>) => void;
  onDone: () => void;
}) {
  const [choice, setChoice] = useState<"camera_only" | "map">(
    (draft.map_choice as "camera_only" | "map") ?? "camera_only");
  const [plans, setPlans] = useState<FloorPlan[]>([]);
  const [coverage, setCoverage] = useState<{ area_m2: number | null; mapped: boolean } | null>(null);

  useEffect(() => { api.listFloorPlans().then(setPlans).catch(() => undefined); }, []);

  useEffect(() => {
    if (!camera.coordinate_system_id) { setCoverage(null); return; }
    api.factoryMap(camera.coordinate_system_id)
      .then((map) => {
        const entry = map.cameras.find((c) => c.camera_id === camera.id);
        setCoverage(entry
          ? { area_m2: entry.floor_polygon ? polygonArea(entry.floor_polygon) : null,
              mapped: !!entry.floor_polygon }
          : { area_m2: null, mapped: false });
      })
      .catch(() => setCoverage(null));
  }, [camera.id, camera.coordinate_system_id]);

  const pick = (value: "camera_only" | "map") => {
    setChoice(value);
    onDraft({ map_choice: value });
  };

  return (
    <>
      <p className="small muted">
        This step is optional. Counting, zones and occupancy all work on the camera picture
        alone — skip it unless you need observations placed on a factory map.
      </p>

      <div className="path-cards">
        <button type="button" className={`path-card${choice === "camera_only" ? " is-active" : ""}`}
                onClick={() => pick("camera_only")}>
          <strong>Camera-only analytics</strong>
          <span className="small muted">
            Events say which camera and which region. Nothing is placed on a map. This is
            complete on its own and can be activated straight away.
          </span>
        </button>
        <button type="button" className={`path-card${choice === "map" ? " is-active" : ""}`}
                onClick={() => pick("map")}>
          <strong>Locate observations on the factory map</strong>
          <span className="small muted">
            Turns a position in the picture into a place on the floor. Needs at least four
            well-spread floor points matched between the picture and the map.
          </span>
        </button>
      </div>

      {choice === "map" && (
        <div className="section">
          {plans.length === 0 && (
            <Notice tone="info" title="No floor plan uploaded yet">
              Map alignment needs a plan image and its scale.
              {" "}<Link to="/floor-plans">Upload one</Link>, set the scale from a known
              distance, then come back.
            </Notice>
          )}

          <Notice tone="warn" title="Dropping a camera icon on a map is not calibration">
            An icon shows roughly where the camera hangs. It does not let the system work out
            where anything it sees actually is. Only matched floor points do that, and until
            they exist this camera's calibration status stays approximate.
          </Notice>

          {coverage?.mapped ? (
            <Notice tone="ok" title="This camera is mapped to the floor">
              {coverage.area_m2 != null
                ? `Its measured points cover about ${coverage.area_m2.toFixed(0)} m² of floor.`
                : "A mapping is active for this camera."}
            </Notice>
          ) : (
            <p className="small muted">
              No floor mapping for this camera yet.
            </p>
          )}

          <Link className="btn btn-primary"
                to={`/align?camera=${camera.id}${camera.coordinate_system_id
                  ? `&workspace=${camera.coordinate_system_id}` : ""}`}>
            Open the alignment tool
          </Link>
        </div>
      )}

      <button className="btn btn-primary" type="button" style={{ marginTop: 12 }} onClick={onDone}>
        Continue
      </button>
    </>
  );
}

function polygonArea(poly: number[][]): number {
  let sum = 0;
  for (let i = 0; i < poly.length; i += 1) {
    const [x1, y1] = poly[i];
    const [x2, y2] = poly[(i + 1) % poly.length];
    sum += x1 * y2 - x2 * y1;
  }
  return Math.abs(sum) / 2;
}

/* ================= Step E — validate and activate ================= */

function StepActivate({ camera, onSay, onError, onActivated }: {
  camera: Camera;
  onSay: (m: string) => void;
  onError: (m: string) => void;
  onActivated: () => void;
}) {
  const [state, setState] = useState<CameraMonitoringState | null>(null);
  const [rules, setRules] = useState<RuleSummary[]>([]);
  const [busy, setBusy] = useState(false);
  const [testMode, setTestMode] = useState(false);

  const refresh = useCallback(async () => {
    const [s, r] = await Promise.all([
      api.cameraMonitoring(camera.id),
      api.ruleSummaries(camera.id).then((x) => x.rules).catch(() => []),
    ]);
    setState(s);
    setRules(r);
  }, [camera.id]);

  useEffect(() => { refresh(); }, [refresh]);

  if (!state) return <Spinner label="Checking this camera over…" />;

  return (
    <>
      <div className="row" style={{ gap: 6, marginBottom: 12 }}>
        <span className={`badge ${state.state === "active" ? "badge-ok"
          : state.state === "validation_pending" ? "badge-warn" : "badge-neutral"}`}>
          {state.label}
        </span>
        {state.calibration_needs_revalidation && (
          <span className="badge badge-warn">Calibration needs revalidation</span>
        )}
      </div>

      <div className="section">
        <h4>Summary</h4>
        <dl className="kv small">
          <dt>Camera</dt><dd>{camera.name} — {camera.host}</dd>
          <dt>Connection</dt>
          <dd>{camera.last_test_status === "online"
            ? "Video decoded successfully" : `Last test: ${camera.last_test_status}`}</dd>
          <dt>Area</dt>
          <dd>{camera.location.area ?? <span className="faint">not assigned</span>}</dd>
          <dt>Analytics</dt>
          <dd>{state.analytics_valid} of {state.analytics_configured} usable</dd>
        </dl>
        {rules.length > 0 && (
          <ul className="plain-list small">
            {rules.map((r) => (
              <li key={r.function_id} className="rule-summary">
                {r.summary}
                {!r.geometry_valid && (
                  <span className="badge badge-warn" style={{ marginLeft: 6 }}>
                    {r.geometry_problem}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="section">
        <h4>Test mode</h4>
        <button className="btn btn-sm" type="button" onClick={() => setTestMode((t) => !t)}>
          {testMode ? "Stop test mode" : "Start test mode"}
        </button>
        {testMode && (
          <div style={{ marginTop: 10 }}>
            <PreviewPanel cameraId={camera.id} />
            <Notice tone="warn" title="Nothing is being detected">
              This is the live picture with your regions configured against it. Detections,
              tracks, zone membership and counting direction would be drawn here by a
              processing service. None is connected to this deployment, so the system shows
              you the video and no more — it will not draw boxes it did not compute.
            </Notice>
          </div>
        )}
      </div>

      {state.blockers.length > 0 ? (
        <Notice tone="warn" title="Not ready to activate">
          <ul className="plain-list small" style={{ marginTop: 4 }}>
            {state.blockers.map((b) => <li key={b}>{b}</li>)}
          </ul>
        </Notice>
      ) : (
        <Notice tone="ok" title="Ready to activate">
          Everything this camera needs is in place. Map calibration is optional and is not
          required for camera-only analytics.
        </Notice>
      )}

      <div className="row" style={{ gap: 6, marginTop: 12 }}>
        {state.state === "active" ? (
          <button className="btn" type="button" disabled={busy}
                  onClick={async () => {
                    setBusy(true);
                    try {
                      setState(await api.deactivateMonitoring(camera.id));
                      onSay("Monitoring switched off.");
                    } catch (e) {
                      onError(e instanceof ApiError ? e.message : "Could not switch it off.");
                    } finally { setBusy(false); }
                  }}>
            Switch monitoring off
          </button>
        ) : (
          <button className="btn btn-primary" type="button"
                  disabled={busy || !state.can_activate}
                  onClick={async () => {
                    setBusy(true);
                    try {
                      setState(await api.activateMonitoring(camera.id));
                      onSay("Monitoring is active for this camera.");
                      onActivated();
                    } catch (e) {
                      onError(e instanceof ApiError ? e.message : "Could not activate.");
                      await refresh();
                    } finally { setBusy(false); }
                  }}>
            {busy ? "Activating…" : "Activate monitoring"}
          </button>
        )}
        <Link className="btn" to="/">Back to the overview</Link>
      </div>
    </>
  );
}
