"""Location CRUD: site -> building -> floor -> area.

The wizard can create names inline, so every create is idempotent by name
within its parent.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Area, Building, Camera, Floor, Site
from ..schemas import AreaIn, AreaOut, BuildingIn, BuildingOut, FloorIn, FloorOut, InlineLocationIn, SiteIn, SiteOut
from ..security import current_operator
from ..serializers import site_tree

router = APIRouter(prefix="/api/locations", tags=["locations"], dependencies=[Depends(current_operator)])


@router.get("/tree", response_model=list[SiteOut])
def tree(db: Session = Depends(get_db)) -> list[SiteOut]:
    sites = db.scalars(select(Site).order_by(Site.name)).all()
    return site_tree(db, sites)


def _get_or_create(db: Session, model, parent_field: str | None, parent_id: int | None, name: str):
    stmt = select(model).where(model.name == name)
    if parent_field:
        stmt = stmt.where(getattr(model, parent_field) == parent_id)
    existing = db.scalars(stmt).first()
    if existing:
        return existing
    kwargs = {"name": name}
    if parent_field:
        kwargs[parent_field] = parent_id
    obj = model(**kwargs)
    db.add(obj)
    db.flush()
    return obj


@router.post("/sites", response_model=SiteOut, status_code=201)
def create_site(payload: SiteIn, db: Session = Depends(get_db)) -> SiteOut:
    site = _get_or_create(db, Site, None, None, payload.name)
    db.commit()
    return site_tree(db, [site])[0]


@router.post("/buildings", response_model=BuildingOut, status_code=201)
def create_building(payload: BuildingIn, db: Session = Depends(get_db)) -> BuildingOut:
    if not db.get(Site, payload.site_id):
        raise HTTPException(404, "Site not found.")
    building = _get_or_create(db, Building, "site_id", payload.site_id, payload.name)
    db.commit()
    return BuildingOut(id=building.id, name=building.name, site_id=building.site_id, floors=[])


@router.post("/floors", response_model=FloorOut, status_code=201)
def create_floor(payload: FloorIn, db: Session = Depends(get_db)) -> FloorOut:
    if not db.get(Building, payload.building_id):
        raise HTTPException(404, "Building not found.")
    floor = _get_or_create(db, Floor, "building_id", payload.building_id, payload.name)
    db.commit()
    return FloorOut(id=floor.id, name=floor.name, building_id=floor.building_id, areas=[],
                    has_floor_plan=floor.floor_plan is not None,
                    floor_plan_id=floor.floor_plan.id if floor.floor_plan else None)


@router.post("/areas", response_model=AreaOut, status_code=201)
def create_area(payload: AreaIn, db: Session = Depends(get_db)) -> AreaOut:
    if not db.get(Floor, payload.floor_id):
        raise HTTPException(404, "Floor not found.")
    area = _get_or_create(db, Area, "floor_id", payload.floor_id, payload.name)
    db.commit()
    return AreaOut(id=area.id, name=area.name, floor_id=area.floor_id, camera_count=0)


@router.post("/resolve", response_model=AreaOut)
def resolve_inline(payload: InlineLocationIn, db: Session = Depends(get_db)) -> AreaOut:
    """Create/find the whole site->area chain in one call (used by the wizard)."""
    site = db.get(Site, payload.site_id) if payload.site_id else None
    if site is None:
        if not payload.site:
            raise HTTPException(422, "A site is required.")
        site = _get_or_create(db, Site, None, None, payload.site.strip())

    building = db.get(Building, payload.building_id) if payload.building_id else None
    if building is None:
        if not payload.building:
            raise HTTPException(422, "A building is required.")
        building = _get_or_create(db, Building, "site_id", site.id, payload.building.strip())

    floor = db.get(Floor, payload.floor_id) if payload.floor_id else None
    if floor is None:
        if not payload.floor:
            raise HTTPException(422, "A floor is required.")
        floor = _get_or_create(db, Floor, "building_id", building.id, payload.floor.strip())

    area = db.get(Area, payload.area_id) if payload.area_id else None
    if area is None:
        if not payload.area:
            raise HTTPException(422, "An area or zone name is required.")
        area = _get_or_create(db, Area, "floor_id", floor.id, payload.area.strip())

    db.commit()
    return AreaOut(id=area.id, name=area.name, floor_id=area.floor_id, camera_count=0)


def _delete_guard(db: Session, area_ids: list[int], label: str) -> None:
    if not area_ids:
        return
    used = db.scalar(select(Camera.id).where(Camera.area_id.in_(area_ids)).limit(1))
    if used:
        raise HTTPException(409, f"This {label} still has cameras assigned. Move or delete them first.")


@router.delete("/areas/{area_id}", status_code=204, response_model=None)
def delete_area(area_id: int, db: Session = Depends(get_db)) -> None:
    area = db.get(Area, area_id)
    if not area:
        raise HTTPException(404, "Area not found.")
    _delete_guard(db, [area_id], "area")
    db.delete(area)
    db.commit()


@router.delete("/floors/{floor_id}", status_code=204, response_model=None)
def delete_floor(floor_id: int, db: Session = Depends(get_db)) -> None:
    floor = db.get(Floor, floor_id)
    if not floor:
        raise HTTPException(404, "Floor not found.")
    _delete_guard(db, [a.id for a in floor.areas], "floor")
    db.delete(floor)
    db.commit()


@router.delete("/buildings/{building_id}", status_code=204, response_model=None)
def delete_building(building_id: int, db: Session = Depends(get_db)) -> None:
    building = db.get(Building, building_id)
    if not building:
        raise HTTPException(404, "Building not found.")
    area_ids = [a.id for f in building.floors for a in f.areas]
    _delete_guard(db, area_ids, "building")
    db.delete(building)
    db.commit()


@router.delete("/sites/{site_id}", status_code=204, response_model=None)
def delete_site(site_id: int, db: Session = Depends(get_db)) -> None:
    site = db.get(Site, site_id)
    if not site:
        raise HTTPException(404, "Site not found.")
    area_ids = [a.id for b in site.buildings for f in b.floors for a in f.areas]
    _delete_guard(db, area_ids, "site")
    db.delete(site)
    db.commit()
