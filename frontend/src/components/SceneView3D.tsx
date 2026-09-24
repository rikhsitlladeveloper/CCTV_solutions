import { Canvas } from "@react-three/fiber";
import { Grid, Line, OrbitControls } from "@react-three/drei";
import { Suspense, useEffect, useMemo, useState } from "react";
import * as THREE from "three";
import type { FactoryScene, SceneObject } from "../lib/sceneTypes";
import type { MapCamera } from "../lib/calibrationTypes";

/**
 * The same scene data as the 2D canvas, drawn in three dimensions. Both views
 * read the identical world metres, so they cannot disagree about where anything
 * is.
 *
 * Three.js is Y-up and the factory frame is Z-up, so the camera is given
 * up = (0, 0, 1) and world (x, y, z) is used directly. A graphics camera also
 * looks down its own -Z with +Y up, while an optical camera looks down +Z with
 * +Y down; frustums below are built from the optical basis vectors explicitly.
 *
 * Labels are sprites carrying a texture painted with the browser's own 2D canvas
 * text. drei's <Text> fetches a font over the network, which an on-premise
 * install cannot rely on and which blanks the canvas while it waits; drei's
 * <Html> mounts a second React root per label, which throws on teardown when
 * the canvas unmounts mid-render (switching to Monitor, or back to 2D). A
 * sprite has neither problem and needs nothing but a system font.
 */

const STATUS_COLOUR: Record<string, string> = {
  validated: "#0f7b4f",
  calibrated_unvalidated: "#1d4ed8",
  approximate: "#9a6207",
  needs_recalibration: "#b0281f",
  unconfigured: "#5a6779",
};

function opticalBasis(roll: number, pitch: number, yaw: number): THREE.Matrix4 {
  const rx = new THREE.Matrix4().makeRotationX(THREE.MathUtils.degToRad(roll));
  const ry = new THREE.Matrix4().makeRotationY(THREE.MathUtils.degToRad(pitch));
  const rz = new THREE.Matrix4().makeRotationZ(THREE.MathUtils.degToRad(yaw));
  return rz.multiply(ry).multiply(rx);       // R = Rz @ Ry @ Rx
}

const LABEL_FONT = '600 48px ui-sans-serif, system-ui, "Segoe UI", Roboto, sans-serif';
const LABEL_WORLD_HEIGHT_M = 0.5;

function Label({ position, colour, text }: {
  position: [number, number, number]; colour: string; text: string;
}) {
  const label = useMemo(() => {
    const canvas = document.createElement("canvas");
    const measure = canvas.getContext("2d");
    if (!measure) return null;
    measure.font = LABEL_FONT;
    const pad = 10;
    const width = Math.ceil(measure.measureText(text).width) + pad * 2;
    const height = 48 + pad * 2;
    canvas.width = Math.max(2, width);
    canvas.height = height;

    const ctx = canvas.getContext("2d");
    if (!ctx) return null;
    ctx.font = LABEL_FONT;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    // A white outline keeps the text readable over a dark machine or a pale floor.
    ctx.lineWidth = 7;
    ctx.lineJoin = "round";
    ctx.strokeStyle = "#ffffff";
    ctx.strokeText(text, canvas.width / 2, canvas.height / 2);
    ctx.fillStyle = colour;
    ctx.fillText(text, canvas.width / 2, canvas.height / 2);

    const texture = new THREE.CanvasTexture(canvas);
    texture.anisotropy = 4;
    return { texture, aspect: canvas.width / canvas.height };
  }, [text, colour]);

  useEffect(() => () => label?.texture.dispose(), [label]);

  if (!label) return null;
  return (
    <sprite position={position}
            scale={[LABEL_WORLD_HEIGHT_M * label.aspect, LABEL_WORLD_HEIGHT_M, 1]}>
      <spriteMaterial map={label.texture} transparent depthTest={false} toneMapped={false} />
    </sprite>
  );
}

function ObjectMesh({ obj, selected, onSelect }: {
  obj: SceneObject; selected: boolean; onSelect: () => void;
}) {
  const estimated = obj.provenance === "estimated";
  const flat = obj.kind === "walkway" || obj.kind === "restricted_area";

  if (obj.points && obj.points.length >= 2 && (obj.kind === "wall" || obj.kind === "conveyor")) {
    // A run of wall or conveyor: extrude each segment as a thin box.
    return (
      <group>
        {obj.points.slice(0, -1).map((p, i) => {
          const q = obj.points![i + 1];
          const dx = q[0] - p[0], dy = q[1] - p[1];
          const length = Math.hypot(dx, dy);
          if (length < 1e-6) return null;
          const angle = Math.atan2(dy, dx);
          return (
            <mesh key={i} onClick={onSelect}
                  position={[p[0] + dx / 2, p[1] + dy / 2, obj.z + obj.height_m / 2]}
                  rotation={[0, 0, angle]}>
              <boxGeometry args={[length, Math.max(obj.depth_m, 0.15), obj.height_m]} />
              <meshStandardMaterial color={obj.colour} transparent opacity={selected ? 0.95 : 0.8}
                                    wireframe={estimated && selected} />
            </mesh>
          );
        })}
      </group>
    );
  }

  if (obj.points && obj.points.length >= 3) {
    // A flat outline: draw its border just above the floor.
    const pts = [...obj.points, obj.points[0]].map(
      ([x, y]) => [x, y, obj.z + 0.02] as [number, number, number]);
    return (
      <group onClick={onSelect}>
        <Line points={pts} color={obj.colour} lineWidth={selected ? 3 : 2}
              dashed={estimated} dashSize={0.4} gapSize={0.25} />
      </group>
    );
  }

  return (
    <mesh onClick={onSelect}
          position={[obj.x, obj.y, obj.z + (flat ? 0.01 : obj.height_m / 2)]}
          rotation={[0, 0, THREE.MathUtils.degToRad(obj.rotation_deg)]}>
      <boxGeometry args={[obj.width_m, obj.depth_m, flat ? 0.02 : obj.height_m]} />
      <meshStandardMaterial color={obj.colour} transparent
                            opacity={selected ? 0.95 : flat ? 0.35 : 0.75} />
    </mesh>
  );
}

function CameraFrustum({ camera, selected, onSelect }: {
  camera: MapCamera; selected: boolean; onSelect: () => void;
}) {
  const geometry = useMemo(() => {
    if (!camera.position || !camera.rpy_deg) return null;
    const R = opticalBasis(camera.rpy_deg.roll, camera.rpy_deg.pitch, camera.rpy_deg.yaw);
    const e = R.elements;
    const right = new THREE.Vector3(e[0], e[1], e[2]);
    const down = new THREE.Vector3(e[4], e[5], e[6]);
    const forward = new THREE.Vector3(e[8], e[9], e[10]);

    const hfov = THREE.MathUtils.degToRad(
      camera.horizontal_fov_deg ?? camera.approx_hfov_deg ?? 70);
    const vfov = THREE.MathUtils.degToRad(camera.vertical_fov_deg ?? 42);
    const depth = Math.min(camera.approx_range_m ?? 10, 18);
    const halfW = Math.tan(hfov / 2) * depth;
    const halfH = Math.tan(vfov / 2) * depth;

    const origin = new THREE.Vector3(camera.position.x, camera.position.y, camera.position.z);
    const centre = origin.clone().add(forward.clone().multiplyScalar(depth));
    const corners = [
      centre.clone().add(right.clone().multiplyScalar(-halfW)).add(down.clone().multiplyScalar(-halfH)),
      centre.clone().add(right.clone().multiplyScalar(+halfW)).add(down.clone().multiplyScalar(-halfH)),
      centre.clone().add(right.clone().multiplyScalar(+halfW)).add(down.clone().multiplyScalar(+halfH)),
      centre.clone().add(right.clone().multiplyScalar(-halfW)).add(down.clone().multiplyScalar(+halfH)),
    ];
    return [
      [origin.toArray(), corners[0].toArray()], [origin.toArray(), corners[1].toArray()],
      [origin.toArray(), corners[2].toArray()], [origin.toArray(), corners[3].toArray()],
      [corners[0].toArray(), corners[1].toArray()], [corners[1].toArray(), corners[2].toArray()],
      [corners[2].toArray(), corners[3].toArray()], [corners[3].toArray(), corners[0].toArray()],
    ] as Array<[number[], number[]]>;
  }, [camera]);

  const colour = STATUS_COLOUR[camera.calibration_status] ?? "#5a6779";

  // A floor-mapped camera has a patch of floor but no mount point, so there is
  // no frustum to draw and nothing to hang in the air. Draw the patch flat and
  // label it there, rather than leaving the camera out of the scene entirely.
  if (!camera.position) {
    const poly = camera.floor_polygon;
    if (!poly || poly.length < 3) return null;
    const cx = poly.reduce((a, [x]) => a + x, 0) / poly.length;
    const cy = poly.reduce((a, [, y]) => a + y, 0) / poly.length;
    return (
      <group onClick={onSelect}>
        <Line points={[...poly, poly[0]].map(([x, y]) => [x, y, 0.03] as [number, number, number])}
              color="#0d9488" lineWidth={selected ? 3 : 1.8} dashed dashSize={0.5} gapSize={0.3} />
        <Label position={[cx, cy, 0.6]} colour="#0f766e"
               text={`${camera.name} · mapped floor only`} />
      </group>
    );
  }

  return (
    <group>
      <mesh position={[camera.position.x, camera.position.y, camera.position.z]} onClick={onSelect}>
        <sphereGeometry args={[selected ? 0.35 : 0.25, 16, 16]} />
        <meshStandardMaterial color={colour} />
      </mesh>
      {geometry?.map(([a, b], i) => (
        <Line key={i} points={[a as [number, number, number], b as [number, number, number]]}
              color={colour} lineWidth={selected ? 2.5 : 1.2}
              dashed={camera.is_approximate} dashSize={0.3} gapSize={0.22} />
      ))}
      {camera.floor_polygon && camera.floor_polygon.length >= 3 && (
        <Line points={[...camera.floor_polygon, camera.floor_polygon[0]]
                .map(([x, y]) => [x, y, 0.03] as [number, number, number])}
              color={colour} lineWidth={1.5} dashed dashSize={0.5} gapSize={0.3} />
      )}
      <Label position={[camera.position.x, camera.position.y, camera.position.z + 0.7]}
             colour={colour} text={camera.name} />
    </group>
  );
}

export default function SceneView3D({
  scene, cameras, selectedObjectId, selectedCameraId, onSelectObject, onSelectCamera,
  height = 640,
}: {
  scene: FactoryScene;
  cameras: MapCamera[];
  selectedObjectId: number | null;
  selectedCameraId: number | null;
  onSelectObject: (id: number | null) => void;
  onSelectCamera: (id: number | null) => void;
  height?: number;
}) {
  const [contextLost, setContextLost] = useState(false);
  const grid = scene.grid;
  const centre: [number, number, number] = [
    (grid.min_x + grid.max_x) / 2, (grid.min_y + grid.max_y) / 2, 0];
  const span = Math.max(grid.max_x - grid.min_x, grid.max_y - grid.min_y);

  return (
    <div className="scene-stage" style={{ height, background: "#f5f7fa" }}>
      {contextLost && (
        <div className="scene-overlay">
          <div>
            <strong>The 3D view lost its graphics context.</strong>
            <p className="small muted" style={{ maxWidth: 360, margin: "8px auto 12px" }}>
              This usually means hardware acceleration is unavailable — common over remote
              desktops. The 2D view shows the same scene and needs no GPU.
            </p>
            <button className="btn btn-sm" type="button" onClick={() => setContextLost(false)}>
              Try again
            </button>
          </div>
        </div>
      )}

      <Canvas
        camera={{ position: [centre[0] + span * 0.7, centre[1] - span * 0.9, span * 0.5],
                  fov: 50, up: [0, 0, 1], near: 0.1, far: span * 12 }}
        onCreated={({ gl, camera }) => {
          camera.up.set(0, 0, 1);
          camera.lookAt(centre[0], centre[1], 0);
          gl.domElement.addEventListener("webglcontextlost", (e) => {
            e.preventDefault(); setContextLost(true);
          });
          gl.domElement.addEventListener("webglcontextrestored", () => setContextLost(false));
        }}
        style={{ background: "#f5f7fa" }}
        onPointerMissed={() => { onSelectObject(null); onSelectCamera(null); }}
      >
        <Suspense fallback={null}>
          <ambientLight intensity={0.85} />
          <directionalLight position={[20, -20, 40]} intensity={0.6} />
          <Grid position={centre} rotation={[Math.PI / 2, 0, 0]}
                args={[span * 1.3, span * 1.3]}
                cellSize={grid.spacing_m} cellColor="#dfe4ec"
                sectionSize={grid.spacing_m * 5} sectionColor="#c3cbd8"
                fadeDistance={span * 4} infiniteGrid={false} />
          <Line points={[[0, 0, 0], [Math.min(4, span * 0.1), 0, 0]]} color="#b0281f" lineWidth={3} />
          <Line points={[[0, 0, 0], [0, Math.min(4, span * 0.1), 0]]} color="#0f7b4f" lineWidth={3} />
          <Line points={[[0, 0, 0], [0, 0, Math.min(4, span * 0.1)]]} color="#1d4ed8" lineWidth={3} />

          {scene.objects.map((obj) => (
            <ObjectMesh key={obj.id} obj={obj} selected={obj.id === selectedObjectId}
                        onSelect={() => { onSelectObject(obj.id); onSelectCamera(null); }} />
          ))}
          {cameras.map((camera) => (
            <CameraFrustum key={camera.camera_id} camera={camera}
                           selected={camera.camera_id === selectedCameraId}
                           onSelect={() => { onSelectCamera(camera.camera_id); onSelectObject(null); }} />
          ))}
        </Suspense>
        <OrbitControls target={centre} makeDefault enableDamping={false} />
      </Canvas>

      <div className="plan-legend plan-legend-right">
        <strong>3D view.</strong> The same scene, same metres. Dashed outlines are estimated
        dimensions; solid ones were measured. A dashed frustum marks a camera placed by hand —
        its position is approximate, not surveyed.
      </div>
    </div>
  );
}
