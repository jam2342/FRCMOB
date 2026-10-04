"""Load app settings for local verification without opening any dotenv files."""
from __future__ import annotations

import ast
import importlib.util
import os
from pathlib import Path
import sys


def configure_disposable_database(database_url: str):
    if "app.core.config" in sys.modules:
        raise RuntimeError("Disposable settings must be installed before app imports.")
    config_path = Path(__file__).resolve().parents[1] / "app/core/config.py"
    spec = importlib.util.spec_from_file_location("app.core.config", config_path)
    module = importlib.util.module_from_spec(spec)
    tree = ast.parse(config_path.read_text())
    fields = {
        item.target.id.lower()
        for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Settings"
        for item in node.body
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
    }
    if any(name.lower() in fields for name in os.environ):
        raise RuntimeError("Inherited app settings are forbidden; use a clean environment.")
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "settings" for target in node.targets
        ):
            node.value = ast.parse("Settings(_env_file=None)", mode="eval").body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "_ENV_FILE_PATHS":
                node.value = ast.parse("()", mode="eval").body
    sys.modules["app.core.config"] = module
    exec(compile(ast.fix_missing_locations(tree), str(config_path), "exec"), module.__dict__)
    settings = module.settings
    settings.database_url = database_url
    settings.public_readonly_mode = False
    settings.enforce_admin_auth_for_writes = True
    settings.admin_api_key = "disposable-local-beta-only"
    settings.admin_session_token_secret = "disposable-local-beta-signing-secret-only"
    settings.tba_auth_key = ""
    return settings
