"""Repository collaboration checks; this is not a product build/test runner."""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


REPOSITORY = "Esp3ra/2026-Moble-3rd-Team-Project"
STAGES = {"Alpha", "Beta", "Release"}
TASK_PREFIXES = ("feature/", "fix/", "docs/", "chore/", "test/", "refactor/")
STABILIZATION_PREFIXES = ("fix/", "hotfix/", "docs/", "chore/", "test/")
REQUIRED_FILES = (
    "README.md", "AGENTS.md", ".gitignore", ".github/CODEOWNERS",
    ".github/pull_request_template.md", ".github/workflows/repository-policy.yml",
    "docs/COLLABORATION.md", "docs/VERSIONING.md", "docs/TEAM.md",
    "docs/prompts/codex-github-manager-prompt.txt",
    "docs/prompts/codex-github-member-prompt.txt",
)


def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], encoding="utf-8", errors="strict"
    ).strip()


def valid_task_name(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(name.startswith(p) and len(name) > len(p) for p in prefixes)


def validate_pr(base: str, head: str, head_repository: str, files: list[str]) -> None:
    if head_repository != REPOSITORY:
        raise ValueError("Fork PRs are not used; push a task branch to the team repository.")
    if base == "Alpha":
        allowed = head in {"Beta", "Release"} or valid_task_name(head, TASK_PREFIXES)
    elif base == "Beta":
        allowed = head in {"Alpha", "Release"} or valid_task_name(head, STABILIZATION_PREFIXES)
    elif base == "Release":
        allowed = head == "Beta" or valid_task_name(head, STABILIZATION_PREFIXES)
    elif base == "main":
        allowed = valid_task_name(head, ("docs/", "chore/"))
        allowed = allowed and all(is_governance_file(path) for path in files)
    else:
        allowed = False
    if not allowed:
        raise ValueError(f"Unsupported PR route: {head} -> {base}. See docs/COLLABORATION.md.")


def is_governance_file(path: str) -> bool:
    return path in {"README.md", "AGENTS.md", ".gitignore", ".gitattributes", ".editorconfig"} or path.startswith(
        ("docs/", ".github/", "scripts/")
    )


def tag_stage(name: str) -> str:
    number = r"(?:0|[1-9][0-9]*)"
    if re.fullmatch(rf"0\.0\.{number}V", name):
        return "Alpha"
    if re.fullmatch(rf"0\.[1-9][0-9]*\.{number}V", name):
        return "Beta"
    if re.fullmatch(rf"1\.{number}\.{number}V", name):
        return "Release"
    raise ValueError(f"Invalid stage version tag: {name}")


def forbidden_file(name: str) -> bool:
    path = PurePosixPath(name)
    lower = name.lower()
    parts = {part.lower() for part in path.parts}
    basename = path.name.lower()
    if basename == ".env" or (basename.startswith(".env.") and not basename.endswith(".example")):
        return True
    if parts.intersection({".git", ".venv", "venv", "node_modules", "bin", "obj", "__pycache__", "backups"}):
        return True
    return lower.endswith((".db", ".sqlite", ".sqlite3", ".bak", ".pem", ".key", ".pfx", ".p12", ".pyc")) or any(
        marker in basename for marker in (".db-", ".sqlite-", ".sqlite3-")
    )


def main() -> int:
    root = Path(git("rev-parse", "--show-toplevel"))
    os.chdir(root)
    tracked = git("ls-files", "-z").split("\0")
    for name in REQUIRED_FILES:
        if name not in tracked or not (root / name).is_file():
            raise ValueError(f"Missing tracked collaboration file: {name}")
    denied = [name for name in tracked if name and forbidden_file(name)]
    if denied:
        raise ValueError("Remove local secrets/data/generated files from Git: " + ", ".join(denied))
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    if event_name == "pull_request":
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        pr = event["pull_request"]
        base_sha, head_sha = pr["base"]["sha"], pr["head"]["sha"]
        changed = git("diff", "--name-only", "-z", f"{base_sha}...{head_sha}").split("\0")
        validate_pr(pr["base"]["ref"], pr["head"]["ref"], pr["head"]["repo"]["full_name"], [p for p in changed if p])
        subprocess.run(["git", "diff", "--check", f"{base_sha}...{head_sha}"], check=True)
        print(f"Same-repository PR policy passed: {pr['head']['ref']} -> {pr['base']['ref']}")
    elif os.environ.get("GITHUB_REF_TYPE") == "tag":
        name = os.environ["GITHUB_REF_NAME"]
        stage = tag_stage(name)
        if git("cat-file", "-t", f"refs/tags/{name}") != "tag":
            raise ValueError("Version milestones must use annotated tags.")
        subprocess.run(["git", "merge-base", "--is-ancestor", "HEAD", f"origin/{stage}"], check=True)
        print(f"Annotated tag {name} belongs to the {stage} history.")
    else:
        subprocess.run(["git", "diff", "--check"], check=True)
    print("Repository policy passed. No product build, runtime, DB or deployment validation is claimed.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f"Repository policy failed: {error}", file=sys.stderr)
        sys.exit(1)
