from typing import Literal, Protocol

PerimeterType = Literal["welded", "andymark"]

# FE-2026 sheet 2 district/regional perimeter mapping encoded into deterministic
# state/country rules using metadata we already store in EventProfile.
STATE_RULES: dict[str, PerimeterType] = {
    # AndyMark districts
    "tx": "andymark",
    "in": "andymark",
    "nc": "andymark",
    "wi": "andymark",
    # NE FIRST states
    "ct": "andymark",
    "me": "andymark",
    "ma": "andymark",
    "nh": "andymark",
    "ri": "andymark",
    "vt": "andymark",
    # Chesapeake district states
    "md": "andymark",
    "va": "andymark",
    "dc": "andymark",
    # Welded districts
    "ca": "welded",
    "mi": "welded",
    "sc": "welded",
    # Mid-Atlantic district states
    "nj": "welded",
    "pa": "welded",
    "de": "welded",
}

STATE_ALIASES: dict[str, str] = {
    "california": "ca",
    "connecticut": "ct",
    "delaware": "de",
    "district of columbia": "dc",
    "indiana": "in",
    "maine": "me",
    "maryland": "md",
    "massachusetts": "ma",
    "michigan": "mi",
    "new hampshire": "nh",
    "new jersey": "nj",
    "north carolina": "nc",
    "pennsylvania": "pa",
    "rhode island": "ri",
    "south carolina": "sc",
    "texas": "tx",
    "vermont": "vt",
    "virginia": "va",
    "wisconsin": "wi",
}

COUNTRY_RULES: dict[str, PerimeterType] = {
    # Regionals / regions from FE-2026 sheet 2
    "australia": "welded",
    "brazil": "andymark",
    "canada": "welded",
    "china": "andymark",
    "israel": "welded",
    "mexico": "andymark",
    "turkiye": "andymark",
    "turkey": "andymark",
}

COUNTRY_ALIASES: dict[str, str] = {
    "united states": "usa",
    "united states of america": "usa",
    "u.s.": "usa",
    "u.s.a.": "usa",
    "us": "usa",
    "usa": "usa",
    "türkiye": "turkiye",
}


class EventProfileLike(Protocol):
    state_prov: str | None
    country: str | None


def normalize_perimeter_type(value: str | None, default: PerimeterType = "welded") -> PerimeterType:
    normalized = (value or "").strip().lower()
    if normalized == "andymark":
        return "andymark"
    if normalized == "welded":
        return "welded"
    return default


def _normalize_state(state_prov: str | None) -> str:
    raw = (state_prov or "").strip().lower()
    if not raw:
        return ""
    if raw in STATE_ALIASES:
        return STATE_ALIASES[raw]
    if len(raw) == 2:
        return raw
    return raw


def _normalize_country(country: str | None) -> str:
    raw = (country or "").strip().lower()
    if not raw:
        return ""
    if raw in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[raw]
    return raw


def resolve_perimeter_type_for_event_profile(
    event_profile: EventProfileLike | None,
) -> tuple[PerimeterType, str]:
    if event_profile is None:
        return "welded", "default:no_event_profile"

    state = _normalize_state(event_profile.state_prov)
    country = _normalize_country(event_profile.country)

    if state in STATE_RULES:
        return STATE_RULES[state], f"state:{state}"

    if country in COUNTRY_RULES:
        return COUNTRY_RULES[country], f"country:{country}"

    if country in {"", "usa"}:
        return "welded", "default:usa_regional"

    return "welded", "default:global_fallback"
