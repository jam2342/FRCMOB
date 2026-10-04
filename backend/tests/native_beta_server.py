"""Disposable loopback API for native/browser beta integration, never production.

Uses real routers/auth/persistence and a temporary SQLite DB. Bootstrap disables
all dotenv sources before constructing Settings; no project credentials are read.
Launch with a clean environment as documented in NATIVE_BETA.md.
"""
from __future__ import annotations

import ast
import importlib.util
import os
from pathlib import Path
import sys
import tempfile


def main():
    import uvicorn
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import JSONResponse

    with tempfile.TemporaryDirectory(prefix="frcmob-beta-db-") as scratch:
        config_path = Path(__file__).resolve().parents[1] / "app/core/config.py"
        spec = importlib.util.spec_from_file_location("app.core.config", config_path)
        module = importlib.util.module_from_spec(spec)
        tree = ast.parse(config_path.read_text())
        fields = {item.target.id.lower() for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Settings" for item in node.body if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)}
        if any(name.lower() in fields for name in os.environ):
            raise RuntimeError("Use the documented clean environment; inherited app settings are forbidden.")
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "settings" for t in node.targets):
                node.value = ast.parse("Settings(_env_file=None)", mode="eval").body
        sys.modules["app.core.config"] = module
        exec(compile(ast.fix_missing_locations(tree), str(config_path), "exec"), module.__dict__)
        module._ENV_FILE_PATHS = ()
        settings = module.settings
        settings.database_url = "sqlite+pysqlite:///" + str(Path(scratch) / "beta.sqlite")
        settings.public_readonly_mode = False
        settings.enforce_admin_auth_for_writes = True
        settings.admin_api_key = "disposable-local-beta-only"
        settings.admin_session_token_secret = "disposable-local-beta-signing-secret-only"
        settings.tba_auth_key = ""

        from app.api.routes_workspaces import router as workspace_router
        from app.api.routes_tracks import router as tracks_router
        from app.api import routes_scouting_rooms
        from app.core.security import enforce_write_request_access
        from app.db import models
        from app.db.base import Base
        from app.db.session import engine, SessionLocal
        Base.metadata.create_all(engine)
        routes_scouting_rooms.scouting_room_hub._redis_presence_enabled = False
        with SessionLocal() as db:
            db.add(models.Event(event_key="2026test", name="Disposable Beta Event", year=2026))
            db.add(models.Team(team_key="frc254", team_number=254, nickname="Fixture"))
            db.add(models.Match(match_key="2026test_qm1", event_key="2026test", comp_level="qm", set_number=1, match_number=1))
            db.add(models.MatchTeam(match_key="2026test_qm1", event_key="2026test", team_key="frc254", alliance="red", station="r1"))
            db.commit()
        app = FastAPI()
        @app.middleware("http")
        async def access(request, call_next):
            try:
                enforce_write_request_access(request)
            except HTTPException as exc:
                return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
            return await call_next(request)
        app.include_router(workspace_router)
        app.include_router(tracks_router)
        @app.get("/health")
        def health():
            return {"ok": True, "disposable_beta": True}
        @app.get("/test-evidence")
        def evidence():
            with SessionLocal() as db:
                return {"sessions": db.query(models.OnDeviceSession).count(), "tracks": db.query(models.RobotTrack).count()}
        try:
            uvicorn.run(app, host="127.0.0.1", port=4182, access_log=False)
        finally:
            engine.dispose()


if __name__ == "__main__":
    main()
