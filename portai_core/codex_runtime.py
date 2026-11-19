"""Codex CLI discovery and safe runtime diagnostics for Termux/Linux."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from .agent import redact_secrets


_LINKER_FAILURE = re.compile(r"CANNOT LINK EXECUTABLE|cannot locate symbol|cannot load library", re.I)


def _run(command: list[str], env: dict[str, str], timeout: int = 8) -> subprocess.CompletedProcess:
    return subprocess.run(command, env=env, capture_output=True, text=True, timeout=timeout, check=False)


def resolve_codex(path: str | None = None) -> str | None:
    """Return an absolute executable path without resolving its symlink target."""
    candidate = os.path.expanduser(path) if path else shutil.which("codex")
    if not candidate:
        return None
    resolved = candidate if os.path.isabs(candidate) else (shutil.which(candidate) or candidate)
    return os.path.abspath(resolved) if resolved and os.path.isfile(resolved) else None


def inspect_codex(path: str | None = None) -> dict:
    """Probe Codex with inherited auth; retry a confirmed Termux loader collision."""
    executable = resolve_codex(path)
    result = {
        "installed": bool(executable), "path": executable, "version": None,
        "runtime": None, "authentication": "not checked", "connection": "not tested",
        "error": None, "environment": os.environ.copy(), "environment_adjustment": None,
    }
    env = os.environ.copy()
    auth_file = Path(env.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser() / "auth.json"
    if auth_file.is_file():
        result["authentication"] = "credential file found (validity not tested)"
    elif any(env.get(key) for key in ("OPENAI_API_KEY", "CODEX_API_KEY")):
        result["authentication"] = "credential environment variable found (value hidden)"
    else:
        result["authentication"] = "no local credential detected"
    if not executable:
        result["error"] = "Codex CLI não foi encontrado no PATH."
        result["environment"] = env
        return result

    try:
        probe = _run([executable, "--version"], env)
        output = (probe.stdout + probe.stderr).strip()
        # The Termux Codex package launcher starts Node via its shebang. In some
        # parent environments LD_LIBRARY_PATH is set for Codex's bundled ELF,
        # which makes Termux Node fail before the launcher's own sanitization.
        if probe.returncode and _LINKER_FAILURE.search(output) and env.get("LD_LIBRARY_PATH"):
            retry_env = env.copy()
            retry_env.pop("LD_LIBRARY_PATH", None)
            retry = _run([executable, "--version"], retry_env)
            retry_output = (retry.stdout + retry.stderr).strip()
            if retry.returncode == 0:
                env = retry_env
                probe = retry
                result["environment_adjustment"] = "Removed conflicting LD_LIBRARY_PATH after confirmed loader failure."
            else:
                output = retry_output or output
        result["environment"] = env
        if probe.returncode:
            result["error"] = output[:1200] or f"Codex version probe exited {probe.returncode}."
            return result
        result["version"] = (probe.stdout or probe.stderr).strip()[:300]
        result["connection"] = "CLI starts; connectivity not tested"
        result["error"] = None
        result["runtime"] = _runtime_info(executable, env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _runtime_info(executable: str, env: dict[str, str]) -> str:
    try:
        with Path(executable).open("rb") as stream:
            first = stream.readline(512).decode("utf-8", "replace").strip()
        if first.startswith("#!"):
            parts = first[2:].split()
            interpreter = None
            if parts:
                interpreter = shutil.which(parts[1], path=env.get("PATH")) if Path(parts[0]).name == "env" and len(parts) > 1 else parts[0]
            if interpreter and Path(interpreter).name.startswith("node"):
                run = _run([interpreter, "--version"], env)
                if run.returncode == 0:
                    return f"Node.js {run.stdout.strip()} ({interpreter})"
                return f"Node.js runtime failed: {(run.stderr or run.stdout).strip()[:300]}"
            if interpreter and "python" in Path(interpreter).name:
                run = _run([interpreter, "--version"], env)
                return f"Python {run.stdout.strip() or run.stderr.strip()} ({interpreter})" if run.returncode == 0 else f"Python runtime ({interpreter})"
            return f"Interpreter {interpreter}" if interpreter else first[2:]
    except OSError:
        pass
    return "native executable"


def test_connection(diagnostic: dict, workspace: Path, timeout: int = 60) -> dict:
    """Make one real, read-only Codex request in an isolated empty workspace."""
    executable = diagnostic.get("path")
    env = diagnostic.get("environment") or os.environ.copy()
    if not executable or not diagnostic.get("version"):
        return {"ok": False, "message": diagnostic.get("error") or "Codex CLI is unavailable."}
    workspace = workspace.resolve(strict=True)
    prompt = "Reply with exactly: PORTAI_CODEX_CONNECTION_OK. Do not inspect other paths or create files."
    command = [executable, "exec", "--cd", str(workspace), "--skip-git-repo-check",
               "--sandbox", "read-only", "--json", prompt]
    try:
        proc = subprocess.run(command, cwd=str(workspace), env=env, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
    output = (proc.stdout + "\n" + proc.stderr).strip()
    # Keep diagnostics bounded and never echo environment values or auth data.
    tail = redact_secrets("\n".join(output.splitlines()[-20:])[-6000:])
    ok = proc.returncode == 0 and "PORTAI_CODEX_CONNECTION_OK" in output
    return {"ok": ok, "returncode": proc.returncode, "message": tail or f"Codex exited {proc.returncode}."}
