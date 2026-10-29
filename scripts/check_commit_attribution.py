import argparse
from pathlib import Path
import re
import subprocess
import sys


AI_NAME = re.compile(r"\b(?:claude|codex)\b|@anthropic\.com", re.IGNORECASE)
SESSION = re.compile(r"^\s*Claude-Session:", re.IGNORECASE | re.MULTILINE)


def git(*args):
    return subprocess.check_output(["git", *args], text=True, encoding="utf-8", errors="replace")


def message_problem(message):
    # A filename reference describes the change; it is not an authorship credit.
    text = re.sub(r"\bCLAUDE\.md\b", "project guide", message, flags=re.IGNORECASE)
    if AI_NAME.search(text) or SESSION.search(text):
        return "remove AI names, credits and session trailers from the commit message"
    return None


def identity_problem(identities):
    if any(AI_NAME.search(identity) for identity in identities):
        return "use a human author and committer identity"
    return None


def main():
    parser = argparse.ArgumentParser(description="Reject AI attribution while preserving human co-authors.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--message-file", type=Path)
    mode.add_argument("--history", action="store_true")
    args = parser.parse_args()
    failures = []
    if args.message_file:
        problem = message_problem(args.message_file.read_text(encoding="utf-8"))
        problem = problem or identity_problem([git("var", "GIT_AUTHOR_IDENT"), git("var", "GIT_COMMITTER_IDENT")])
        if problem:
            failures.append(f"commit-msg: {problem}")
    else:
        fields = git("log", "--all", "--format=%H%x00%an%x00%ae%x00%cn%x00%ce%x00%B%x00").split("\0")
        if fields[-1].strip() or (len(fields) - 1) % 6:
            raise ValueError("unexpected git log record format")
        count = (len(fields) - 1) // 6
        for offset in range(0, len(fields) - 1, 6):
            sha, author, author_email, committer, committer_email, message = fields[offset:offset + 6]
            problem = message_problem(message) or identity_problem([author, author_email, committer, committer_email])
            if problem:
                failures.append(f"{sha.strip()[:12]}: {problem}")
        if not failures:
            print(f"Attribution check passed ({count} commits).")
    for failure in failures:
        print(failure, file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
