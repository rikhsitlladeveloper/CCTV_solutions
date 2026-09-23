"""Model -> schema conversion, in one place so credentials cannot leak by accident."""
from __future__ import annotations

from sqlalchemy.orm import Session

from .models import Area, Camera, Floor, FloorPlan, ReviewStatus
from .schemas import (
    AreaOut, BuildingOut, CameraOut, FloorOut, FloorPlanOut, LocationPathOut, PlacementOut, SiteOut,
)


def location_path(area: Area | None) -> LocationPathOut:
    if area is None:
        return LocationPathOut()
    floor = area.floor
    building = floor.building
    site = building.site
    return LocationPathOut(
        site_id=site.id, site=site.name,
        building_id=building.id, building=building.name,
        floor_id=floor.id, floor=floor.name,
        area_id=area.id, area=area.name,
    )


def floor_location_path(floor: Floor) -> LocationPathOut:
    building = floor.building
    site = building.site
    return LocationPathOut(
        site_id=site.id, site=site.name,
        building_id=building.id, building=building.name,
        floor_id=floor.id, floor=floor.name,
    )


def camera_out(camera: Camera) -> CameraOut:
    data = CameraOut.model_validate(camera)
    data.has_password = bool(camera.password_encrypted)
    data.location = location_path(camera.area)
    data.placement = PlacementOut.model_validate(camera.placement) if camera.placement else None
    return data


def floor_plan_out(plan: FloorPlan) -> FloorPlanOut:
    data = FloorPlanOut.model_validate(plan)
    data.image_url = f"/api/floor-plans/{plan.id}/image?v={plan.version}"
    data.location = floor_location_path(plan.floor)
    data.placement_count = len(plan.placements)
    data.needs_review_count = sum(
        1 for p in plan.placements if p.review_status == ReviewStatus.needs_review
    )
    return data


def site_tree(db: Session, sites) -> list[SiteOut]:
    from sqlalchemy import func, select
    from .models import Camera as CameraModel

    counts = dict(
        db.execute(
            select(CameraModel.area_id, func.count(CameraModel.id)).group_by(CameraModel.area_id)
        ).all()
    )
    out = []
    for site in sites:
        buildings = []
        for building in site.buildings:
            floors = []
            for floor in building.floors:
                floors.append(FloorOut(
                    id=floor.id, name=floor.name, building_id=building.id,
                    areas=[
                        AreaOut(id=a.id, name=a.name, floor_id=floor.id,
                                camera_count=counts.get(a.id, 0))
                        for a in floor.areas
                    ],
                    has_floor_plan=floor.floor_plan is not None,
                    floor_plan_id=floor.floor_plan.id if floor.floor_plan else None,
                ))
            buildings.append(BuildingOut(id=building.id, name=building.name,
                                         site_id=site.id, floors=floors))
        out.append(SiteOut(id=site.id, name=site.name, buildings=buildings))
    return out
