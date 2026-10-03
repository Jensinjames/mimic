"""OpenAI-hosted engineering sandbox for iterative work on a local codebase."""
from __future__ import annotations

import base64
import fnmatch
import io
import os
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = os.environ.get("MIMIC_AGENT_MODEL", "gpt-6-astra")
DEFAULT_CONTAINER_SIZE = os.environ.get("MIMIC_AGENT_CONTAINER_SIZE", "medium")
DEFAULT_MAX_FILE_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_ARCHIVE_BYTES = 5 * 1024 * 1024

_EXCLUDED_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".mimic-agent", "dist", "build", ".ssh",
    ".aws", ".gnupg",
}
_EXCLUDED_NAMES = {
    ".env", ".npmrc", ".pypirc", "id_rsa", "id_ed25519", "credentials.json",
    "service-account.json", "service_account.json",
}
_EXCLUDED_GLOBS = (
    ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*credentials*.json",
    "*service-account*.json", "*service_account*.json",
)


@dataclass
class AgentRunResult:
    output_text: str
    report_path: Path
    patch_path: Path
    workspace_zip_path: Path
    patch_applied: bool = False


def _should_include(rel: Path) -> bool:
    if any(part in _EXCLUDED_DIRS for part in rel.parts[:-1]):
        return False
    name = rel.name
    return name not in _EXCLUDED_NAMES and not any(
        fnmatch.fnmatch(name, pattern) for pattern in _EXCLUDED_GLOBS
    )


def build_workspace_zip(root, *, max_file_bytes=DEFAULT_MAX_FILE_BYTES,
                        max_archive_bytes=DEFAULT_MAX_ARCHIVE_BYTES):
    """Build a filtered project archive suitable for an inline sandbox upload."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError(f"workspace is not a directory: {root}")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            rel = path.relative_to(root)
            if not _should_include(rel) or path.stat().st_size > max_file_bytes:
                continue
            zf.write(path, rel.as_posix())
            if buffer.tell() > max_archive_bytes:
                raise ValueError("workspace archive exceeds the 5 MiB inline upload limit")
    archive = buffer.getvalue()
    if len(archive) > max_archive_bytes:
        raise ValueError("workspace archive exceeds the 5 MiB inline upload limit")
    return archive


def _agent_input(task):
    return f"""You are mimic-agent, a software-engineering worker operating only inside /workspace/project.

Goal:
{task}

Required workflow:
1. Inspect the project before changing it; identify architecture, tests, and the smallest reliable change.
2. Reproduce failures where possible. Prefer local inspection/tests over assumptions.
3. Modify only files under /workspace/project. Keep changes focused and maintainable.
4. Run relevant tests/static checks and report failures honestly.
5. Network access is disabled. Do not search for credentials or unrelated system data.
6. Before finishing create all three files:
   - /workspace/outputs/mimic-agent-report.md
   - /workspace/outputs/mimic-agent.patch from `git diff --binary HEAD -- .`
   - /workspace/outputs/mimic-agent-workspace.zip containing the modified project without .git
7. Read the report and patch back before finishing. If code changed, the patch must be non-empty.

Do the work now. Keep the final text summary concise; artifacts are authoritative.
"""


def _openai_client():
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("OpenAI SDK is missing; reinstall mimic-client") from exc
    return OpenAI(max_retries=3, timeout=300.0)


def _download_artifact(artifacts, remote_path, local_path):
    local_path = Path(local_path)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        artifacts.download(remote_path, to=str(local_path))
    except Exception as exc:
        raise RuntimeError(f"mimic-agent did not publish {remote_path}: {exc}") from exc
    if not local_path.exists():
        raise RuntimeError(f"artifact download produced no file: {remote_path}")
    return local_path


def _apply_patch(root, patch_path):
    if patch_path.stat().st_size == 0:
        return False
    if not (root / ".git").exists():
        raise RuntimeError("--apply requires --path to be a Git working tree")
    if shutil.which("git") is None:
        raise RuntimeError("--apply requires git on PATH")
    check = subprocess.run(
        ["git", "apply", "--check", str(patch_path)], cwd=root,
        capture_output=True, text=True,
    )
    if check.returncode != 0:
        raise RuntimeError(f"generated patch did not apply cleanly:\n{check.stderr.strip()}")
    subprocess.run(["git", "apply", str(patch_path)], cwd=root, check=True)
    return True


def run_agent(task, *, path=".", model=DEFAULT_MODEL,
              container_size=DEFAULT_CONTAINER_SIZE, out_dir=".mimic-agent",
              apply=False, client=None):
    """Run a software task in a network-disabled OpenAI-hosted sandbox."""
    if not task.strip():
        raise ValueError("agent task cannot be empty")
    if container_size not in {"small", "medium", "large"}:
        raise ValueError("container_size must be small, medium, or large")

    root = Path(path).resolve()
    archive = build_workspace_zip(root)
    client = client or _openai_client()
    environment = {
        "type": "openai_hosted",
        "container_size": container_size,
        "network": {"access": "disabled"},
        "packages": {"python": ["pytest>=8,<9", "openai>=3.24,<4", "requests>=2.28"]},
        "files": [{
            "type": "inline",
            "path": "/workspace/project.zip",
            "data": base64.b64encode(archive).decode("ascii"),
        }],
        "setup_commands": [
            {"command": "mkdir -p /workspace/project /workspace/outputs"},
            {"command": "python -c \"import zipfile; zipfile.ZipFile('/workspace/project.zip').extractall('/workspace/project')\""},
            {"command": "git init -q", "cwd": "/workspace/project"},
            {"command": "git config user.email mimic-agent@localhost", "cwd": "/workspace/project"},
            {"command": "git config user.name mimic-agent", "cwd": "/workspace/project"},
            {"command": "git add -A && git commit -qm baseline", "cwd": "/workspace/project"},
            {"command": "python -m pip install -e . --no-deps --no-build-isolation", "cwd": "/workspace/project"},
        ],
    }
    try:
        stream = client.beta.agents.sessions.create(
            agent={"model": model}, environment=environment,
            input=_agent_input(task), stream=True,
        )
        with stream.with_result_collection():
            for _event in stream:
                pass
            result = stream.get_final_result()
    except Exception as exc:
        raise RuntimeError(f"mimic-agent failed: {exc}") from exc

    artifacts = client.beta.agents.sessions.artifacts.for_result(result)
    out = Path(out_dir).resolve()
    report = _download_artifact(artifacts, "/workspace/outputs/mimic-agent-report.md", out / "mimic-agent-report.md")
    patch = _download_artifact(artifacts, "/workspace/outputs/mimic-agent.patch", out / "mimic-agent.patch")
    workspace = _download_artifact(artifacts, "/workspace/outputs/mimic-agent-workspace.zip", out / "mimic-agent-workspace.zip")
    if report.stat().st_size == 0 or workspace.stat().st_size == 0:
        raise RuntimeError("mimic-agent published an empty required artifact")
    applied = _apply_patch(root, patch) if apply else False
    return AgentRunResult(
        output_text=getattr(result, "output_text", "") or "",
        report_path=report,
        patch_path=patch,
        workspace_zip_path=workspace,
        patch_applied=applied,
    )
