"""Loopback web chat for PORTAI AGENT, backed by the existing Codex agent."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import webbrowser
from collections import defaultdict, deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .agent import AgentOrchestrator, AgentStore, classify_request, codex_json_event
from .analyzer import analyze_project
from .codex_runtime import inspect_codex, test_connection
from .platforms import PLATFORM_INDEX
from .projects import load_projects, register_project
from .reports import write_report

_CONVERSATION_ID = re.compile(r"^[0-9a-f]{32}$")
_MAX_MESSAGE = 50_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ConversationStore:
    """Owner-private conversation metadata; task/session data stays in AgentStore."""
    def __init__(self, store: AgentStore):
        self.store = store
        self.root = store.root / "conversations"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()

    def _path(self, conversation_id: str) -> Path:
        if not _CONVERSATION_ID.fullmatch(conversation_id or ""):
            raise ValueError("ID de conversa inválido")
        return self.root / f"{conversation_id}.json"

    def create(self) -> dict:
        now = _now()
        conversation = {
            "id": uuid.uuid4().hex, "title": "Nova conversa", "project_path": None,
            "workspace": None, "messages": [], "pending": [], "task_ids": [],
            "last_task_id": None, "active_task_id": None, "active_message_id": None,
            "status": "idle", "created_at": now, "updated_at": now,
        }
        self.save(conversation)
        return conversation

    def load(self, conversation_id: str) -> dict:
        return json.loads(self._path(conversation_id).read_text(encoding="utf-8"))

    def save(self, conversation: dict) -> None:
        with self.lock:
            conversation["updated_at"] = _now()
            self.store._write(self._path(conversation["id"]), conversation)

    def list(self, limit: int = 100) -> list[dict]:
        result = []
        for path in sorted(self.root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                result.append({key: value.get(key) for key in ("id", "title", "project_path", "status", "updated_at")})
            except (OSError, ValueError):
                continue
            if len(result) >= limit:
                break
        return result


class AgentWebService:
    """Conversation API and serialized per-conversation task runner."""
    def __init__(self, app: Any, data_home: Path):
        self.app = app
        self.store = AgentStore(data_home)
        self.conversations = ConversationStore(self.store)
        self.orchestrator = AgentOrchestrator(self.store, app.WORKSPACES, self.store.root / "backups")
        self.runtime = inspect_codex()
        self.lock = threading.RLock()
        self.workers: dict[str, threading.Thread] = {}
        self.stop_events: dict[str, threading.Event] = {}
        self.event_seq: dict[str, int] = defaultdict(int)
        self.events: dict[str, deque] = defaultdict(lambda: deque(maxlen=500))
        for task in self.store.recover():
            self.publish(task.get("conversation_id", ""), "task", {"task_id": task["id"], "status": "interrupted"})
        self._recover_conversations()

    def _recover_conversations(self) -> None:
        for row in self.conversations.list():
            try:
                conversation = self.conversations.load(row["id"])
            except (OSError, ValueError):
                continue
            active = conversation.get("active_task_id")
            if active:
                try:
                    task = self.store.load(active)
                except (OSError, ValueError):
                    task = {}
                if task.get("status") == "interrupted":
                    conversation.update({"active_task_id": None, "active_message_id": None, "status": "interrupted"})
                    self.conversations.save(conversation)

    def publish(self, conversation_id: str, kind: str, payload: dict) -> None:
        if not conversation_id:
            return
        with self.lock:
            self.event_seq[conversation_id] += 1
            self.events[conversation_id].append({"id": self.event_seq[conversation_id], "type": kind, "data": payload})

    def events_after(self, conversation_id: str, after: int) -> list[dict]:
        with self.lock:
            return [event for event in self.events[conversation_id] if event["id"] > after]

    def public_runtime(self) -> dict:
        return {key: value for key, value in self.runtime.items() if key != "environment"}

    def new_conversation(self) -> dict:
        conversation = self.conversations.create()
        self.publish(conversation["id"], "conversation", {"status": "idle"})
        return conversation

    def get_conversation(self, conversation_id: str) -> dict:
        conversation = self.conversations.load(conversation_id)
        with self.lock:
            conversation["event_cursor"] = self.event_seq[conversation_id]
        if conversation.get("active_task_id"):
            try:
                task = self.store.load(conversation["active_task_id"])
                conversation["active_task"] = {key: task.get(key) for key in ("id", "status", "stage", "target", "tail_log")}
            except (OSError, ValueError):
                conversation["active_task"] = None
        if conversation.get("last_task_id"):
            try:
                task = self.store.load(conversation["last_task_id"])
                conversation["last_task"] = {key: task.get(key) for key in ("id", "status", "stage", "session_id", "target")}
            except (OSError, ValueError):
                conversation["last_task"] = None
        return conversation

    def attach_project(self, conversation_id: str, project_text: str) -> dict:
        project = Path(project_text).expanduser().resolve(strict=True)
        if not project.is_dir():
            raise ValueError("Selecione uma pasta de projeto existente.")
        if project == self.app.ROOT.resolve() or self.app.ROOT.resolve() in project.parents:
            raise ValueError("Selecione um projeto fora da pasta do OffTec PortAI.")
        register_project(self.app.ROOT, project)
        conversation = self.conversations.load(conversation_id)
        conversation["project_path"] = str(project)
        conversation["workspace"] = None
        self.conversations.save(conversation)
        self.publish(conversation_id, "project", {"path": str(project), "name": project.name})
        return conversation

    def submit(self, conversation_id: str, message: str) -> dict:
        message = message.strip()
        if not message or len(message) > _MAX_MESSAGE:
            raise ValueError(f"A mensagem precisa conter entre 1 e {_MAX_MESSAGE} caracteres.")
        with self.lock:
            conversation = self.conversations.load(conversation_id)
            user_message = {"id": uuid.uuid4().hex, "role": "user", "content": message, "created_at": _now(), "status": "queued"}
            conversation["messages"].append(user_message)
            conversation["messages"] = conversation["messages"][-2000:]
            if conversation["title"] == "Nova conversa":
                conversation["title"] = message.replace("\n", " ")[:72] or "Nova conversa"
            conversation["pending"].append({"message_id": user_message["id"], "content": message})
            conversation["status"] = "running" if conversation.get("active_task_id") else "queued"
            self.conversations.save(conversation)
            self.publish(conversation_id, "message", user_message)
            self.publish(conversation_id, "conversation", {"status": conversation["status"]})
            self._ensure_worker_locked(conversation_id)
            return self.get_conversation(conversation_id)

    def resume(self, conversation_id: str) -> dict:
        conversation = self.conversations.load(conversation_id)
        if not conversation.get("last_task_id"):
            raise ValueError("Esta conversa ainda não tem uma tarefa para retomar.")
        task = self.store.load(conversation["last_task_id"])
        if task.get("status") != "interrupted" or not task.get("session_id"):
            raise ValueError("A última tarefa não tem uma sessão Codex interrompida que possa ser retomada.")
        return self.submit(conversation_id, "Continue a tarefa interrompida usando a sessão Codex salva. Retome do estado atual, confira o que já foi feito e prossiga com o objetivo original.")

    def stop(self, conversation_id: str) -> dict:
        with self.lock:
            event = self.stop_events.get(conversation_id)
            if event:
                event.set()
            conversation = self.conversations.load(conversation_id)
            cancelled = conversation.get("pending", [])
            conversation["pending"] = []
            for item in cancelled:
                for message in conversation["messages"]:
                    if message.get("id") == item.get("message_id"):
                        message["status"] = "cancelled"
            self.conversations.save(conversation)
            self.publish(conversation_id, "conversation", {"status": "stopping", "cancelled": len(cancelled)})
            return conversation

    def doctor(self) -> dict:
        self.runtime = inspect_codex(self.runtime.get("path"))
        if not self.runtime.get("version"):
            return {"runtime": self.public_runtime(), "connection": {"ok": False, "message": self.runtime.get("error") or "Codex não está disponível."}}
        with tempfile.TemporaryDirectory(prefix="codex-check-", dir=self.store.root) as directory:
            connection = test_connection(self.runtime, Path(directory))
        self.runtime["connection"] = "passed" if connection["ok"] else "failed"
        self.runtime["last_test"] = self.runtime["connection"]
        self.runtime["last_test_at"] = _now()
        self.runtime["last_test_message"] = connection["message"]
        self.store._write(self.store.root / "web-runtime.json", {key: value for key, value in self.runtime.items() if key != "environment"})
        return {"runtime": self.public_runtime(), "connection": connection}

    def _ensure_worker_locked(self, conversation_id: str) -> None:
        current = self.workers.get(conversation_id)
        if current and current.is_alive():
            return
        stop = threading.Event()
        self.stop_events[conversation_id] = stop
        worker = threading.Thread(target=self._worker_loop, args=(conversation_id, stop),
                                  name=f"portai-web-{conversation_id[:8]}", daemon=True)
        self.workers[conversation_id] = worker
        worker.start()

    def _workspace_for(self, conversation: dict) -> tuple[Path, Path]:
        project_text = conversation.get("project_path")
        if project_text:
            project = Path(project_text).resolve(strict=True)
            workspace = self.app.find_workspace(project)
            if workspace is None:
                workspace = self.app.make_workspace(project)
                self.app.save_workspace_state(workspace, {"source_path": str(project), "workspace": str(workspace),
                    "status": "PORTAI AGENT web workspace", "session_id": None, "analysis_done": False, "history": []})
            return project, workspace.resolve(strict=True)
        root = self.app.WORKSPACES.resolve() / ".portai-agent-conversations"
        workspace = root / conversation["id"]
        workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        return workspace, workspace

    def _worker_loop(self, conversation_id: str, stop: threading.Event) -> None:
        while True:
            with self.lock:
                try:
                    conversation = self.conversations.load(conversation_id)
                except (OSError, ValueError):
                    self.workers.pop(conversation_id, None)
                    return
                if not conversation.get("pending"):
                    conversation.update({"status": "interrupted" if stop.is_set() else "idle",
                                         "active_task_id": None, "active_message_id": None})
                    self.conversations.save(conversation)
                    self.publish(conversation_id, "conversation", {"status": conversation["status"]})
                    self.workers.pop(conversation_id, None)
                    self.stop_events.pop(conversation_id, None)
                    return
                request = conversation["pending"].pop(0)
                conversation.update({"status": "running", "active_message_id": request["message_id"]})
                for message in conversation["messages"]:
                    if message.get("id") == request["message_id"]:
                        message["status"] = "running"
                self.conversations.save(conversation)
            try:
                self._run_request(conversation_id, request, stop)
            except Exception as exc:
                self._finish_error(conversation_id, request, str(exc))
            if stop.is_set():
                with self.lock:
                    try:
                        conversation = self.conversations.load(conversation_id)
                        conversation["pending"] = []
                        for message in conversation["messages"]:
                            if message.get("status") == "queued":
                                message["status"] = "cancelled"
                        self.conversations.save(conversation)
                    except (OSError, ValueError):
                        pass
                    self.workers.pop(conversation_id, None)
                    self.stop_events.pop(conversation_id, None)
                return

    def _run_request(self, conversation_id: str, request: dict, stop: threading.Event) -> None:
        if not self.runtime.get("path") or not self.runtime.get("version"):
            raise RuntimeError(self.runtime.get("error") or "Codex não está disponível. Use Verificar conexão.")
        conversation = self.conversations.load(conversation_id)
        project, workspace = self._workspace_for(conversation)
        if conversation.get("project_path"):
            static = analyze_project(project)
            from portai_agent_ui import _diagnose, _source_platform
            source = _source_platform(request["content"], static)
            routing = classify_request(request["content"])
            target = routing["target"]
            diagnostics = _diagnose(project, request["content"], source, target, static)
        else:
            static = analyze_project(workspace)
            routing = classify_request(request["content"])
            source = "unknown"
            target = routing["target"]
            diagnostics = {"project_analysis": static, "request": routing, "game_analysis": {"engine": "Unknown"},
                           "environment": {"host": os.uname().machine if hasattr(os, "uname") else "unknown"}}
        previous = self.store.load(conversation["last_task_id"]) if conversation.get("last_task_id") else None
        task = self.orchestrator.prepare(request["content"], project, workspace, diagnostics,
                                         source=source, target=target, previous=previous)
        task["conversation_id"] = conversation_id
        self.store.save(task)
        assistant = {"id": uuid.uuid4().hex, "role": "assistant", "content": "", "created_at": _now(),
                     "status": "streaming", "task_id": task["id"]}
        with self.lock:
            conversation = self.conversations.load(conversation_id)
            conversation["messages"].append(assistant)
            conversation["task_ids"].append(task["id"])
            conversation.update({"active_task_id": task["id"], "last_task_id": task["id"], "workspace": str(workspace)})
            self.conversations.save(conversation)
        self.publish(conversation_id, "assistant_start", assistant)
        self.publish(conversation_id, "task", {"id": task["id"], "status": "running", "stage": "Preparando diagnóstico"})
        streamed_text = []
        streamed_snapshot = ""

        def on_output(line: str) -> None:
            nonlocal streamed_snapshot
            event = codex_json_event(line)
            if event:
                stage = event.get("type")
                if stage:
                    self.publish(conversation_id, "task", {"id": task["id"], "status": "running", "stage": str(stage)[:100]})
                delta = _assistant_delta(event)
                snapshot = _assistant_snapshot(event)
                if snapshot is not None:
                    delta = snapshot[len(streamed_snapshot):] if snapshot.startswith(streamed_snapshot) else ""
                    streamed_snapshot = snapshot
                if delta:
                    from .agent import redact_secrets
                    delta = redact_secrets(delta)
                    streamed_text.append(delta)
                    self._append_assistant(conversation_id, assistant["id"], delta)
                    self.publish(conversation_id, "assistant_delta", {"message_id": assistant["id"], "delta": delta})
                label = _event_label(event)
                if label:
                    self.publish(conversation_id, "log", {"task_id": task["id"], "message": label})
            elif line.strip():
                self.publish(conversation_id, "log", {"task_id": task["id"], "message": line[:400]})

        result = self.orchestrator.run(task["id"], self.runtime["path"],
            codex_exec_builder=self.app.codex_exec_command, codex_resume_builder=self.app.codex_resume_command,
            on_output=on_output, stop_event=stop, user_approved=True, env=self.runtime.get("environment"))
        if result.get("status") == "completed" and diagnostics.get("request", {}).get("intent") == "build":
            build = self._run_build(workspace, target, conversation_id, task["id"], stop)
            result = self.store.load(task["id"])
            result["build"] = build
            result["status"] = "completed" if build.get("status") == "succeeded" else build.get("status") if build.get("status") in {"interrupted", "timeout"} else "failed" if build.get("status") == "failed" else "blocked"
            result["stage"] = "build finished" if build.get("status") == "succeeded" else "build unavailable"
            result["result"] = {**(result.get("result") or {}), "build": build}
            self.store.save(result)
        from portai_agent_ui import _friendly_answer
        answer = _friendly_answer(result)
        if result.get("build"):
            answer += f"\n\nBuild Manager: {result['build'].get('status')} — {result['build'].get('reason', '')}"
        if streamed_text and result.get("status") == "completed":
            # Prefer the completed assistant message if the CLI did not expose text deltas.
            if not "".join(streamed_text).strip():
                streamed_text.clear()
        self._replace_assistant(conversation_id, assistant["id"], answer,
                                "interrupted" if result.get("status") == "interrupted" else "complete" if result.get("status") == "completed" else result.get("status", "failed"))
        report = {"project": str(project), "source": source, "target": target, "objective": request["content"],
                  "diagnostics": diagnostics, "task": result, "result": result.get("result"), "changed_files": result.get("changed_files")}
        markdown, structured = write_report(report, self.app.REPORTS)
        result["report"] = {"markdown": str(markdown), "json": str(structured)}
        self.store.save(result)
        with self.lock:
            conversation = self.conversations.load(conversation_id)
            conversation.update({"last_task_id": task["id"], "active_task_id": None,
                                 "active_message_id": None, "status": result.get("status", "failed")})
            for message in conversation["messages"]:
                if message.get("id") == request["message_id"]:
                    message["status"] = "complete" if result.get("status") == "completed" else result.get("status")
            self.conversations.save(conversation)
        self.publish(conversation_id, "assistant_replace", {"message_id": assistant["id"], "content": answer,
                                                            "status": result.get("status"), "task_id": task["id"]})
        self.publish(conversation_id, "task", {"id": task["id"], "status": result.get("status"),
                                                "stage": result.get("stage"), "report": result.get("report")})

    def _run_build(self, workspace: Path, target: str, conversation_id: str, task_id: str, stop: threading.Event) -> dict:
        def output(line: str) -> None:
            self.publish(conversation_id, "log", {"task_id": task_id, "message": line[:1000]})
        if target == "android":
            code, stdout, stderr, apk = self.app.run_build(workspace, on_output=output, stop_event=stop)
            status = "succeeded" if code == 0 and apk else "interrupted" if code == 130 else "timeout" if code == 124 else "failed"
            return {"status": status, "returncode": code, "stdout": stdout[-12000:], "stderr": stderr[-12000:],
                    "artifact": str(apk) if apk else None}
        if target in {"windows", "linux"}:
            from .build import run_build
            return run_build(workspace, on_output=output, stop_event=stop)
        return {"status": "not_run", "reason": "Destino sem build local verificável ou SDK autorizado não detectado."}

    def _append_assistant(self, conversation_id: str, message_id: str, delta: str) -> None:
        with self.lock:
            conversation = self.conversations.load(conversation_id)
            for message in conversation["messages"]:
                if message.get("id") == message_id:
                    message["content"] = (message.get("content", "") + delta)[-_MAX_MESSAGE:]
                    break
            self.conversations.save(conversation)

    def _replace_assistant(self, conversation_id: str, message_id: str, content: str, status: str) -> None:
        with self.lock:
            conversation = self.conversations.load(conversation_id)
            for message in conversation["messages"]:
                if message.get("id") == message_id:
                    message["content"] = content[-_MAX_MESSAGE:]
                    message["status"] = status
                    break
            self.conversations.save(conversation)

    def _finish_error(self, conversation_id: str, request: dict, error: str) -> None:
        from .agent import redact_secrets
        safe_error = redact_secrets(error)[:1200]
        assistant = {"id": uuid.uuid4().hex, "role": "assistant", "content": safe_error,
                     "created_at": _now(), "status": "failed"}
        try:
            with self.lock:
                conversation = self.conversations.load(conversation_id)
                conversation["messages"].append(assistant)
                conversation.update({"status": "failed", "active_task_id": None, "active_message_id": None})
                for message in conversation["messages"]:
                    if message.get("id") == request.get("message_id"):
                        message["status"] = "failed"
                self.conversations.save(conversation)
            self.publish(conversation_id, "assistant_start", assistant)
            self.publish(conversation_id, "assistant_replace", {"message_id": assistant["id"], "content": safe_error, "status": "failed"})
        except (OSError, ValueError):
            pass

    def projects(self) -> list[dict]:
        return [{"name": row.get("name", "Projeto"), "path": row.get("path", "")}
                for row in load_projects(self.app.ROOT) if Path(row.get("path", "")).is_dir()][:100]

    def shutdown(self) -> None:
        with self.lock:
            for event in self.stop_events.values():
                event.set()


def _assistant_delta(event: dict) -> str:
    kind = str(event.get("type", ""))
    if kind in {"response.output_text.delta", "output_text.delta", "agent_message.delta"}:
        value = event.get("delta") or event.get("text")
        return value if isinstance(value, str) else ""
    if kind == "item.delta":
        item = event.get("item") or {}
        if item.get("type") in {"agent_message", "assistant_message", "message"}:
            value = event.get("delta") or event.get("text") or item.get("delta")
            return value if isinstance(value, str) else ""
    return ""


def _assistant_snapshot(event: dict) -> str | None:
    """Extract text from Codex item updates, which may arrive as snapshots."""
    if event.get("type") not in {"item.updated", "item.completed"}:
        return None
    item = event.get("item") or {}
    if item.get("type") not in {"agent_message", "assistant_message", "message"}:
        return None
    if isinstance(item.get("text"), str):
        return item["text"]
    content = item.get("content")
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict) and isinstance(part.get("text"), str))
    return None


def _event_label(event: dict) -> str | None:
    kind = str(event.get("type", ""))
    if kind in {"thread.started", "turn.started"}:
        return "Codex iniciou uma etapa."
    if kind == "item.started":
        item = event.get("item") or {}
        if item.get("type") == "command_execution":
            return "Codex iniciou uma ferramenta."
    if kind == "item.completed":
        item = event.get("item") or {}
        if item.get("type") == "command_execution":
            return "Ferramenta concluída."
        if item.get("type") == "error":
            return str(item.get("message", "Codex reportou um erro."))[:500]
    if kind == "turn.completed":
        return "Etapa Codex concluída."
    return None


class _AgentHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_handler(service: AgentWebService, web_root: Path, token: str):
    class AgentHandler(BaseHTTPRequestHandler):
        server_version = "PORTAI-AGENT/1"

        def log_message(self, format: str, *args) -> None:
            return

        def _json(self, value: Any, status: int = 200) -> None:
            data = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 100_000:
                raise ValueError("Corpo da solicitação excede o limite.")
            try:
                value = json.loads(self.rfile.read(length) or b"{}")
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("Corpo JSON inválido.") from exc
            if not isinstance(value, dict):
                raise ValueError("Formato JSON inválido.")
            return value

        def _local_request(self) -> bool:
            host = self.headers.get("Host", "").split(":", 1)[0].strip("[]").lower()
            return host in {"127.0.0.1", "localhost"}

        def _api(self, method: str) -> None:
            if not self._local_request():
                self._json({"error": "A interface aceita somente conexões locais."}, 403)
                return
            if method == "POST" and self.headers.get("X-PortAI-Token") != token:
                self._json({"error": "Token local inválido."}, 403)
                return
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            try:
                if method == "GET" and path == "/api/bootstrap":
                    self._json({"runtime": service.public_runtime(), "conversations": service.conversations.list(),
                                "projects": service.projects()})
                elif method == "GET" and path == "/api/projects":
                    self._json({"projects": service.projects()})
                elif method == "GET" and path == "/api/conversations":
                    self._json({"conversations": service.conversations.list()})
                elif method == "POST" and path == "/api/conversations":
                    self._json({"conversation": service.new_conversation()}, 201)
                elif path.startswith("/api/conversations/"):
                    parts = path.split("/")
                    if len(parts) not in {4, 5}:
                        self._json({"error": "Rota inválida."}, 404); return
                    conversation_id = parts[3]
                    if method == "GET" and len(parts) == 4:
                        self._json({"conversation": service.get_conversation(conversation_id)})
                    elif method == "POST" and len(parts) == 5 and parts[4] == "messages":
                        body = self._body()
                        self._json({"conversation": service.submit(conversation_id, str(body.get("message", "")))}, 202)
                    elif method == "POST" and len(parts) == 5 and parts[4] == "project":
                        body = self._body()
                        value = service.attach_project(conversation_id, str(body.get("path", "")))
                        self._json({"conversation": value})
                    elif method == "POST" and len(parts) == 5 and parts[4] == "stop":
                        self._json({"conversation": service.stop(conversation_id)})
                    elif method == "POST" and len(parts) == 5 and parts[4] == "resume":
                        self._json({"conversation": service.resume(conversation_id)}, 202)
                    elif method == "GET" and len(parts) == 5 and parts[4] == "events":
                        self._events(conversation_id, parse_qs(parsed.query))
                    else:
                        self._json({"error": "Rota não encontrada."}, 404)
                elif method == "POST" and path == "/api/doctor":
                    self._json(service.doctor())
                elif method == "POST" and path == "/api/shutdown":
                    self._json({"stopping": True})
                    service.shutdown()
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                else:
                    self._json({"error": "Rota não encontrada."}, 404)
            except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                self._json({"error": str(exc)[:1000]}, 400)

        def _events(self, conversation_id: str, query: dict) -> None:
            if not self._local_request():
                self._json({"error": "A interface aceita somente conexões locais."}, 403); return
            try:
                service.conversations.load(conversation_id)
            except (OSError, ValueError):
                self._json({"error": "Conversa não encontrada."}, 404); return
            try: after = int(self.headers.get("Last-Event-ID") or (query.get("after") or ["0"])[0])
            except ValueError: after = 0
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            deadline = time.monotonic() + 25
            try:
                while time.monotonic() < deadline:
                    events = service.events_after(conversation_id, after)
                    for event in events:
                        after = event["id"]
                        payload = json.dumps(event, ensure_ascii=False)
                        self.wfile.write(f"id: {after}\nevent: update\ndata: {payload}\n\n".encode("utf-8"))
                    if events:
                        self.wfile.flush()
                    else:
                        self.wfile.write(b": heartbeat\n\n"); self.wfile.flush()
                        time.sleep(.5)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

        def do_GET(self) -> None:
            if self.path.startswith("/api/"):
                self._api("GET"); return
            if not self._local_request():
                self.send_error(403); return
            path = urlparse(self.path).path
            filename = "index.html" if path in {"/", "/index.html"} else path.lstrip("/")
            if filename not in {"index.html", "app.css", "app.js"}:
                self.send_error(404); return
            try: data = (web_root / filename).read_bytes()
            except OSError:
                self.send_error(404); return
            if filename == "index.html":
                data = data.replace(b"__PORTAI_CSRF_TOKEN__", token.encode("ascii"))
            content_type = "text/html; charset=utf-8" if filename.endswith(".html") else "text/css; charset=utf-8" if filename.endswith(".css") else "text/javascript; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            self._api("POST")

    return AgentHandler


def serve_agent_web(app, console, web_root: Path | None = None) -> None:
    """Open the browser chat and keep the local server alive until it is closed there."""
    web_root = web_root or Path(__file__).resolve().parents[1] / "portai_agent_web"
    service = AgentWebService(app, Path(os.environ.get("PORTAI_AGENT_HOME", str(Path.home() / ".local/share/offtec-portai"))))
    token = uuid.uuid4().hex
    server = _AgentHTTPServer(("127.0.0.1", 0), make_handler(service, web_root, token))
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    thread = threading.Thread(target=server.serve_forever, name="portai-agent-web-server", daemon=True)
    thread.start()
    opened = False
    termux_open = shutil.which("termux-open-url")
    if termux_open:
        try:
            subprocess.Popen([termux_open, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            opened = True
        except OSError:
            pass
    if not opened:
        try: opened = bool(webbrowser.open(url))
        except webbrowser.Error: opened = False
    console.print(f"[bold bright_magenta]PORTAI AGENT[/]  {url}")
    console.print("A conversa abrirá no navegador. Encerre pelo botão ‘Sair do agente’ na interface ou com Ctrl+C aqui.")
    if not opened:
        console.print("[yellow]Abra o endereço local acima no navegador do Android.[/]")
    try:
        while thread.is_alive(): thread.join(timeout=.5)
    except KeyboardInterrupt:
        service.shutdown()
        server.shutdown()
    finally:
        service.shutdown()
        server.server_close()
