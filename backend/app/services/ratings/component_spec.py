# Declared scoring inputs for candidate rating models, independent of game rules.
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path

from app.services.ratings.state_inputs import digest

REGISTRY = Path(__file__).resolve().parent / "data/component_specs.json"


@dataclass(frozen=True)
class Component:
    name: str
    source: tuple[str, ...]
    level: str = "alliance"
    kind: str = "points"


@dataclass(frozen=True)
class BonusRp:
    name: str
    flag: str


@dataclass(frozen=True)
class ComponentSpec:
    season: int
    tier: str
    version: int
    components: tuple[Component, ...]
    bonus_rps: tuple[BonusRp, ...] = ()

    def __post_init__(self):
        if type(self.season) is not int or not 1992 <= self.season <= 2100 or type(self.version) is not int or self.version < 1:
            raise ValueError("invalid component season/version")
        if self.tier not in ("A", "B", "C") or not self.components:
            raise ValueError("invalid component tier")
        names = [c.name for c in self.components]
        if len(set(names)) != len(names) or "other" in names or any(not n.isidentifier() for n in names):
            raise ValueError("component names must be unique; other is reserved for the residual")
        for component in self.components:
            if component.level not in ("alliance", "robot") or component.kind != "points":
                raise ValueError("only numeric alliance/robot point components are supported")
            if not component.source or any(not path or any(not key for key in path.split(".")) for path in component.source):
                raise ValueError("components need explicit source paths")
            if component.level == "robot" and len(component.source) != 3:
                raise ValueError("robot components need three ordered sources")
        total = Component("total", ("score_without_fouls",))
        if self.tier == "A" and self.components != (total,):
            raise ValueError("Tier A is exactly score without fouls")
        if self.tier != "A" and any(c.name == "total" for c in self.components):
            raise ValueError("split components must not count the total again")
        names = [rp.name for rp in self.bonus_rps]
        flags = [rp.flag for rp in self.bonus_rps]
        if len(set(names)) != len(names) or len(set(flags)) != len(flags) or any(not n.isidentifier() for n in names):
            raise ValueError("bonus RP names and flags must be unique")
        if any(not path or any(not key for key in path.split(".")) for path in flags):
            raise ValueError("bonus RPs need explicit flag paths")

    @property
    def fingerprint(self):
        return digest(asdict(self))


def get_spec(season, *, registry=None):
    raw = json.loads(Path(registry or REGISTRY).read_text())
    if raw.get("schema") != 1:
        raise ValueError("unsupported component registry schema")
    specs = {}
    for entry in raw["seasons"]:
        value = {**entry, "components": tuple(Component(**{**c, "source": tuple(c["source"])}) for c in entry["components"]),
                 "bonus_rps": tuple(BonusRp(**rp) for rp in entry.get("bonus_rps", []))}
        spec = ComponentSpec(**value)
        if spec.season in specs:
            raise ValueError("duplicate season component declaration")
        specs[spec.season] = spec
    if season not in specs:
        raise ValueError(f"Season {season} has no declared rating component spec")
    return specs[season]


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def field(breakdown, path):
    return number(raw_field(breakdown,path))


def raw_field(breakdown, path):
    value = breakdown
    for key in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def bonus_flags(spec, breakdown):
    return {rp.name: value if type(value := raw_field(breakdown,rp.flag)) is bool else None for rp in spec.bonus_rps}


def project(spec, score, breakdown):
    score, foul = number(score), field(breakdown, "foulPoints")
    total = score-foul if score is not None and score >= 0 and foul is not None and foul >= 0 else None
    if total is not None and total < 0:
        total = None
    if spec.tier == "A":
        return {"total": total}
    out = {"total": total}
    measured = 0.0
    complete = total is not None
    for component in spec.components:
        values = [field(breakdown, path) for path in component.source]
        if component.level == "robot":
            out[component.name] = values
        else:
            out[component.name] = sum(values) if all(v is not None for v in values) else None
        if any(v is None for v in values):
            complete = False
        else:
            measured += sum(values)
    out["other"] = total-measured if complete else None
    return out
