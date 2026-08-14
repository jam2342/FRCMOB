from typing import Any

from app.services.game_config import load_game_config
from app.services.hash_utils import stable_payload_hash
from app.services.vision.perimeter_resolver import normalize_perimeter_type


def build_analysis_params_hash(
    *,
    analysis_version: str,
    calibration_id: int | None,
    sampling: dict[str, Any],
    tracking: dict[str, Any],
    perimeter_type: str | None = None,
) -> str:
    config = load_game_config()
    payload = {
        "analysis_version": analysis_version,
        "sampling": sampling,
        "tracking": tracking,
        "game_config": {
            "season_year": int(config.season_year),
            "version": str(config.version),
            "perimeter_type": normalize_perimeter_type(perimeter_type),
        },
        "calibration_id": calibration_id,
    }
    return stable_payload_hash(payload)
