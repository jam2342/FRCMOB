from __future__ import annotations

import re
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
FRONTEND_API_TS = ROOT_DIR / "ScoutingApp" / "src" / "api.ts"
BACKEND_API_DIR = ROOT_DIR / "backend" / "app" / "api"
BACKEND_MAIN_PY = ROOT_DIR / "backend" / "app" / "main.py"

_FRONTEND_FETCH_CALL = re.compile(
    r"(?:apiFetch|fetch)\(\s*`([^`]+)`\s*(?:,\s*(\{[\s\S]*?\}))?\s*\)",
    re.MULTILINE,
)
_FRONTEND_METHOD = re.compile(r'method\s*:\s*"(GET|POST|PUT|PATCH|DELETE)"')
_BACKEND_ROUTER_PREFIX = re.compile(r'APIRouter\(\s*prefix="([^"]*)"')
_BACKEND_ROUTE_DECORATOR = re.compile(
    r'@router\.(get|post|put|patch|delete|websocket)\("([^"]*)"\)'
)
_BACKEND_APP_DECORATOR = re.compile(r'@app\.(get|post|put|patch|delete)\("([^"]*)"\)')

def _normalize_path(path: str) -> str:
    value = str(path or "").strip()
    value = re.sub(r"\$\{[^}]+\}", "{var}", value)
    value = re.sub(r"\{[^}]+\}", "{var}", value)
    value = re.sub(r"/+", "/", value)
    value = value.rstrip("/") or "/"
    if not value.startswith("/"):
        value = f"/{value}"
    return value

def _normalize_frontend_api_path(path: str) -> str:
    value = str(path or "")
    if "?" in value:
        value = value.split("?", 1)[0]
    while True:
        suffix_match = re.search(r"\$\{[^}]+\}$", value)
        if not suffix_match:
            break
        if suffix_match.start() > 0 and value[suffix_match.start() - 1] != "/":
            value = value[: suffix_match.start()]
            continue
        break
    return _normalize_path(value)

def _frontend_http_contract() -> set[tuple[str, str]]:
    text = FRONTEND_API_TS.read_text()
    routes: set[tuple[str, str]] = set()
    for match in _FRONTEND_FETCH_CALL.finditer(text):
        template = match.group(1)
        options_blob = match.group(2) or ""
        if "${API}" not in template:
            continue
        method_match = _FRONTEND_METHOD.search(options_blob)
        method = method_match.group(1) if method_match else "GET"
        route = template.split("${API}", 1)[1]
        routes.add((method, _normalize_frontend_api_path(route)))
    return routes

_BACKEND_INCLUDE_ROUTER = re.compile(
    r"from\s+app\.api\.([\w\.]+)\s+import\s+router\s+as\s+\w+"
)

def _api_module_name(file_path: Path) -> str:
    relative = file_path.relative_to(BACKEND_API_DIR)
    parts = list(relative.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)

def _resolve_sub_router_prefixes() -> dict[str, str]:
    # Map sub-router module names to their parent's prefix.
    #
    # Scans API files for ``from app.api.X import router as ...`` followed by
    # ``router.include_router(...)`` to detect sub-routers that inherit a
    # parent's prefix.
    prefix_map: dict[str, str] = {}
    for file_path in BACKEND_API_DIR.rglob("*.py"):
        text = file_path.read_text()
        parent_prefix_match = _BACKEND_ROUTER_PREFIX.search(text)
        if not parent_prefix_match:
            continue
        parent_prefix = parent_prefix_match.group(1)
        for m in _BACKEND_INCLUDE_ROUTER.finditer(text):
            sub_module = m.group(1)
            prefix_map[sub_module] = parent_prefix
    return prefix_map

def _backend_http_contract() -> set[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    sub_prefix_map = _resolve_sub_router_prefixes()
    for file_path in BACKEND_API_DIR.rglob("*.py"):
        text = file_path.read_text()
        prefix_match = _BACKEND_ROUTER_PREFIX.search(text)
        prefix = prefix_match.group(1) if prefix_match else ""
        # If this file has no own prefix, check if it's a sub-router
        if not prefix:
            module_name = _api_module_name(file_path)
            prefix = sub_prefix_map.get(module_name, "")
        for match in _BACKEND_ROUTE_DECORATOR.finditer(text):
            method = match.group(1).upper()
            if method == "WEBSOCKET":
                continue
            suffix = match.group(2)
            routes.add((method, _normalize_path(f"{prefix}{suffix}")))
    main_text = BACKEND_MAIN_PY.read_text()
    for match in _BACKEND_APP_DECORATOR.finditer(main_text):
        method = match.group(1).upper()
        routes.add((method, _normalize_path(match.group(2))))
    return routes

def _backend_websocket_paths() -> set[str]:
    paths: set[str] = set()
    sub_prefix_map = _resolve_sub_router_prefixes()
    for file_path in BACKEND_API_DIR.rglob("*.py"):
        text = file_path.read_text()
        prefix_match = _BACKEND_ROUTER_PREFIX.search(text)
        prefix = prefix_match.group(1) if prefix_match else ""
        if not prefix:
            prefix = sub_prefix_map.get(_api_module_name(file_path), "")
        for match in _BACKEND_ROUTE_DECORATOR.finditer(text):
            method = match.group(1).upper()
            if method != "WEBSOCKET":
                continue
            suffix = match.group(2)
            paths.add(_normalize_path(f"{prefix}{suffix}"))
    return paths

def test_frontend_http_routes_exist_in_backend_contract() -> None:
    frontend_routes = _frontend_http_contract()
    backend_routes = _backend_http_contract()
    missing = sorted(frontend_routes - backend_routes)
    assert not missing, f"Frontend API routes missing in backend: {missing}"

def test_scouting_room_websocket_path_is_wired() -> None:
    frontend_text = FRONTEND_API_TS.read_text()
    assert re.search(r"scouting/rooms/\$\{[^}]+\}/ws", frontend_text), (
        "Frontend websocket URL builder path missing from api.ts"
    )
    backend_ws_paths = _backend_websocket_paths()
    assert "/scouting/rooms/{var}/ws" in backend_ws_paths, (
        "Backend websocket route missing: /scouting/rooms/{room_key}/ws"
    )
