#!/usr/bin/env python3
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess  # nosec B404
import sys
import tempfile
import time
from typing import Any

import httpx
import websockets


BACKEND_DIR = Path(__file__).resolve().parents[1]
HOST = "127.0.0.1"
PORT_A = 8100
PORT_B = 8101


class BackendProcess:
    def __init__(self, port: int) -> None:
        self.port = port
        self.proc: subprocess.Popen[str] | None = None
        self.log_file = tempfile.NamedTemporaryFile(  # noqa: SIM115 - kept open for subprocess output.
            prefix=f"backend-{port}-",
            suffix=".log",
            delete=False,
        )

    def start(self) -> None:
        env = os.environ.copy()
        env.update(
            {
                "DATABASE_URL": env.get(
                    "DATABASE_URL",
                    "postgresql+psycopg://postgres:postgres@localhost:5432/frc",
                ),
                "REDIS_URL": env.get("REDIS_URL", "redis://localhost:6379/0"),
                "CORS_ALLOW_ORIGINS": "http://localhost:5173,http://localhost:3000",
                "ENFORCE_ADMIN_AUTH_FOR_WRITES": "false",
                "AUTOMATION_REGIONAL_ENABLED": "false",
                "INTEL_SNAPSHOT_REFRESH_ENABLED": "false",
                "MEDIA_CLEANUP_ENABLED": "false",
                "STORAGE_CLEANUP_ENABLED": "false",
                "SCOUTING_ROOMS_REDIS_PUBSUB_ENABLED": "true",
                "LOG_LEVEL": "INFO",
            }
        )
        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            HOST,
            "--port",
            str(self.port),
            "--log-level",
            "info",
        ]
        self.proc = subprocess.Popen(  # nosec B603
            cmd,
            cwd=str(BACKEND_DIR),
            env=env,
            stdout=self.log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )

    def stop(self) -> None:
        if self.proc is None:
            return
        if self.proc.poll() is not None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=4)

    def read_log_tail(self, lines: int = 80) -> str:
        try:
            path = Path(self.log_file.name)
            content = path.read_text(encoding="utf-8", errors="replace").splitlines()
            return "\n".join(content[-lines:])
        except Exception:
            return "<log unavailable>"


async def wait_for_health(base_url: str, *, timeout_sec: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_sec
    async with httpx.AsyncClient(timeout=2.0) as client:
        while time.monotonic() < deadline:
            try:
                response = await client.get(f"{base_url}/health")
                if response.status_code == 200:
                    return
            except Exception as exc:
                print(f"Health probe failed for {base_url}: {exc}", file=sys.stderr)
            await asyncio.sleep(0.25)
    raise RuntimeError(f"Timed out waiting for health at {base_url}")


async def recv_until(
    websocket: websockets.WebSocketClientProtocol,
    *,
    predicate,
    timeout_sec: float,
    label: str,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_sec
    seen: list[str] = []
    while time.monotonic() < deadline:
        remaining = max(0.1, deadline - time.monotonic())
        raw = await asyncio.wait_for(websocket.recv(), timeout=remaining)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        payload = json.loads(raw)
        message_type = str(payload.get("type") or "")
        if message_type:
            seen.append(message_type)
        if predicate(payload):
            return payload
    raise RuntimeError(f"Timed out waiting for {label}; saw message types: {seen}")


async def run() -> None:
    base_a = f"http://{HOST}:{PORT_A}"
    base_b = f"http://{HOST}:{PORT_B}"

    process_a = BackendProcess(PORT_A)
    process_b = BackendProcess(PORT_B)
    process_a.start()
    process_b.start()

    try:
        await wait_for_health(base_a)
        await wait_for_health(base_b)

        room_key = f"e2e-{secrets.token_hex(4)}"
        async with httpx.AsyncClient(timeout=8.0) as client:
            created = await client.post(
                f"{base_a}/scouting/rooms",
                json={
                    "room_key": room_key,
                    "title": "multi instance e2e",
                    "scout_profile": "SmokeTest",
                },
            )
            created.raise_for_status()

        ws_a_url = f"ws://{HOST}:{PORT_A}/scouting/rooms/{room_key}/ws?scout_profile=ScoutA&client_id=client-a"
        ws_b_url = f"ws://{HOST}:{PORT_B}/scouting/rooms/{room_key}/ws?scout_profile=ScoutB&client_id=client-b"

        async with AsyncExitStack() as stack:
            ws_a = await stack.enter_async_context(websockets.connect(ws_a_url, open_timeout=8.0))
            ws_b = await stack.enter_async_context(websockets.connect(ws_b_url, open_timeout=8.0))

            await recv_until(
                ws_a,
                predicate=lambda payload: payload.get("type") == "snapshot",
                timeout_sec=8.0,
                label="snapshot on instance A",
            )
            await recv_until(
                ws_b,
                predicate=lambda payload: payload.get("type") == "snapshot",
                timeout_sec=8.0,
                label="snapshot on instance B",
            )

            entry_id_a = f"entry-a-{secrets.token_hex(3)}"
            entry_id_b = f"entry-b-{secrets.token_hex(3)}"

            async with httpx.AsyncClient(timeout=8.0) as client:
                save_a = await client.post(
                    f"{base_a}/scouting/rooms/{room_key}/entries",
                    json={
                        "scout_profile": "ScoutA",
                        "entry": {
                            "id": entry_id_a,
                            "team_key": "frc118",
                            "match_key": "2026test_qm1",
                            "points": {"total": 12},
                        },
                        "client_entry_id": entry_id_a,
                    },
                )
                save_a.raise_for_status()

            await recv_until(
                ws_a,
                predicate=lambda payload: payload.get("type") == "entry_saved"
                and (payload.get("entry") or {}).get("id") == entry_id_a,
                timeout_sec=8.0,
                label="entry_saved on instance A websocket",
            )
            await recv_until(
                ws_b,
                predicate=lambda payload: payload.get("type") == "entry_saved"
                and (payload.get("entry") or {}).get("id") == entry_id_a,
                timeout_sec=8.0,
                label="cross-instance entry_saved on instance B websocket",
            )

            async with httpx.AsyncClient(timeout=8.0) as client:
                save_b = await client.post(
                    f"{base_b}/scouting/rooms/{room_key}/entries",
                    json={
                        "scout_profile": "ScoutB",
                        "entry": {
                            "id": entry_id_b,
                            "team_key": "frc148",
                            "match_key": "2026test_qm2",
                            "points": {"total": 18},
                        },
                        "client_entry_id": entry_id_b,
                    },
                )
                save_b.raise_for_status()

            await recv_until(
                ws_b,
                predicate=lambda payload: payload.get("type") == "entry_saved"
                and (payload.get("entry") or {}).get("id") == entry_id_b,
                timeout_sec=8.0,
                label="entry_saved on instance B websocket",
            )
            await recv_until(
                ws_a,
                predicate=lambda payload: payload.get("type") == "entry_saved"
                and (payload.get("entry") or {}).get("id") == entry_id_b,
                timeout_sec=8.0,
                label="cross-instance entry_saved on instance A websocket",
            )

        print(
            json.dumps(
                {
                    "ok": True,
                    "room_key": room_key,
                    "instances": [
                        {"base_url": base_a, "port": PORT_A},
                        {"base_url": base_b, "port": PORT_B},
                    ],
                    "checks": [
                        "instance_a_local_broadcast",
                        "instance_b_receives_a",
                        "instance_b_local_broadcast",
                        "instance_a_receives_b",
                    ],
                },
                indent=2,
            )
        )
    finally:
        process_a.stop()
        process_b.stop()
        if process_a.proc and process_a.proc.returncode not in (None, 0, -signal.SIGTERM):
            print("\n[instance A log tail]\n" + process_a.read_log_tail(), file=sys.stderr)
        if process_b.proc and process_b.proc.returncode not in (None, 0, -signal.SIGTERM):
            print("\n[instance B log tail]\n" + process_b.read_log_tail(), file=sys.stderr)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        print(f"Smoke test failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
