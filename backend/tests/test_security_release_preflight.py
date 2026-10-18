from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _module():
    path = Path(__file__).resolve().parents[1] / "scripts/security_release_preflight.py"
    spec = importlib.util.spec_from_file_location("security_release_preflight", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ready_environment():
    return {
        "APP_ENV": "production", "STRICT_STARTUP_ENV_VALIDATION": "true",
        "ENFORCE_ADMIN_AUTH_FOR_WRITES": "true", "ON_DEVICE_SYNC_REQUIRE_SIGNED_TOKEN": "true",
        "ADMIN_API_KEY": "private-admin-marker-" + "x" * 40,
        "ADMIN_SESSION_TOKEN_SECRET": "private-session-marker-" + "y" * 40,
        "TURNSTILE_SITE_KEY": "public-site-marker", "TURNSTILE_SECRET_KEY": "private-turnstile-marker",
        "TURNSTILE_ALLOWED_HOSTNAMES": "app.example, preview.example", "CORS_ALLOW_ORIGINS": "https://app.example",
    }


def test_ready_environment_does_not_disclose_values():
    env = _ready_environment()
    checks = _module().environment_checks(env, "app.example")
    assert all(check["passed"] for check in checks)
    output = json.dumps(checks)
    assert "private-" not in output and "public-site-marker" not in output


def test_missing_challenge_and_wrong_hostname_block_release():
    env = _ready_environment()
    env["TURNSTILE_SECRET_KEY"] = ""
    env["TURNSTILE_ALLOWED_HOSTNAMES"] = "https://app.example"
    result = {check["check"]: check["passed"] for check in _module().environment_checks(env, "app.example")}
    assert result["TURNSTILE_SECRET_KEY"] is False
    assert result["TURNSTILE_ALLOWED_HOSTNAMES"] is False


def test_disabled_auth_weak_signing_and_wildcard_cors_block_release():
    env = _ready_environment()
    env.update(ENFORCE_ADMIN_AUTH_FOR_WRITES="false", ADMIN_SESSION_TOKEN_SECRET="weak", CORS_ALLOW_ORIGINS="*")
    result = {check["check"]: check["passed"] for check in _module().environment_checks(env, "app.example")}
    assert result["ENFORCE_ADMIN_AUTH_FOR_WRITES"] is False
    assert result["ADMIN_SESSION_TOKEN_SECRET"] is False
    assert result["explicit_cors_origins"] is False
