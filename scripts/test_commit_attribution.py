import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from check_commit_attribution import identity_problem, message_problem


SOURCE = Path(__file__).resolve().parents[1]


class AttributionTests(unittest.TestCase):
    def test_human_coauthor_and_project_filename_are_allowed(self):
        self.assertIsNone(message_problem("Update CLAUDE.md\n\nCo-authored-by: Alex Example <alex@example.com>\n"))
        self.assertIsNone(identity_problem(["Alex Example", "alex@example.com"]))

    def test_ai_credits_and_named_audits_are_rejected(self):
        for message in [
            "Fix a race\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>",
            "Fix a race\n\nco-authored-by: Codex <codex@openai.com>",
            "Fix a race\n\nCo-authored-by: AI <noreply@anthropic.com>",
            "Fix the Codex second-pass audit findings",
            "Fix a race\n\nClaude-Session: https://claude.ai/code/example",
        ]:
            with self.subTest(message=message):
                self.assertIsNotNone(message_problem(message))

    def test_ai_identities_are_rejected(self):
        for identity in ["Claude", "codex[bot]", "noreply@anthropic.com"]:
            self.assertIsNotNone(identity_problem([identity]))

    def test_real_hook_and_history_check(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
            env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})

            def run(*args):
                return subprocess.run(args, cwd=repo, env=env, capture_output=True, text=True)

            self.assertEqual(run("git", "init", "-q").returncode, 0)
            self.assertEqual(run("git", "config", "user.name", "Test Maintainer").returncode, 0)
            self.assertEqual(run("git", "config", "user.email", "maintainer@example.com").returncode, 0)
            (repo / "scripts").mkdir()
            shutil.copy(SOURCE / "scripts/check_commit_attribution.py", repo / "scripts")
            shutil.copy(SOURCE / ".githooks/commit-msg", repo / ".git/hooks/commit-msg")
            (repo / ".git/hooks/commit-msg").chmod(0o755)
            accepted = run("git", "commit", "--allow-empty", "-m", "Update CLAUDE.md\n\nCo-authored-by: Alex Example <alex@example.com>")
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            rejected = run("git", "commit", "--allow-empty", "-m", "Fix a race\n\nCo-authored-by: Claude <noreply@anthropic.com>")
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("commit-msg:", rejected.stderr)
            clean = run("python3", "scripts/check_commit_attribution.py", "--history")
            self.assertEqual(clean.returncode, 0, clean.stderr)
            rejected_author = run("git", "-c", "user.name=Codex", "commit", "--allow-empty", "-m", "Fix a race")
            self.assertNotEqual(rejected_author.returncode, 0)
            self.assertIn("human author", rejected_author.stderr)
            bypassed_credit = run("git", "commit", "--allow-empty", "--no-verify", "-m", "Fix a race\n\nCo-authored-by: Codex <codex@openai.com>")
            self.assertEqual(bypassed_credit.returncode, 0, bypassed_credit.stderr)
            detected_credit = run("python3", "scripts/check_commit_attribution.py", "--history")
            self.assertNotEqual(detected_credit.returncode, 0)
            self.assertIn("AI names", detected_credit.stderr)
            bypassed = run("git", "-c", "user.name=Codex", "commit", "--allow-empty", "--no-verify", "-m", "Fix a race")
            self.assertEqual(bypassed.returncode, 0, bypassed.stderr)
            detected = run("python3", "scripts/check_commit_attribution.py", "--history")
            self.assertNotEqual(detected.returncode, 0)
            self.assertIn("human author", detected.stderr)


if __name__ == "__main__":
    unittest.main()
