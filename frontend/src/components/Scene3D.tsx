import { Canvas } from "@react-three/fiber";
import { Grid, Html, Line, OrbitControls } from "@react-three/drei";
import { Suspense, useMemo, useState } from "react";
import * as THREE from "three";
import type { FactoryMap, MapCamera } from "../lib/calibrationTypes";

/**
 * Three.js defaults to Y-up; the factory frame is Z-up. The camera is given
 * up = (0, 0, 1) so world (x, y, z) can be used directly and Z genuinely points
 * up on screen, rather than every coordinate being swizzled on the way in.
 *
 * Separately, a *graphics* camera looks down its own -Z with +Y up, while an
 * optical (OpenCV) camera looks down +Z with +Y down: R_gl = R_world_camera @
 * diag(1, -1, -1). Frustums below are built from the optical basis vectors
 * explicitly, so that distinction stays visible in the code rather than hidden.
 *
 * Labels are drawn as HTML rather than 3D text on purpose. drei's <Text> fetches
 * a font over the network, which an on-premise install cannot rely on — and a
 * suspended font request blanks the whole canvas.
 */

const STATUS_COLOUR: Record<string, string> = {
  validated: "#0f7b4f",
  calibrated_unvalidated: "#1d4ed8",
  approximate: "#9a6207",
  needs_recalibration: "#b0281f",
  unconfigured: "#5a6779",
};

/** Rebuild R_world_camera (optical) from the roll/pitch/yaw the API reports. */
function opticalBasis(roll: number, pitch: number, yaw: number): THREE.Matrix4 {
  const rx = new THREE.Matrix4().makeRotationX(THREE.MathUtils.degToRad(roll));
  const ry = new THREE.Matrix4().makeRotationY(THREE.MathUtils.degToRad(pitch));
  const rz = new THREE.Matrix4().makeRotationZ(THREE.MathUtils.degToRad(yaw));
  return rz.multiply(ry).multiply(rx);       // R = Rz @ Ry @ Rx, as documented
}

function Label({ position, colour, children }: {
  position: [number, number, number]; colour: string; children: React.ReactNode;
}) {
  return (
    <Html position={position} center distanceFactor={26} zIndexRange={[10, 0]}>
      <div style={{
        color: colour, fontSize: 12, fontWeight: 600, whiteSpace: "nowrap",
        textShadow: "0 0 3px #fff, 0 0 3px #fff, 0 0 3px #fff", pointerEvents: "none",
        fontFamily: "var(--sans)",
      }}>
        {children}
      </div>
    </Html>
  );
}

function CameraFrustum({ camera, selected, onSelect }: {
  camera: MapCamera; selected: boolean; onSelect: () => void;
}) {
  const { position, rpy_deg } = camera;
  const geometry = useMemo(() => {
    if (!position || !rpy_deg) return null;
    const R = opticalBasis(rpy_deg.roll, rpy_deg.pitch, rpy_deg.yaw);
    const e = R.elements;                                   // column-major
    const right = new THREE.Vector3(e[0], e[1], e[2]);      // optical +X
    const down = new THREE.Vector3(e[4], e[5], e[6]);       // optical +Y
    const forward = new THREE.Vector3(e[8], e[9], e[10]);   // optical +Z

    const hfov = THREE.MathUtils.degToRad(camera.horizontal_fov_deg ?? camera.approx_hfov_deg ?? 70);
    const vfov = THREE.MathUtils.degToRad(camera.vertical_fov_deg ?? 40);
    const depth = Math.min(camera.approx_range_m ?? 8, 14);
    const halfW = Math.tan(hfov / 2) * depth;
    const halfH = Math.tan(vfov / 2) * depth;

    const origin = new THREE.Vector3(position.x, position.y, position.z);
    const centre = origin.clone().add(forward.clone().multiplyScalar(depth));
    const corners = [
      centre.clone().add(right.clone().multiplyScalar(-halfW)).add(down.clone().multiplyScalar(-halfH)),
      centre.clone().add(right.clone().multiplyScalar(+halfW)).add(down.clone().multiplyScalar(-halfH)),
      centre.clone().add(right.clone().multiplyScalar(+halfW)).add(down.clone().multiplyScalar(+halfH)),
      centre.clone().add(right.clone().multiplyScalar(-halfW)).add(down.clone().multiplyScalar(+halfH)),
    ];
    const edges: Array<[number[], number[]]> = [
      [origin.toArray(), corners[0].toArray()],
      [origin.toArray(), corners[1].toArray()],
      [origin.toArray(), corners[2].toArray()],
      [origin.toArray(), corners[3].toArray()],
      [corners[0].toArray(), corners[1].toArray()],
      [corners[1].toArray(), corners[2].toArray()],
      [corners[2].toArray(), corners[3].toArray()],
      [corners[3].toArray(), corners[0].toArray()],
    ];
    return { edges };
  }, [position, rpy_deg, camera.horizontal_fov_deg, camera.vertical_fov_deg,
      camera.approx_hfov_deg, camera.approx_range_m]);

  if (!geometry || !position) return null;
  const colour = STATUS_COLOUR[camera.calibration_status] ?? "#5a6779";

  return (
    <group>
      <mesh position={[position.x, position.y, position.z]} onClick={onSelect}>
        <sphereGeometry args={[selected ? 0.36 : 0.26, 16, 16]} />
        <meshStandardMaterial color={colour} />
      </mesh>
      {geometry.edges.map(([a, b], i) => (
        <Line key={i} points={[a as [number, number, number], b as [number, number, number]]}
              color={colour} lineWidth={selected ? 2.5 : 1.2}
              dashed={camera.is_approximate} dashSize={0.3} gapSize={0.22} />
      ))}
      <Label position={[position.x, position.y, position.z + 0.8]} colour={colour}>
        {camera.name}
      </Label>
    </group>
  );
}

function WorldAxes({ length }: { length: number }) {
  return (
    <group>
      <Line points={[[0, 0, 0], [length, 0, 0]]} color="#b0281f" lineWidth={3} />
      <Line points={[[0, 0, 0], [0, length, 0]]} color="#0f7b4f" lineWidth={3} />
      <Line points={[[0, 0, 0], [0, 0, length]]} color="#1d4ed8" lineWidth={3} />
      <Label position={[length + 0.5, 0, 0]} colour="#b0281f">X</Label>
      <Label position={[0, length + 0.5, 0]} colour="#0f7b4f">Y</Label>
      <Label position={[0, 0, length + 0.5]} colour="#1d4ed8">Z up</Label>
    </group>
  );
}

export default function Scene3D({ map, selectedCameraId, onSelectCamera, height = 520 }: {
  map: FactoryMap;
  selectedCameraId: number | null;
  onSelectCamera?: (id: number) => void;
  height?: number;
}) {
  const [contextLost, setContextLost] = useState(false);
  const cs = map.coordinate_system;
  const placed = map.cameras.filter((c) => c.position && c.rpy_deg);
  const centre: [number, number, number] = [
    (cs.grid_min_x + cs.grid_max_x) / 2,
    (cs.grid_min_y + cs.grid_max_y) / 2,
    0,
  ];
  const span = Math.max(cs.grid_max_x - cs.grid_min_x, cs.grid_max_y - cs.grid_min_y);

  return (
    <div className="plan-stage" style={{ height, background: "#f5f7fa" }}>
      {contextLost && (
        <div style={{
          position: "absolute", inset: 0, zIndex: 20, display: "grid", placeItems: "center",
          background: "rgba(245,247,250,0.94)", padding: 24, textAlign: "center",
        }}>
          <div>
            <strong>The 3D view lost its graphics context.</strong>
            <p className="small muted" style={{ maxWidth: 380, margin: "8px auto 12px" }}>
              This usually means hardware acceleration is unavailable — common over remote
              desktops and in virtual machines. The 2D grid shows the same positions and does not
              need a GPU.
            </p>
            <button className="btn btn-sm" type="button" onClick={() => setContextLost(false)}>
              Try again
            </button>
          </div>
        </div>
      )}

      <Canvas
        camera={{
          position: [centre[0] + span * 0.75, centre[1] - span * 0.85, span * 0.55],
          fov: 50, up: [0, 0, 1], near: 0.1, far: span * 12,
        }}
        onCreated={({ gl, camera }) => {
          camera.up.set(0, 0, 1);
          camera.lookAt(centre[0], centre[1], centre[2]);
          gl.domElement.addEventListener("webglcontextlost", (e) => {
            e.preventDefault();
            setContextLost(true);
          });
          gl.domElement.addEventListener("webglcontextrestored", () => setContextLost(false));
        }}
        style={{ background: "#f5f7fa" }}
      >
        {/* Any suspending child would otherwise blank the whole canvas. */}
        <Suspense fallback={null}>
          <ambientLight intensity={0.9} />
          <directionalLight position={[20, -20, 40]} intensity={0.55} />
          <Grid
            position={centre}
            rotation={[Math.PI / 2, 0, 0]}
            args={[span * 1.3, span * 1.3]}
            cellSize={cs.grid_spacing_m}
            cellColor="#dfe4ec"
            sectionSize={cs.grid_spacing_m * 5}
            sectionColor="#c3cbd8"
            fadeDistance={span * 4}
            fadeStrength={1}
            infiniteGrid={false}
          />
          <WorldAxes length={Math.max(2, span * 0.12)} />
          {placed.map((camera) => (
            <CameraFrustum
              key={camera.camera_id}
              camera={camera}
              selected={camera.camera_id === selectedCameraId}
              onSelect={() => onSelectCamera?.(camera.camera_id)}
            />
          ))}
          {map.reference_points.map((point) => (
            <mesh key={point.id} position={[point.x, point.y, point.z]}>
              <boxGeometry args={[0.22, 0.22, 0.22]} />
              <meshStandardMaterial color="#9a6207" />
            </mesh>
          ))}
        </Suspense>
        <OrbitControls target={centre} makeDefault enableDamping={false} />
      </Canvas>

      <div className="plan-legend plan-legend-right">
        <strong>3D view.</strong> Z is up, matching the factory frame. Each pyramid is a camera's
        viewing frustum drawn from its optical axes; a dashed frustum marks a hand-entered pose.
        Cameras with only a floor homography are not drawn, because a homography does not
        determine where a camera is.
      </div>
    </div>
  );
}
