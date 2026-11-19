import json
import os
import sys
import tempfile
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from portai_core.agent_web import AgentWebService, _assistant_snapshot, make_handler
from portai_core.agent import AgentStore


class AgentWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspaces = self.root / "workspaces"
        self.reports = self.root / "reports"
        self.workspaces.mkdir(); self.reports.mkdir()
        self.fake_codex = self.root / "codex"
        self.fake_codex.write_text("""#!/usr/bin/env python3
import json, sys, time
if '--version' in sys.argv:
 print('codex test 1.0'); raise SystemExit
print(json.dumps({'type':'thread.started','thread_id':'web-test-session'}), flush=True)
print(json.dumps({'type':'item.updated','item':{'type':'agent_message','text':'Resposta em'}}), flush=True)
time.sleep(.08)
print(json.dumps({'type':'item.updated','item':{'type':'agent_message','text':'Resposta em streaming.'}}), flush=True)
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'Resposta em streaming.'}}), flush=True)
print(json.dumps({'type':'turn.completed'}), flush=True)
""", encoding="utf-8")
        self.fake_codex.chmod(0o700)
        self.app = SimpleNamespace(ROOT=self.root, WORKSPACES=self.workspaces, REPORTS=self.reports,
            find_workspace=lambda source: None,
            make_workspace=lambda source: self._copy(source),
            save_workspace_state=lambda *args: None,
            codex_exec_command=lambda codex, cwd, prompt, sandbox: [codex, "exec", "--cd", str(cwd), "--sandbox", sandbox, prompt],
            codex_resume_command=lambda codex, cwd, session_id=None, sandbox=None: [codex, "exec", "resume", "--cd", str(cwd), "--sandbox", sandbox or "workspace-write"],
            run_build=lambda *a, **k: (1, "", "", None))
        runtime = {"installed": True, "path": str(self.fake_codex), "version": "codex test 1.0", "runtime": "Python fixture", "authentication": "test fixture", "environment": os.environ.copy(), "connection": "test"}
        self.patcher = patch("portai_core.agent_web.inspect_codex", return_value=runtime)
        self.patcher.start()
        self.service = AgentWebService(self.app, self.root / "agent-data")

    def tearDown(self):
        self.service.shutdown()
        for worker in list(self.service.workers.values()): worker.join(timeout=3)
        self.patcher.stop(); self.temp.cleanup()

    def _copy(self, source):
        dest = self.workspaces / "copy"; dest.mkdir(exist_ok=True)
        return dest

    def test_persistent_conversation_and_streaming_codex_execution(self):
        conversation = self.service.new_conversation()
        self.service.submit(conversation["id"], "Analise o projeto vazio")
        deadline = time.time() + 5
        while time.time() < deadline:
            current = self.service.get_conversation(conversation["id"])
            if current["status"] == "idle" and current["messages"] and len(current["messages"]) > 1:
                break
            time.sleep(.03)
        self.assertEqual(current["messages"][0]["content"], "Analise o projeto vazio")
        assistant = [m for m in current["messages"] if m["role"] == "assistant"][-1]
        self.assertEqual(assistant["content"], "Resposta em streaming.")
        self.assertEqual(assistant["status"], "complete")
        events = self.service.events_after(conversation["id"], 0)
        deltas = [event["data"]["delta"] for event in events if event["type"] == "assistant_delta"]
        self.assertEqual("".join(deltas), "Resposta em streaming.")
        self.assertEqual(current["last_task"]["session_id"], "web-test-session")
        saved = self.service.conversations.load(conversation["id"])
        self.assertEqual(saved["messages"][-1]["content"], "Resposta em streaming.")
        self.assertTrue(list(self.reports.glob("*.md")))

    def test_chat_can_start_without_project_and_attach_afterward(self):
        conversation = self.service.new_conversation()
        self.assertIsNone(conversation["project_path"])
        external = tempfile.TemporaryDirectory()
        project = Path(external.name) / "external-game"; project.mkdir()
        attached = self.service.attach_project(conversation["id"], str(project))
        self.assertEqual(attached["project_path"], str(project))
        external.cleanup()

    def test_task_progress_and_stop_cancel_queued_messages(self):
        conversation = self.service.new_conversation()
        self.service.submit(conversation["id"], "Primeira solicitação")
        self.service.submit(conversation["id"], "Segunda solicitação")
        result = self.service.stop(conversation["id"])
        self.assertEqual(result["pending"], [])

    def test_ui_is_responsive_and_has_real_chat_controls(self):
        web = Path(__file__).resolve().parents[1] / "portai_agent_web"
        html = (web / "index.html").read_text()
        css = (web / "app.css").read_text()
        js = (web / "app.js").read_text()
        self.assertIn("__PORTAI_CSRF_TOKEN__", html)
        self.assertIn("id=\"composer\"", html)
        self.assertIn("id=\"stopTask\"", html)
        self.assertIn("@media(max-width:720px)", css)
        self.assertIn("new EventSource", js)
        self.assertIn("/api/conversations/", js)
        self.assertIn("Selecionar pasta", html)

    def test_codex_updated_item_snapshots_are_supported_for_streaming(self):
        event = {"type": "item.updated", "item": {"type": "agent_message", "text": "Olá"}}
        self.assertEqual(_assistant_snapshot(event), "Olá")

    def test_local_http_api_serves_chat_and_accepts_messages(self):
        token = "test-csrf-token"
        web = Path(__file__).resolve().parents[1] / "portai_agent_web"
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.service, web, token))
        import threading
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            page = urllib.request.urlopen(base + "/", timeout=2).read().decode()
            self.assertIn(token, page)
            request = urllib.request.Request(base + "/api/conversations", data=b"{}", method="POST",
                headers={"Content-Type":"application/json", "X-PortAI-Token":token})
            conversation = json.loads(urllib.request.urlopen(request, timeout=2).read())["conversation"]
            request = urllib.request.Request(base + f"/api/conversations/{conversation['id']}/messages",
                data=json.dumps({"message":"Analise"}).encode(), method="POST",
                headers={"Content-Type":"application/json", "X-PortAI-Token":token})
            self.assertEqual(urllib.request.urlopen(request, timeout=2).status, 202)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
