import json
from functools import lru_cache
from pathlib import Path

from app.core.config import settings
from app.game_config.schema import FieldLayoutVariant, GameConfig, Zone


def resolve_config_path() -> Path:
    path = Path(settings.game_config_path)
    if path.is_absolute():
        return path

    app_root = Path(__file__).resolve().parents[1]
    parts = list(path.parts)
    if parts and parts[0] == "app":
        path = Path(*parts[1:])
    return app_root / path


@lru_cache(maxsize=1)
def load_game_config() -> GameConfig:
    config_path = resolve_config_path()
    with config_path.open("r", encoding="utf-8") as file:
        raw = json.load(file)
    return GameConfig.model_validate(raw)


def reload_game_config() -> GameConfig:
    load_game_config.cache_clear()
    return load_game_config()


def get_field_layout_variant(perimeter_type: str) -> FieldLayoutVariant | None:
    config = load_game_config()
    if config.field_layout is None:
        return None
    normalized = (perimeter_type or "").strip().lower()
    for variant in config.field_layout.variants:
        if variant.perimeter_type == normalized:
            return variant
    return None


def point_in_polygon(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(polygon)
    j = n - 1

    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        intersects = ((yi > y) != (yj > y)) and (
            x < ((xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi)
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def classify_point(x: float, y: float) -> Zone | None:
    config = load_game_config()
    for zone in config.zones:
        polygon = [(point.x, point.y) for point in zone.polygon]
        if point_in_polygon(x, y, polygon):
            return zone
    return None
