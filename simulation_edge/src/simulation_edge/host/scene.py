"""Build a MuJoCo xArm7 scene from cloud-labs layout, catalog, and state."""

from __future__ import annotations

import hashlib
import html
import json
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

from lab_model.language.domain.component import (
    PRESENCE_BREADBOARD,
    PRESENCE_STORAGE,
    get_tunables,
    presence_of,
)


TABLE_SURFACE_Z_M = 0.12
INCH_TO_M = 0.0254
DEFAULT_WIDTH_MM = 62.0
DEFAULT_DEPTH_MM = 62.0
DEFAULT_HEIGHT_MM = 60.0
MIN_BOX_DIMENSION_M = 0.012
DEFAULT_PROFILE_ID = "demo_boxes"
SPAWN_CLEARANCE_MM = 1.0
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_]+")
_SAFE_PROFILE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class SceneValidationError(ValueError):
    pass


def normalize_capabilities(caps: Any) -> Dict[str, Any]:
    """Normalize the catalog capability block without importing coordinator code."""
    if not isinstance(caps, dict):
        return {
            "statecontrol": {"tunables": {}, "measurables": {}},
            "telemetry": {"teleop": {}, "live_feed": {}},
            "primitives": [],
        }
    if "statecontrol" in caps:
        out = dict(caps)
        sc = dict(out.get("statecontrol") or {})
        sc.setdefault("tunables", {})
        sc.setdefault("measurables", {})
        tel = dict(out.get("telemetry") or {})
        tel.setdefault("teleop", {})
        tel.setdefault("live_feed", {})
        out["statecontrol"] = sc
        out["telemetry"] = tel
        out.setdefault("primitives", [])
        return out
    legacy_tunables = dict(caps.get("tunables") or {})
    teleop = legacy_tunables.pop("teleop", None)
    telemetry = dict(caps.get("telemetry") or {})
    return {
        "statecontrol": {
            "tunables": legacy_tunables,
            "measurables": dict(caps.get("measurables") or {}),
        },
        "telemetry": {
            "teleop": {"pose": dict(teleop)} if isinstance(teleop, dict) else {},
            "live_feed": telemetry,
        },
        "primitives": list(caps.get("primitives") or []),
    }


@dataclass(frozen=True)
class ComponentSpec:
    tag_id: str
    body_name: str
    joint_name: str
    site_name: str
    weld_name: str
    x_m: float
    y_m: float
    yaw_deg: float
    width_m: float
    depth_m: float
    height_m: float
    mass_kg: float
    rgba: tuple[float, float, float, float]
    grasp_height_m: float | None = None
    mesh_name: str | None = None
    mesh_offset_m: tuple[float, float, float] | None = None
    base_cylinder_diameter_m: float | None = None
    base_cylinder_height_m: float | None = None

    @property
    def center_z_m(self) -> float:
        return TABLE_SURFACE_Z_M + self.height_m / 2.0

    @property
    def grasp_site_local_z_m(self) -> float:
        if self.grasp_height_m is not None:
            return self.grasp_height_m - self.height_m / 2.0
        # Match the assisted attachment site to the commanded TCP height.
        # Keeping the site frames coincident prevents weld activation from
        # pulling or rotating an already-centered component.
        finger_grasp_z = max(
            -self.height_m / 2.0 + 0.02,
            -self.height_m / 4.0,
        )
        return finger_grasp_z + 0.03

    @property
    def grasp_tcp_z_m(self) -> float:
        return self.center_z_m + self.grasp_site_local_z_m

    def footprint_half_extents_m(self, yaw_deg: float) -> tuple[float, float]:
        yaw = math.radians(float(yaw_deg))
        c = abs(math.cos(yaw))
        s = abs(math.sin(yaw))
        half_x = c * self.width_m / 2.0 + s * self.depth_m / 2.0
        half_y = s * self.width_m / 2.0 + c * self.depth_m / 2.0
        if self.base_cylinder_diameter_m is not None:
            radius = self.base_cylinder_diameter_m / 2.0
            half_x = max(half_x, radius)
            half_y = max(half_y, radius)
        return half_x, half_y

    @property
    def footprint_width_m(self) -> float:
        diameter = self.base_cylinder_diameter_m or 0.0
        return max(self.width_m, diameter)

    @property
    def footprint_depth_m(self) -> float:
        diameter = self.base_cylinder_diameter_m or 0.0
        return max(self.depth_m, diameter)


@dataclass(frozen=True)
class SceneSpec:
    xml: str
    components: Dict[str, ComponentSpec]
    lab_bounds_mm: Dict[str, float]
    table_bounds_mm: Dict[str, float]
    profile_id: str
    frame_safety_clearance_mm: float = 0.0
    manual_motion_corner_cutoff_mm: float = 0.0
    static_collision_objects: tuple["StaticCollisionObjectSpec", ...] = ()
    spawn_adjustments_mm: Dict[str, Dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class _PlacedFootprint:
    tag_id: str
    x_mm: float
    y_mm: float
    half_x_mm: float
    half_y_mm: float


@dataclass(frozen=True)
class StaticCollisionObjectSpec:
    object_id: str
    geom_name: str
    position_m: tuple[float, float, float]
    dimensions_m: tuple[float, float, float]


@dataclass(frozen=True)
class VisualMeshSpec:
    name: str
    geom_name: str
    mesh_path: Path
    mesh_scale: float
    position_m: tuple[float, float, float]
    quat_wxyz: tuple[float, float, float, float]
    rgba: tuple[float, float, float, float]


@dataclass(frozen=True)
class SimulationProfile:
    profile_id: str
    kind: str
    mass_kg: float
    mesh_path: Path | None = None
    mesh_scale: float = 1.0
    mesh_offset_m: tuple[float, float, float] | None = None
    collision_size_m: tuple[float, float, float] | None = None
    base_cylinder_diameter_m: float | None = None
    base_cylinder_height_m: float | None = None
    grasp_height_m: float | None = None
    rgba: tuple[float, float, float, float] | None = None
    visual_meshes: tuple[VisualMeshSpec, ...] = ()
    static_collision_objects: tuple[StaticCollisionObjectSpec, ...] = ()
    table_bounds_mm: Dict[str, float] | None = None


def _workspace_root() -> Path:
    override = os.getenv("ROBOTIC_TWIN_WORKSPACE")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[4]


def _cloud_labs_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _profiles_root() -> Path:
    override = os.getenv("CLOUDLAB_SIM_PROFILES_PATH")
    if override:
        return Path(override).expanduser().resolve()
    return _cloud_labs_root() / "simulation_profiles"


def _profile_vector(
    value: Any,
    *,
    label: str,
    length: int,
) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise SceneValidationError(f"{label} must contain {length} numbers")
    return tuple(
        _finite_float(item, label=f"{label}[{index}]")
        for index, item in enumerate(value)
    )


def _rotation_matrix_from_quat_wxyz(
    quat: tuple[float, float, float, float],
) -> tuple[tuple[float, float, float], ...]:
    w, x, y, z = quat
    norm = math.sqrt(w * w + x * x + y * y + z * z)
    if norm <= 0:
        raise SceneValidationError("quaternion must be non-zero")
    w, x, y, z = (w / norm, x / norm, y / norm, z / norm)
    return (
        (
            1.0 - 2.0 * (y * y + z * z),
            2.0 * (x * y - z * w),
            2.0 * (x * z + y * w),
        ),
        (
            2.0 * (x * y + z * w),
            1.0 - 2.0 * (x * x + z * z),
            2.0 * (y * z - x * w),
        ),
        (
            2.0 * (x * z - y * w),
            2.0 * (y * z + x * w),
            1.0 - 2.0 * (x * x + y * y),
        ),
    )


def _mat_vec_mul(
    matrix: tuple[tuple[float, float, float], ...],
    vector: tuple[float, float, float],
) -> tuple[float, float, float]:
    return tuple(
        sum(matrix[row][col] * vector[col] for col in range(3))
        for row in range(3)
    )


def _parse_lab_frame_environment(
    profile_path: Path,
    frame: Mapping[str, Any],
) -> tuple[
    tuple[VisualMeshSpec, ...],
    tuple[StaticCollisionObjectSpec, ...],
    Dict[str, float],
]:
    outer_size_in = _profile_vector(
        frame.get("outer_size_in"),
        label="environment.lab_frame.outer_size_in",
        length=3,
    )
    if any(value <= 0 for value in outer_size_in):
        raise SceneValidationError("lab frame outer dimensions must be positive")
    beam_size_in = _finite_float(
        frame.get("beam_size_in", 0.75),
        label="environment.lab_frame.beam_size_in",
    )
    safety_margin_in = _finite_float(
        frame.get("safety_margin_in", 3.0),
        label="environment.lab_frame.safety_margin_in",
    )
    camera_reserved_depth_in = _finite_float(
        frame.get("camera_reserved_depth_in", 6.0),
        label="environment.lab_frame.camera_reserved_depth_in",
    )
    if beam_size_in <= 0 or safety_margin_in < 0 or camera_reserved_depth_in < 0:
        raise SceneValidationError("lab frame clearance dimensions are invalid")

    outer_x_m, outer_y_m, outer_z_m = (
        value * INCH_TO_M for value in outer_size_in
    )
    wall_thickness_m = (beam_size_in + safety_margin_in) * INCH_TO_M
    top_thickness_m = (
        camera_reserved_depth_in + safety_margin_in
    ) * INCH_TO_M
    center_z_m = TABLE_SURFACE_Z_M + outer_z_m / 2.0
    static_objects = (
        StaticCollisionObjectSpec(
            object_id="lab_frame_left_clearance",
            geom_name="lab_frame_left_clearance",
            position_m=(
                -outer_x_m / 2.0 + wall_thickness_m / 2.0,
                0.0,
                center_z_m,
            ),
            dimensions_m=(wall_thickness_m, outer_y_m, outer_z_m),
        ),
        StaticCollisionObjectSpec(
            object_id="lab_frame_right_clearance",
            geom_name="lab_frame_right_clearance",
            position_m=(
                outer_x_m / 2.0 - wall_thickness_m / 2.0,
                0.0,
                center_z_m,
            ),
            dimensions_m=(wall_thickness_m, outer_y_m, outer_z_m),
        ),
        StaticCollisionObjectSpec(
            object_id="lab_frame_front_clearance",
            geom_name="lab_frame_front_clearance",
            position_m=(
                0.0,
                -outer_y_m / 2.0 + wall_thickness_m / 2.0,
                center_z_m,
            ),
            dimensions_m=(outer_x_m, wall_thickness_m, outer_z_m),
        ),
        StaticCollisionObjectSpec(
            object_id="lab_frame_back_clearance",
            geom_name="lab_frame_back_clearance",
            position_m=(
                0.0,
                outer_y_m / 2.0 - wall_thickness_m / 2.0,
                center_z_m,
            ),
            dimensions_m=(outer_x_m, wall_thickness_m, outer_z_m),
        ),
        StaticCollisionObjectSpec(
            object_id="lab_frame_top_camera_clearance",
            geom_name="lab_frame_top_camera_clearance",
            position_m=(
                0.0,
                0.0,
                TABLE_SURFACE_Z_M + outer_z_m - top_thickness_m / 2.0,
            ),
            dimensions_m=(outer_x_m, outer_y_m, top_thickness_m),
        ),
    )
    table_bounds_mm = {
        "x_min": -outer_x_m * 500.0,
        "x_max": outer_x_m * 500.0,
        "y_min": -outer_y_m * 500.0,
        "y_max": outer_y_m * 500.0,
    }

    visual_meshes: tuple[VisualMeshSpec, ...] = ()
    visual_mesh = frame.get("visual_mesh")
    if isinstance(visual_mesh, str) and visual_mesh.strip():
        mesh_path = (profile_path.parent / visual_mesh).resolve()
        if not mesh_path.is_file():
            raise SceneValidationError(f"Lab frame visual mesh not found: {mesh_path}")
        mesh_scale = _finite_float(
            frame.get("visual_mesh_scale", 0.001),
            label="environment.lab_frame.visual_mesh_scale",
        )
        if mesh_scale <= 0:
            raise SceneValidationError("lab frame mesh scale must be positive")
        bounds = frame.get("visual_mesh_bounds_mm")
        if not isinstance(bounds, Mapping):
            raise SceneValidationError(
                "environment.lab_frame.visual_mesh_bounds_mm is required"
            )
        bounds_min = _profile_vector(
            bounds.get("min"),
            label="environment.lab_frame.visual_mesh_bounds_mm.min",
            length=3,
        )
        bounds_max = _profile_vector(
            bounds.get("max"),
            label="environment.lab_frame.visual_mesh_bounds_mm.max",
            length=3,
        )
        if any(low >= high for low, high in zip(bounds_min, bounds_max)):
            raise SceneValidationError("lab frame visual mesh bounds are invalid")
        quat = _profile_vector(
            frame.get("visual_quat_wxyz", (1.0, 0.0, 0.0, 0.0)),
            label="environment.lab_frame.visual_quat_wxyz",
            length=4,
        )
        rgba = _profile_vector(
            frame.get("visual_rgba", (0.72, 0.74, 0.76, 1.0)),
            label="environment.lab_frame.visual_rgba",
            length=4,
        )
        local_center = tuple(
            ((low + high) / 2.0) * mesh_scale
            for low, high in zip(bounds_min, bounds_max)
        )
        desired_center = (0.0, 0.0, center_z_m)
        rotated_center = _mat_vec_mul(
            _rotation_matrix_from_quat_wxyz(quat),
            local_center,
        )
        visual_meshes = (
            VisualMeshSpec(
                name="lab_frame_visual_mesh",
                geom_name="visual_lab_frame",
                mesh_path=mesh_path,
                mesh_scale=mesh_scale,
                position_m=tuple(
                    desired_center[index] - rotated_center[index]
                    for index in range(3)
                ),
                quat_wxyz=quat,
                rgba=rgba,
            ),
        )

    return visual_meshes, static_objects, table_bounds_mm


def _parse_profile_environment(
    profile_path: Path,
    document: Mapping[str, Any],
) -> tuple[
    tuple[VisualMeshSpec, ...],
    tuple[StaticCollisionObjectSpec, ...],
    Dict[str, float] | None,
]:
    environment = document.get("environment")
    if not isinstance(environment, Mapping):
        return (), (), None
    frame = environment.get("lab_frame")
    if not isinstance(frame, Mapping):
        return (), (), None
    return _parse_lab_frame_environment(profile_path, frame)


def load_simulation_profile(profile_id: str | None = None) -> SimulationProfile:
    selected = str(
        profile_id or os.getenv("CLOUDLAB_SIM_PROFILE") or DEFAULT_PROFILE_ID
    ).strip()
    if not _SAFE_PROFILE_ID.fullmatch(selected):
        raise SceneValidationError(f"Invalid MuJoCo simulation profile id: {selected!r}")
    path = _profiles_root() / selected / "profile.json"
    if not path.is_file():
        raise SceneValidationError(
            f"MuJoCo simulation profile not found: {path}"
        )
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SceneValidationError(
            f"Failed to read MuJoCo simulation profile {path}: {exc}"
        ) from exc
    if not isinstance(document, Mapping):
        raise SceneValidationError(f"Simulation profile {path} must be an object")
    visual_meshes, static_collision_objects, table_bounds_mm = (
        _parse_profile_environment(path, document)
    )
    declared_id = str(document.get("id") or selected)
    if declared_id != selected:
        raise SceneValidationError(
            f"Simulation profile id {declared_id!r} does not match {selected!r}"
        )
    geometry = document.get("geometry")
    if not isinstance(geometry, Mapping):
        raise SceneValidationError(f"Simulation profile {selected} lacks geometry")
    kind = str(geometry.get("kind") or "").strip()
    mass_kg = _finite_float(
        geometry.get("mass_kg", 0.12),
        label=f"{selected}.geometry.mass_kg",
    )
    if mass_kg <= 0:
        raise SceneValidationError(f"{selected}.geometry.mass_kg must be positive")
    if kind == "catalog_box":
        return SimulationProfile(
            profile_id=selected,
            kind=kind,
            mass_kg=mass_kg,
            visual_meshes=visual_meshes,
            static_collision_objects=static_collision_objects,
            table_bounds_mm=table_bounds_mm,
        )
    if kind != "mesh":
        raise SceneValidationError(
            f"Unsupported geometry kind {kind!r} in profile {selected}"
        )

    mesh_value = geometry.get("mesh")
    if not isinstance(mesh_value, str) or not mesh_value.strip():
        raise SceneValidationError(f"{selected}.geometry.mesh is required")
    mesh_path = (path.parent / mesh_value).resolve()
    if not mesh_path.is_file():
        raise SceneValidationError(f"Profile mesh not found: {mesh_path}")
    mesh_scale = _finite_float(
        geometry.get("mesh_scale", 1.0),
        label=f"{selected}.geometry.mesh_scale",
    )
    if mesh_scale <= 0:
        raise SceneValidationError(f"{selected}.geometry.mesh_scale must be positive")
    bounds = geometry.get("mesh_bounds_mm")
    if not isinstance(bounds, Mapping):
        raise SceneValidationError(
            f"{selected}.geometry.mesh_bounds_mm is required"
        )
    bounds_min = _profile_vector(
        bounds.get("min"),
        label=f"{selected}.geometry.mesh_bounds_mm.min",
        length=3,
    )
    bounds_max = _profile_vector(
        bounds.get("max"),
        label=f"{selected}.geometry.mesh_bounds_mm.max",
        length=3,
    )
    if any(low >= high for low, high in zip(bounds_min, bounds_max)):
        raise SceneValidationError(f"{selected} mesh bounds must have positive size")
    mesh_offset_m = tuple(
        -((low + high) / 2.0) * mesh_scale
        for low, high in zip(bounds_min, bounds_max)
    )
    collision_mm = _profile_vector(
        geometry.get("collision_size_mm"),
        label=f"{selected}.geometry.collision_size_mm",
        length=3,
    )
    if any(value <= 0 for value in collision_mm):
        raise SceneValidationError(f"{selected} collision dimensions must be positive")
    base_cylinder_diameter_m: float | None = None
    base_cylinder_height_m: float | None = None
    base_cylinder = geometry.get("base_collision_cylinder_mm")
    if base_cylinder is not None:
        if not isinstance(base_cylinder, Mapping):
            raise SceneValidationError(
                f"{selected}.geometry.base_collision_cylinder_mm must be an object"
            )
        diameter_mm = _finite_float(
            base_cylinder.get("diameter"),
            label=f"{selected}.geometry.base_collision_cylinder_mm.diameter",
        )
        base_height_mm = _finite_float(
            base_cylinder.get("height"),
            label=f"{selected}.geometry.base_collision_cylinder_mm.height",
        )
        if diameter_mm <= 0 or not 0 < base_height_mm <= collision_mm[2]:
            raise SceneValidationError(
                f"{selected} base cylinder must have positive diameter and fit inside the component height"
            )
        base_cylinder_diameter_m = diameter_mm / 1000.0
        base_cylinder_height_m = base_height_mm / 1000.0
    grasp_height_mm = _finite_float(
        geometry.get("grasp_height_mm"),
        label=f"{selected}.geometry.grasp_height_mm",
    )
    if not 0 < grasp_height_mm < collision_mm[2]:
        raise SceneValidationError(
            f"{selected} grasp height must lie inside the collision body"
        )
    rgba = _profile_vector(
        geometry.get("rgba", (0.05, 0.05, 0.05, 1.0)),
        label=f"{selected}.geometry.rgba",
        length=4,
    )
    return SimulationProfile(
        profile_id=selected,
        kind=kind,
        mass_kg=mass_kg,
        mesh_path=mesh_path,
        mesh_scale=mesh_scale,
        mesh_offset_m=mesh_offset_m,
        collision_size_m=tuple(value / 1000.0 for value in collision_mm),
        base_cylinder_diameter_m=base_cylinder_diameter_m,
        base_cylinder_height_m=base_cylinder_height_m,
        grasp_height_m=grasp_height_mm / 1000.0,
        rgba=rgba,
        visual_meshes=visual_meshes,
        static_collision_objects=static_collision_objects,
        table_bounds_mm=table_bounds_mm,
    )


def xarm_model_path() -> Path:
    override = os.getenv("MUJOCO_XARM7_XML")
    if override:
        path = Path(override).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"MuJoCo Menagerie xArm7 model not found: {path}")
        return path

    candidates = [
        _cloud_labs_root()
        / "third_party"
        / "mujoco_menagerie"
        / "ufactory_xarm7"
        / "xarm7.xml",
        _workspace_root()
        / "external"
        / "mujoco_menagerie"
        / "ufactory_xarm7"
        / "xarm7.xml",
    ]
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file():
            return resolved
    searched = "\n- ".join(str(path.resolve()) for path in candidates)
    raise FileNotFoundError(
        "MuJoCo Menagerie xArm7 model not found. Set MUJOCO_XARM7_XML or "
        f"install the vendored model at one of:\n- {searched}"
    )


def _has_table_pose(catalog_row: Mapping[str, Any]) -> bool:
    caps = normalize_capabilities(catalog_row.get("capabilities") or {})
    statecontrol = caps.get("statecontrol") or {}
    tunables = statecontrol.get("tunables") if isinstance(statecontrol, dict) else {}
    return isinstance(tunables, dict) and "nominal_pose" in tunables


def _finite_float(value: Any, *, label: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise SceneValidationError(f"{label} must be numeric") from exc
    if not math.isfinite(out):
        raise SceneValidationError(f"{label} must be finite")
    return out


def _component_color(comp_type: str) -> tuple[float, float, float, float]:
    fixed = {
        "OPTICAL_FILTER": (0.15, 0.75, 0.95, 1.0),
        "OPTICAL_MIRROR": (0.72, 0.48, 0.95, 1.0),
        "OPTICAL_LENS": (0.20, 0.85, 0.55, 1.0),
        "OPTICAL_CRYSTAL": (0.95, 0.55, 0.20, 1.0),
        "OPTICAL_BEAM_BLOCK": (0.90, 0.20, 0.22, 1.0),
        "OPTICAL_BEAMSPLITTER": (0.95, 0.78, 0.18, 1.0),
        "OPTICAL_CAMERA": (0.35, 0.42, 0.52, 1.0),
    }
    if comp_type in fixed:
        return fixed[comp_type]
    digest = hashlib.sha256(comp_type.encode("utf-8")).digest()
    return (
        0.25 + digest[0] / 510.0,
        0.25 + digest[1] / 510.0,
        0.25 + digest[2] / 510.0,
        1.0,
    )


def _safe_suffix(tag_id: str) -> str:
    cleaned = _SAFE_NAME.sub("_", tag_id).strip("_")
    if not cleaned:
        cleaned = "component"
    digest = hashlib.sha1(tag_id.encode("utf-8")).hexdigest()[:8]
    return f"{cleaned}_{digest}"


def _catalog_size_mm(row: Mapping[str, Any]) -> tuple[float, float, float]:
    size = row.get("size")
    if isinstance(size, Mapping):
        width = _finite_float(size.get("width", DEFAULT_WIDTH_MM), label="catalog width")
        depth = _finite_float(size.get("height", DEFAULT_DEPTH_MM), label="catalog depth")
    elif isinstance(size, (int, float)):
        width = depth = _finite_float(size, label="catalog size")
    else:
        width, depth = DEFAULT_WIDTH_MM, DEFAULT_DEPTH_MM
    height = _finite_float(row.get("height_mm", DEFAULT_HEIGHT_MM), label="catalog height_mm")
    return width, depth, height


def _footprint_half_extents_mm(
    width_mm: float,
    depth_mm: float,
    yaw_deg: float,
) -> tuple[float, float]:
    yaw = math.radians(yaw_deg)
    c = abs(math.cos(yaw))
    s = abs(math.sin(yaw))
    return (
        c * width_mm / 2.0 + s * depth_mm / 2.0,
        s * width_mm / 2.0 + c * depth_mm / 2.0,
    )


def _footprint_inside_bounds(
    x_mm: float,
    y_mm: float,
    half_x_mm: float,
    half_y_mm: float,
    bounds: Mapping[str, float],
) -> bool:
    return (
        bounds["x_min"] + half_x_mm
        <= x_mm
        <= bounds["x_max"] - half_x_mm
        and bounds["y_min"] + half_y_mm
        <= y_mm
        <= bounds["y_max"] - half_y_mm
    )


def _footprint_overlaps(
    x_mm: float,
    y_mm: float,
    half_x_mm: float,
    half_y_mm: float,
    placed: _PlacedFootprint,
) -> bool:
    return (
        abs(x_mm - placed.x_mm)
        < half_x_mm + placed.half_x_mm + SPAWN_CLEARANCE_MM
        and abs(y_mm - placed.y_mm)
        < half_y_mm + placed.half_y_mm + SPAWN_CLEARANCE_MM
    )


def _grid_parameters_mm(layout: Mapping[str, Any]) -> tuple[float, float, float]:
    breadboard = layout.get("breadboard")
    if not isinstance(breadboard, Mapping):
        return 25.0, 0.0, 0.0
    spacing = _finite_float(
        breadboard.get("grid_spacing_mm", 25.0),
        label="breadboard.grid_spacing_mm",
    )
    if spacing <= 0:
        raise SceneValidationError("breadboard.grid_spacing_mm must be positive")
    offset = breadboard.get("origin_offset_mm")
    if not isinstance(offset, Mapping):
        return spacing, 0.0, 0.0
    return (
        spacing,
        _finite_float(offset.get("x", 0.0), label="breadboard.origin_offset_mm.x"),
        _finite_float(offset.get("y", 0.0), label="breadboard.origin_offset_mm.y"),
    )


def _candidate_spawn_points_mm(
    x_mm: float,
    y_mm: float,
    half_x_mm: float,
    half_y_mm: float,
    bounds: Mapping[str, float],
    layout: Mapping[str, Any],
) -> list[tuple[float, float]]:
    spacing, origin_x, origin_y = _grid_parameters_mm(layout)
    candidates = [(x_mm, y_mm)]
    ix_min = math.ceil((bounds["x_min"] + half_x_mm - origin_x) / spacing)
    ix_max = math.floor((bounds["x_max"] - half_x_mm - origin_x) / spacing)
    iy_min = math.ceil((bounds["y_min"] + half_y_mm - origin_y) / spacing)
    iy_max = math.floor((bounds["y_max"] - half_y_mm - origin_y) / spacing)
    grid_points = [
        (origin_x + ix * spacing, origin_y + iy * spacing)
        for ix in range(ix_min, ix_max + 1)
        for iy in range(iy_min, iy_max + 1)
    ]
    grid_points.sort(
        key=lambda point: (
            (point[0] - x_mm) ** 2 + (point[1] - y_mm) ** 2,
            abs(point[0] - x_mm) + abs(point[1] - y_mm),
            point[0],
            point[1],
        )
    )
    seen = {(round(x_mm, 6), round(y_mm, 6))}
    for point in grid_points:
        key = (round(point[0], 6), round(point[1], 6))
        if key in seen:
            continue
        seen.add(key)
        candidates.append(point)
    return candidates


def _find_clear_spawn_point_mm(
    x_mm: float,
    y_mm: float,
    half_x_mm: float,
    half_y_mm: float,
    bounds: Mapping[str, float],
    layout: Mapping[str, Any],
    placed: list[_PlacedFootprint],
) -> tuple[float, float] | None:
    for candidate_x, candidate_y in _candidate_spawn_points_mm(
        x_mm,
        y_mm,
        half_x_mm,
        half_y_mm,
        bounds,
        layout,
    ):
        if not _footprint_inside_bounds(
            candidate_x,
            candidate_y,
            half_x_mm,
            half_y_mm,
            bounds,
        ):
            continue
        if any(
            _footprint_overlaps(
                candidate_x,
                candidate_y,
                half_x_mm,
                half_y_mm,
                existing,
            )
            for existing in placed
        ):
            continue
        return candidate_x, candidate_y
    return None


def build_scene_spec(
    layout: Mapping[str, Any],
    catalog_rows: Iterable[Mapping[str, Any]],
    state: Mapping[str, Any],
    *,
    profile_id: str | None = None,
) -> SceneSpec:
    profile = load_simulation_profile(profile_id)
    bounds_raw = layout.get("lab_bounds_mm")
    if not isinstance(bounds_raw, Mapping):
        raise SceneValidationError("layout.json is missing lab_bounds_mm")
    bounds = {
        key: _finite_float(bounds_raw.get(key), label=f"lab_bounds_mm.{key}")
        for key in ("x_min", "x_max", "y_min", "y_max")
    }
    if bounds["x_min"] >= bounds["x_max"] or bounds["y_min"] >= bounds["y_max"]:
        raise SceneValidationError("lab bounds must have positive width and height")

    frame_safety = layout.get("frame_safety")
    frame_safety_clearance_mm = 0.0
    if isinstance(frame_safety, Mapping):
        frame_safety_clearance_mm = _finite_float(
            frame_safety.get("clearance_mm", 0.0),
            label="frame_safety.clearance_mm",
        )
    if frame_safety_clearance_mm < 0:
        raise SceneValidationError("frame safety clearance must be non-negative")
    manual_workspace = layout.get("manual_motion_workspace")
    manual_motion_corner_cutoff_mm = 0.0
    if isinstance(manual_workspace, Mapping):
        corner_cutoff_raw = manual_workspace.get(
            "corner_cutoff_mm",
            manual_workspace.get("half_extent_mm", 0.0),
        )
        manual_motion_corner_cutoff_mm = _finite_float(
            corner_cutoff_raw,
            label="manual_motion_workspace.corner_cutoff_mm",
        )
    if manual_motion_corner_cutoff_mm < 0:
        raise SceneValidationError(
            "manual motion workspace corner cutoff must be non-negative"
        )
    placement_bounds = {
        "x_min": bounds["x_min"] + frame_safety_clearance_mm,
        "x_max": bounds["x_max"] - frame_safety_clearance_mm,
        "y_min": bounds["y_min"] + frame_safety_clearance_mm,
        "y_max": bounds["y_max"] - frame_safety_clearance_mm,
    }
    if (
        placement_bounds["x_min"] >= placement_bounds["x_max"]
        or placement_bounds["y_min"] >= placement_bounds["y_max"]
    ):
        raise SceneValidationError("frame safety clearance leaves no usable lab area")

    catalog_map = {
        str(row.get("tag_id")): dict(row)
        for row in catalog_rows
        if isinstance(row, Mapping) and row.get("tag_id")
    }
    components_raw = state.get("components") or {}
    if not isinstance(components_raw, Mapping):
        raise SceneValidationError("lab state components must be an object")

    specs: Dict[str, ComponentSpec] = {}
    spawn_adjustments: Dict[str, Dict[str, Any]] = {}
    placed: list[_PlacedFootprint] = []
    invalid: list[str] = []
    for tag_id, entry in components_raw.items():
        row = catalog_map.get(str(tag_id))
        if row is None or not _has_table_pose(row) or not isinstance(entry, dict):
            continue
        if presence_of(entry) not in (PRESENCE_BREADBOARD, PRESENCE_STORAGE):
            continue
        nominal = get_tunables(entry).get("nominal_pose") or {}
        try:
            x_mm = _finite_float(nominal.get("x"), label=f"{tag_id}.x")
            y_mm = _finite_float(nominal.get("y"), label=f"{tag_id}.y")
            yaw_deg = _finite_float(nominal.get("rotation", 0.0), label=f"{tag_id}.rotation")
            width_mm, depth_mm, height_mm = _catalog_size_mm(row)
            if profile.collision_size_m is not None:
                width_mm, depth_mm, height_mm = (
                    value * 1000.0 for value in profile.collision_size_m
                )
        except SceneValidationError as exc:
            invalid.append(f"{tag_id}: {exc}")
            continue
        half_x_mm, half_y_mm = _footprint_half_extents_mm(
            width_mm,
            depth_mm,
            yaw_deg,
        )
        if profile.base_cylinder_diameter_m is not None:
            base_radius_mm = profile.base_cylinder_diameter_m * 500.0
            half_x_mm = max(half_x_mm, base_radius_mm)
            half_y_mm = max(half_y_mm, base_radius_mm)
        if not _footprint_inside_bounds(
            x_mm,
            y_mm,
            half_x_mm,
            half_y_mm,
            placement_bounds,
        ):
            invalid.append(
                f"{tag_id}: ({x_mm:.1f}, {y_mm:.1f}) mm footprint enters the "
                f"{frame_safety_clearance_mm:.1f} mm frame safety boundary"
            )
            continue
        clear_spawn = _find_clear_spawn_point_mm(
            x_mm,
            y_mm,
            half_x_mm,
            half_y_mm,
            placement_bounds,
            layout,
            placed,
        )
        if clear_spawn is None:
            invalid.append(
                f"{tag_id}: ({x_mm:.1f}, {y_mm:.1f}) mm cannot be placed without "
                "overlapping another startup component inside "
                f"x=[{bounds['x_min']:.1f},{bounds['x_max']:.1f}], "
                f"y=[{bounds['y_min']:.1f},{bounds['y_max']:.1f}]"
            )
            continue
        spawn_x_mm, spawn_y_mm = clear_spawn
        if abs(spawn_x_mm - x_mm) > 1e-6 or abs(spawn_y_mm - y_mm) > 1e-6:
            spawn_adjustments[str(tag_id)] = {
                "x": spawn_x_mm,
                "y": spawn_y_mm,
                "rotation": yaw_deg,
                "original_x": x_mm,
                "original_y": y_mm,
                "reason": "startup_footprint_overlap",
            }
        placed.append(
            _PlacedFootprint(
                tag_id=str(tag_id),
                x_mm=spawn_x_mm,
                y_mm=spawn_y_mm,
                half_x_mm=half_x_mm,
                half_y_mm=half_y_mm,
            )
        )
        suffix = _safe_suffix(str(tag_id))
        specs[str(tag_id)] = ComponentSpec(
            tag_id=str(tag_id),
            body_name=f"component_{suffix}",
            joint_name=f"freejoint_{suffix}",
            site_name=f"grasp_site_{suffix}",
            weld_name=f"assisted_grasp_{suffix}",
            x_m=spawn_x_mm / 1000.0,
            y_m=spawn_y_mm / 1000.0,
            yaw_deg=yaw_deg,
            width_m=max(width_mm / 1000.0, MIN_BOX_DIMENSION_M),
            depth_m=max(depth_mm / 1000.0, MIN_BOX_DIMENSION_M),
            height_m=max(height_mm / 1000.0, MIN_BOX_DIMENSION_M),
            mass_kg=profile.mass_kg,
            rgba=(
                profile.rgba
                or _component_color(str(row.get("type") or entry.get("type") or ""))
            ),
            grasp_height_m=profile.grasp_height_m,
            mesh_name=("profile_component_mesh" if profile.mesh_path else None),
            mesh_offset_m=profile.mesh_offset_m,
            base_cylinder_diameter_m=profile.base_cylinder_diameter_m,
            base_cylinder_height_m=profile.base_cylinder_height_m,
        )

    if invalid:
        raise SceneValidationError("Invalid MuJoCo component poses:\n- " + "\n- ".join(invalid))
    if not specs:
        raise SceneValidationError("No movable table components are available for MuJoCo")

    model_path = xarm_model_path()
    asset_dir = model_path.parent / "assets"
    table_bounds = dict(bounds)
    if profile.table_bounds_mm is not None:
        table_bounds = {
            "x_min": min(bounds["x_min"], profile.table_bounds_mm["x_min"]),
            "x_max": max(bounds["x_max"], profile.table_bounds_mm["x_max"]),
            "y_min": min(bounds["y_min"], profile.table_bounds_mm["y_min"]),
            "y_max": max(bounds["y_max"], profile.table_bounds_mm["y_max"]),
        }
    x_center_m = (table_bounds["x_min"] + table_bounds["x_max"]) / 2000.0
    y_center_m = (table_bounds["y_min"] + table_bounds["y_max"]) / 2000.0
    half_x_m = (table_bounds["x_max"] - table_bounds["x_min"]) / 2000.0
    half_y_m = (table_bounds["y_max"] - table_bounds["y_min"]) / 2000.0

    bodies: list[str] = []
    welds: list[str] = []
    for spec in specs.values():
        half = (spec.width_m / 2.0, spec.depth_m / 2.0, spec.height_m / 2.0)
        yaw = math.radians(spec.yaw_deg)
        quat = (math.cos(yaw / 2.0), 0.0, 0.0, math.sin(yaw / 2.0))
        rgba = " ".join(f"{v:.5f}" for v in spec.rgba)
        if spec.mesh_name and spec.mesh_offset_m:
            mesh_offset = " ".join(f"{value:.8f}" for value in spec.mesh_offset_m)
            base_geometry = ""
            if (
                spec.base_cylinder_diameter_m is not None
                and spec.base_cylinder_height_m is not None
            ):
                base_center_z = -spec.height_m / 2.0 + spec.base_cylinder_height_m / 2.0
                base_geometry = f"""
      <geom name="base_geom_{html.escape(spec.body_name)}" type="cylinder"
        pos="0 0 {base_center_z:.8f}"
        size="{spec.base_cylinder_diameter_m / 2.0:.8f} {spec.base_cylinder_height_m / 2.0:.8f}"
        mass="0" rgba="{rgba}" friction="1.2 0.02 0.002"
        solref="0.008 1" solimp="0.95 0.99 0.001"/>"""
            geometry = f"""
      <geom name="visual_{html.escape(spec.body_name)}" type="mesh"
        mesh="{html.escape(spec.mesh_name)}" pos="{mesh_offset}"
        mass="0" contype="0" conaffinity="0" rgba="{rgba}"/>
      <geom name="geom_{html.escape(spec.body_name)}" type="box"
        size="{half[0]:.8f} {half[1]:.8f} {half[2]:.8f}"
        mass="{spec.mass_kg:.8f}" rgba="0 0 0 0"
        friction="1.2 0.02 0.002"
        solref="0.008 1" solimp="0.95 0.99 0.001"/>{base_geometry}"""
        else:
            geometry = f"""
      <geom name="geom_{html.escape(spec.body_name)}" type="box"
        size="{half[0]:.8f} {half[1]:.8f} {half[2]:.8f}"
        mass="{spec.mass_kg:.8f}" rgba="{rgba}" friction="1.2 0.02 0.002"
        solref="0.008 1" solimp="0.95 0.99 0.001"/>"""
        bodies.append(
            f"""
    <body name="{html.escape(spec.body_name)}"
      pos="{spec.x_m:.8f} {spec.y_m:.8f} {spec.center_z_m:.8f}"
      quat="{quat[0]:.8f} {quat[1]:.8f} {quat[2]:.8f} {quat[3]:.8f}">
      <freejoint name="{html.escape(spec.joint_name)}"/>
      {geometry}
      <site name="{html.escape(spec.site_name)}"
        pos="0 0 {spec.grasp_site_local_z_m:.8f}" quat="0 0 1 0"
        size="0.004"
        rgba="1 1 0 1"/>
    </body>"""
        )
        welds.append(
            f"""
    <weld name="{html.escape(spec.weld_name)}" site1="link_tcp"
      site2="{html.escape(spec.site_name)}" active="false"
      solref="0.002 1" solimp="0.99 0.999 0.0001"/>"""
        )

    xml = f"""<mujoco model="cloud-labs xarm7">
  <include file="{html.escape(model_path.as_posix())}"/>
  <compiler meshdir="{html.escape(asset_dir.as_posix())}"/>
  <option timestep="0.002" integrator="implicitfast"/>
  <statistic center="{x_center_m:.5f} {y_center_m:.5f} 0.30"
    extent="{max(half_x_m, half_y_m, 0.75):.5f}"/>
  <visual>
    <headlight diffuse="0.72 0.72 0.72" ambient="0.32 0.32 0.32"
      specular="0.1 0.1 0.1"/>
    <global azimuth="135" elevation="-28"/>
  </visual>
  <asset>
    {(
        f'<mesh name="profile_component_mesh" '
        f'file="{html.escape(profile.mesh_path.as_posix())}" '
        f'scale="{profile.mesh_scale:.8f} {profile.mesh_scale:.8f} '
        f'{profile.mesh_scale:.8f}"/>'
        if profile.mesh_path
        else ""
    )}
    {''.join(
        f'''
    <mesh name="{html.escape(mesh.name)}"
      file="{html.escape(mesh.mesh_path.as_posix())}"
      scale="{mesh.mesh_scale:.8f} {mesh.mesh_scale:.8f} {mesh.mesh_scale:.8f}"/>'''
        for mesh in profile.visual_meshes
    )}
    <texture type="skybox" builtin="gradient" rgb1="0.28 0.42 0.62"
      rgb2="0.02 0.025 0.05" width="512" height="3072"/>
    <texture type="2d" name="table_grid" builtin="checker" mark="edge"
      rgb1="0.30 0.34 0.38" rgb2="0.18 0.21 0.25" markrgb="0.7 0.72 0.75"
      width="300" height="300"/>
    <material name="table_material" texture="table_grid" texuniform="true"
      texrepeat="20 20" reflectance="0.12"/>
  </asset>
  <worldbody>
    <light pos="0.2 -0.3 1.6" dir="0 0 -1" directional="true"/>
    <geom name="floor" type="plane" size="0 0 0.05" rgba="0.08 0.10 0.14 1"/>
    <geom name="tabletop" type="box"
      pos="{x_center_m:.8f} {y_center_m:.8f} 0.08"
      size="{half_x_m:.8f} {half_y_m:.8f} 0.04"
      material="table_material" friction="1 0.01 0.001"/>
    {''.join(
        f'''
    <geom name="{html.escape(mesh.geom_name)}" type="mesh"
      mesh="{html.escape(mesh.name)}"
      pos="{mesh.position_m[0]:.8f} {mesh.position_m[1]:.8f} {mesh.position_m[2]:.8f}"
      quat="{mesh.quat_wxyz[0]:.8f} {mesh.quat_wxyz[1]:.8f} {mesh.quat_wxyz[2]:.8f} {mesh.quat_wxyz[3]:.8f}"
      rgba="{mesh.rgba[0]:.5f} {mesh.rgba[1]:.5f} {mesh.rgba[2]:.5f} {mesh.rgba[3]:.5f}"
      contype="0" conaffinity="0"/>'''
        for mesh in profile.visual_meshes
    )}
    {''.join(
        f'''
    <geom name="{html.escape(obj.geom_name)}" type="box"
      pos="{obj.position_m[0]:.8f} {obj.position_m[1]:.8f} {obj.position_m[2]:.8f}"
      size="{obj.dimensions_m[0] / 2.0:.8f} {obj.dimensions_m[1] / 2.0:.8f} {obj.dimensions_m[2] / 2.0:.8f}"
      rgba="0 0 0 0" friction="1 0.01 0.001"/>'''
        for obj in profile.static_collision_objects
    )}
    {''.join(bodies)}
  </worldbody>
  <equality>
    {''.join(welds)}
  </equality>
</mujoco>
"""
    return SceneSpec(
        xml=xml,
        components=specs,
        lab_bounds_mm=bounds,
        table_bounds_mm=table_bounds,
        profile_id=profile.profile_id,
        frame_safety_clearance_mm=frame_safety_clearance_mm,
        manual_motion_corner_cutoff_mm=manual_motion_corner_cutoff_mm,
        static_collision_objects=profile.static_collision_objects,
        spawn_adjustments_mm=spawn_adjustments,
    )
