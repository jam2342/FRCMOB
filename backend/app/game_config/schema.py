from typing import Literal

from pydantic import BaseModel, Field, model_validator


class FieldDimensions(BaseModel):
    length_m: float = Field(gt=0)
    width_m: float = Field(gt=0)


class MatchPhases(BaseModel):
    total_sec: int = Field(gt=0)
    auto_sec: int = Field(gt=0)
    teleop_sec: int = Field(gt=0)
    endgame_sec: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_timing(self):
        if self.auto_sec + self.teleop_sec != self.total_sec:
            raise ValueError("auto_sec + teleop_sec must equal total_sec")
        if self.endgame_sec > self.teleop_sec:
            raise ValueError("endgame_sec must be <= teleop_sec")
        return self


class ShiftWindow(BaseModel):
    key: str
    start_sec: int = Field(ge=0)
    end_sec: int = Field(gt=0)
    # Which alliance's hub is active during this window. "both" = everyone can score
    # (auto / transition / endgame) and never counts as defense.
    active: Literal["red", "blue", "both", "shift_1", "opposite_shift_1"]

    @model_validator(mode="after")
    def validate_window(self):
        if self.end_sec <= self.start_sec:
            raise ValueError(f"shift window {self.key}: end_sec must be > start_sec")
        return self


class AllianceZonePair(BaseModel):
    red: str
    blue: str


class ShiftRoleZones(BaseModel):
    # Zone key a robot is expected to occupy for each role, by alliance. Used to read
    # attack vs defense vs fuel-gathering behaviour from positional tracking per shift.
    attack: AllianceZonePair
    gather: AllianceZonePair
    defense: AllianceZonePair
    climb: AllianceZonePair


class ShiftSchedule(BaseModel):
    # Match segmentation used to split offense vs defense from tracking. During a
    # robot's active shift it should attack; during the opponent's active shift it
    # should gather fuel or play defense.
    countdown_based: bool = True
    windows: list[ShiftWindow] = Field(min_length=1)
    role_zones: ShiftRoleZones

    @model_validator(mode="after")
    def validate_windows(self):
        keys = [window.key for window in self.windows]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate shift window keys")
        ordered = sorted(self.windows, key=lambda window: window.start_sec)
        if ordered[0].start_sec != 0:
            raise ValueError("shift_schedule must start at 0")
        for prev, curr in zip(ordered, ordered[1:]):
            if curr.start_sec != prev.end_sec:
                raise ValueError(
                    f"shift windows must be contiguous: gap/overlap between {prev.key} and {curr.key}"
                )
        return self


class Point(BaseModel):
    x: float
    y: float


class Zone(BaseModel):
    key: str
    label: str
    kind: Literal["scoring", "loading", "neutral", "protected", "endgame", "custom"]
    polygon: list[Point] = Field(min_length=3)
    description: str | None = None


class ScoringAction(BaseModel):
    key: str
    label: str
    phase: Literal["auto", "teleop", "endgame", "any"]
    points: float | None = None
    description: str | None = None


class GameConfig(BaseModel):
    season_year: int
    season_key: str
    season_name: str
    version: str
    field: FieldDimensions
    phases: MatchPhases
    shift_schedule: ShiftSchedule | None = None
    zones: list[Zone]
    scoring_actions: list[ScoringAction]

    @model_validator(mode="after")
    def validate_keys(self):
        def unique(values: list[str], label: str):
            if len(values) != len(set(values)):
                raise ValueError(f"Duplicate keys in {label}")

        unique([zone.key for zone in self.zones], "zones")
        unique([score.key for score in self.scoring_actions], "scoring_actions")

        if self.shift_schedule is not None:
            zone_keys = {zone.key for zone in self.zones}
            ordered = sorted(self.shift_schedule.windows, key=lambda window: window.start_sec)
            if ordered[-1].end_sec != self.phases.total_sec:
                raise ValueError("shift_schedule final window end_sec must equal phases.total_sec")
            role_zones = self.shift_schedule.role_zones
            for role_name in ("attack", "gather", "defense", "climb"):
                pair = getattr(role_zones, role_name)
                for alliance in ("red", "blue"):
                    zone_key = getattr(pair, alliance)
                    if zone_key not in zone_keys:
                        raise ValueError(
                            f"shift_schedule.role_zones.{role_name}.{alliance} references unknown zone '{zone_key}'"
                        )
        return self
