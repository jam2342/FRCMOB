from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest


class RealtimeMultiInstanceE2ETests(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("RUN_MULTI_INSTANCE_REDIS_E2E") == "1",
        "Set RUN_MULTI_INSTANCE_REDIS_E2E=1 to run multi-instance Redis pub/sub smoke test.",
    )
    def test_multi_instance_pubsub_smoke(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "smoke_scouting_rooms_multi_instance.py"
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(Path(__file__).resolve().parents[1]),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            self.fail(
                "multi-instance smoke failed\n"
                f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
            )


if __name__ == "__main__":
    unittest.main()
