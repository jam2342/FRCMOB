from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.security import require_write_access
from app.db import models
from app.db.session import get_db
from app.services.game_config import classify_point, get_field_layout_variant, load_game_config, reload_game_config
from app.services.vision.perimeter_resolver import resolve_perimeter_type_for_event_profile

router = APIRouter(prefix="/game-config", tags=["game-config"])


class ZoneClassifyRequest(BaseModel):
    x: float
    y: float


@router.get("")
def get_game_config():
    config = load_game_config()
    return {"ok": True, "config": config.model_dump()}


@router.get("/zones")
def get_zones():
    config = load_game_config()
    return {"ok": True, "zones": [zone.model_dump() for zone in config.zones]}


@router.get("/event-labels")
def get_event_labels():
    config = load_game_config()
    return {"ok": True, "event_labels": [event.model_dump() for event in config.event_labels]}


@router.get("/core-metrics")
def get_core_metrics():
    config = load_game_config()
    return {"ok": True, "core_metrics": [metric.model_dump() for metric in config.core_metrics]}


@router.get("/field-layout")
def get_field_layout(
    event_key: str | None = None,
    match_key: str | None = None,
    db: Session = Depends(get_db),
):
    config = load_game_config()
    field_layout = config.field_layout
    if field_layout is None:
        return {
            "ok": True,
            "field_layout": None,
            "active_perimeter_type": None,
            "perimeter_resolution": None,
            "active_variant": None,
        }

    resolved_event_key = event_key
    resolution_source = "default:no_scope"
    warnings: list[str] = []

    if match_key:
        match = db.get(models.Match, match_key)
        if match is None:
            warnings.append(f"match_not_found:{match_key}")
        else:
            resolved_event_key = match.event_key

    if resolved_event_key:
        profile = db.get(models.EventProfile, resolved_event_key)
        active_perimeter_type, resolution_source = resolve_perimeter_type_for_event_profile(profile)
    else:
        active_perimeter_type = "welded"

    active_variant = get_field_layout_variant(active_perimeter_type)
    if active_variant is None:
        active_variant = field_layout.variants[0]
        active_perimeter_type = active_variant.perimeter_type
        resolution_source = f"{resolution_source}|fallback:first_variant"

    return {
        "ok": True,
        "field_layout": field_layout.model_dump(),
        "active_perimeter_type": active_perimeter_type,
        "perimeter_resolution": {
            "event_key": resolved_event_key,
            "match_key": match_key,
            "source": resolution_source,
        },
        "active_variant": active_variant.model_dump(),
        "warnings": warnings,
    }


@router.post("/reload")
def reload_config():
    require_write_access("Game config reload")
    config = reload_game_config()
    return {"ok": True, "config": config.model_dump()}


@router.post("/classify-point")
def classify_zone(payload: ZoneClassifyRequest):
    zone = classify_point(payload.x, payload.y)
    return {
        "ok": True,
        "x": payload.x,
        "y": payload.y,
        "zone": zone.model_dump() if zone else None,
    }
