import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import portai
import portai_agent_ui
from portai_core.agent import AgentOrchestrator, AgentStore, classify_request, make_prompt, stream_process
from portai_core.codex_runtime import inspect_codex

class AgentTests(unittest.TestCase):
    def setUp(self): self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
    def tearDown(self): self.temp.cleanup()

    def test_store_persists_private_tasks_and_recovers_running_work(self):
        project=self.root/"game"; project.mkdir(); workspace=self.root/"workspaces"/"game"; workspace.mkdir(parents=True)
        store=AgentStore(self.root/"data"); task=store.create(name="Analyze",project=project,workspace=workspace,message="Analyze project",target="android",diagnostics={"engine":"Godot"})
        task.update(status="running",session_id="codex-session-1"); store.save(task)
        self.assertEqual(store.load(task["id"])["diagnostics"]["engine"],"Godot")
        self.assertEqual(store.recover()[0]["status"],"interrupted")
        self.assertEqual(store.load(task["id"])["session_id"],"codex-session-1")
        self.assertEqual(stat.S_IMODE(store.task_path(task["id"]).stat().st_mode)&0o077,0)

    def test_intent_routing_and_quick_actions(self):
        self.assertEqual(classify_request("Quero portar meu jogo de PS2 para Android"),{"intent":"port","target":"android"})
        self.assertEqual(classify_request("Corrija erros para Nintendo Switch"),{"intent":"fix","target":"switch"})
        self.assertEqual(classify_request("Compile este projeto para Linux"),{"intent":"build","target":"linux"})
        self.assertEqual(len(portai_agent_ui.QUICK_ACTIONS),10)

    def test_prompt_contains_workspace_scope_and_security_rules(self):
        task={"objective":"prepare Android","project":"/src/game","workspace":"/workspace/game","source":"ps2","target":"android","diagnostics":{"engine":"Godot"}}
        prompt=make_prompt(task)
        self.assertIn("authorized workspace",prompt); self.assertIn("Godot",prompt)
        self.assertIn("never assume a proprietary SDK exists",prompt)

    def test_streaming_process_delivers_live_lines(self):
        lines=[]; result=stream_process([sys.executable,"-u","-c","print('planning',flush=True); print('done',flush=True)"],self.root,on_output=lines.append)
        self.assertEqual(result["returncode"],0); self.assertEqual(lines,["planning","done"]); self.assertFalse(result["interrupted"])

    def test_codex_runtime_retries_only_after_confirmed_termux_linker_error(self):
        from portai_core import codex_runtime
        failed=type("Result",(),{"returncode":1,"stdout":"","stderr":"CANNOT LINK EXECUTABLE node: cannot locate symbol"})()
        passed=type("Result",(),{"returncode":0,"stdout":"codex-cli 1.2.3\n","stderr":""})()
        node=type("Result",(),{"returncode":0,"stdout":"v26.0.0\n","stderr":""})()
        with patch.dict("os.environ",{"LD_LIBRARY_PATH":"/termux/codex/bin"}), \
             patch.object(codex_runtime,"resolve_codex",return_value="/usr/bin/codex"), \
             patch.object(codex_runtime,"_runtime_info",return_value="Node.js v26.0.0 (/usr/bin/node)"), \
             patch.object(codex_runtime,"_run",side_effect=[failed,passed,node]) as runner:
            result=inspect_codex()
        self.assertEqual(result["version"],"codex-cli 1.2.3")
        self.assertNotIn("LD_LIBRARY_PATH",result["environment"])
        self.assertEqual(result["environment_adjustment"],"Removed conflicting LD_LIBRARY_PATH after confirmed loader failure.")
        self.assertIn("Node.js v26.0.0",result["runtime"])
        self.assertEqual(runner.call_count,2)

    def test_codex_diagnostic_does_not_log_credential_values(self):
        from portai_core.codex_runtime import test_connection
        secret="sk-"+"a"*24
        executable=self.root/"codex-output"; executable.write_text(f"#!/bin/sh\nprintf '%s\\n' 'API_KEY={secret}'")
        executable.chmod(0o700)
        result=test_connection({"path":str(executable),"version":"fixture","environment":{}},self.root)
        self.assertNotIn(secret,result["message"])
        self.assertIn("[REDACTED]",result["message"])

    def test_streaming_process_can_be_interrupted(self):
        stop=threading.Event(); timer=threading.Timer(.3,stop.set); timer.start()
        try: result=stream_process([sys.executable,"-u","-c","import time; print('ready',flush=True); time.sleep(15)"],self.root,stop_event=stop)
        finally: timer.cancel()
        self.assertTrue(result["interrupted"]); self.assertEqual(result["output"],"ready")

    def test_streaming_process_marks_timeout_without_fake_completion(self):
        result=stream_process([sys.executable,"-u","-c","import time; print('started',flush=True); time.sleep(2)"],self.root,timeout=1)
        self.assertTrue(result["timed_out"]); self.assertNotEqual(result["returncode"],0)

    def test_orchestrator_authorization_backup_stream_and_codex_session(self):
        project=self.root/"project"; project.mkdir(); workspace=self.root/"workspaces"/"copy"; workspace.mkdir(parents=True)
        (workspace/"main.cpp").write_text("int main() {}")
        store=AgentStore(self.root/"data"); orchestrator=AgentOrchestrator(store,self.root/"workspaces",self.root/"backups")
        task=orchestrator.prepare("Analyze",project,workspace,{"facts":["C++"]},target="android")
        with self.assertRaises(PermissionError): orchestrator.run(task["id"],"codex",codex_exec_builder=lambda *a:["x"],codex_resume_builder=lambda *a:["x"])
        commands=[]
        def builder(exe,cwd,prompt,sandbox):
            commands.append((prompt,sandbox))
            return [sys.executable,"-u","-c","import json; print(json.dumps({'thread_id':'saved-session'})); print('agent answer')","placeholder"]
        result=orchestrator.run(task["id"],"codex",codex_exec_builder=builder,codex_resume_builder=lambda *a:[sys.executable,"-c","print('resume')"],user_approved=True)
        self.assertEqual(result["status"],"completed"); self.assertEqual(result["session_id"],"saved-session")
        self.assertTrue(Path(result["backup"]).is_file()); self.assertIn("authorized_workspace",commands[0][0]); self.assertEqual(commands[0][1],"workspace-write")

    def test_followup_resumes_saved_codex_thread_and_keeps_project_memory(self):
        project=self.root/"project"; project.mkdir(); workspace=self.root/"workspaces"/"copy"; workspace.mkdir(parents=True)
        store=AgentStore(self.root/"data"); engine=AgentOrchestrator(store,self.root/"workspaces",self.root/"backups")
        previous=store.create(name="First",project=project,workspace=workspace,message="Analyze",diagnostics={"facts":["SDL"]})
        previous.update(session_id="codex-thread",result={"summary":"SDL project"},changed_files=["PORTAI_AGENT_REPORT.md"]); store.save(previous)
        task=engine.prepare("Now prepare Android",project,workspace,{"facts":["CMake"]},target="android",previous=previous)
        self.assertEqual(task["session_id"],"codex-thread"); self.assertEqual(task["project_memory"]["result"]["summary"],"SDL project")
        resume_calls=[]
        result=engine.run(task["id"],"codex",codex_exec_builder=lambda *a:self.fail("new session not expected"),
            codex_resume_builder=lambda *args:(resume_calls.append(args) or [sys.executable,"-u","-c","print('resumed answer')"]),user_approved=True)
        self.assertEqual(result["status"],"completed"); self.assertEqual(resume_calls[0][2],"codex-thread")

    def test_internal_diagnostics_select_project_analyzer_and_compatibility(self):
        project=self.root/"source"; project.mkdir()
        (project/"CMakeLists.txt").write_text("project(game)\nadd_executable(game main.cpp)\n")
        (project/"main.cpp").write_text("#include <windows.h>\nD3D11CreateDevice();")
        static=portai_agent_ui.analyze_project(project)
        source=portai_agent_ui._source_platform("Prepare this for Android",static)
        diagnostics=portai_agent_ui._diagnose(project,"Prepare this for Android",source,"android",static)
        self.assertEqual(source,"windows"); self.assertEqual(diagnostics["compatibility"]["target"],"android")
        self.assertEqual(diagnostics["project_analysis"]["build_systems"],["CMakeLists.txt"])

    def test_agent_welcome_and_missing_codex_message(self):
        from rich.console import Console
        from io import StringIO
        output=StringIO(); previous=portai.console; portai.console=Console(file=output,width=36,force_terminal=False,color_system=None)
        try:
            with patch.object(portai_agent_ui,"DATA_HOME",self.root/"data"), \
                 patch.object(portai_agent_ui,"inspect_codex",return_value={"installed":False,"path":None,"version":None,"runtime":None,"authentication":"no local credential detected","connection":"not tested","error":"Codex CLI não foi encontrado no PATH.","environment":{}}), \
                 patch.object(portai_agent_ui.Prompt,"ask",return_value="/quit"):
                portai_agent_ui.open_agent(portai)
        finally: portai.console=previous
        self.assertIn("Olá! Sou o PortAI",output.getvalue()); self.assertIn("não foi encontrado no PATH",output.getvalue())

    def test_agent_doctor_rechecks_and_runs_real_connection_probe_hook(self):
        from rich.console import Console
        from io import StringIO
        missing={"installed":False,"path":None,"version":None,"runtime":None,"authentication":"no local credential detected","connection":"not tested","error":"Codex indisponível","environment":{}}
        ready={"installed":True,"path":"/usr/bin/codex","version":"codex-cli test","runtime":"Node.js test","authentication":"credential file found (validity not tested)","connection":"CLI starts; connectivity not tested","error":None,"environment":{}}
        output=StringIO(); previous=portai.console; portai.console=Console(file=output,width=72,force_terminal=False,color_system=None)
        try:
            with patch.object(portai_agent_ui,"DATA_HOME",self.root/"data"), \
                 patch.object(portai_agent_ui,"inspect_codex",side_effect=[missing,ready]), \
                 patch.object(portai_agent_ui,"test_codex_connection",return_value={"ok":True,"message":"PORTAI_CODEX_CONNECTION_OK"}) as probe, \
                 patch.object(portai_agent_ui.Prompt,"ask",side_effect=["/doctor","/quit"]):
                portai_agent_ui.open_agent(portai)
        finally: portai.console=previous
        probe.assert_called_once()
        self.assertIn("PORTAI_CODEX_CONNECTION_OK",output.getvalue())
        self.assertIn("conectada",output.getvalue())
        saved=portai_agent_ui._read_session(AgentStore(self.root/"data"))["codex_last_test"]
        self.assertEqual(saved["status"],"passed")
        self.assertNotIn("environment",saved)

    def test_agent_ui_executes_real_codex_process_and_persists_reported_answer(self):
        from rich.console import Console
        from io import StringIO
        from portai_core import codex_runtime
        project=self.root/"DemoGame"; project.mkdir(); (project/"main.cpp").write_text("int main() {}")
        executable=self.root/"codex-fixture"
        executable.write_text("#!"+sys.executable+"\nimport json,sys\nif '--version' in sys.argv: print('codex fixture 1')\nelse:\n print(json.dumps({'type':'thread.started','thread_id':'real-fixture-session'}),flush=True)\n print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'Analysis complete with evidence'}}),flush=True)\n")
        executable.chmod(executable.stat().st_mode|stat.S_IXUSR)
        output=StringIO(); old_console=portai.console; portai.console=Console(file=output,width=60,force_terminal=False,color_system=None)
        workspace_root=self.root/"workspaces"; reports=self.root/"reports"; app_root=self.root/"app"
        try:
            with patch.object(portai_agent_ui,"DATA_HOME",self.root/"agent-data"), \
                 patch.object(codex_runtime.shutil,"which",return_value=str(executable)), \
                 patch.object(portai_agent_ui.Prompt,"ask",side_effect=["2",str(project),"/quit"]), \
                 patch.object(portai_agent_ui.Confirm,"ask",return_value=True), \
                 patch.object(portai,"ROOT",app_root), patch.object(portai,"WORKSPACES",workspace_root), \
                 patch.object(portai,"REPORTS",reports):
                portai_agent_ui.open_agent(portai)
        finally: portai.console=old_console
        store=AgentStore(self.root/"agent-data"); tasks=store.list_tasks()
        self.assertEqual(len(tasks),1); self.assertEqual(tasks[0]["status"],"completed")
        self.assertEqual(tasks[0]["session_id"],"real-fixture-session")
        self.assertIn("Analysis complete with evidence",output.getvalue())
        self.assertTrue(Path(tasks[0]["report"]["json"]).is_file())

    def test_project_selection_resolves_recent_project_by_name(self):
        project=self.root/"Star Game"; project.mkdir()
        from rich.console import Console
        from io import StringIO
        app=type("App",(),{"ROOT":self.root})()
        with patch.object(portai_agent_ui,"load_projects",return_value=[{"path":str(project),"name":"Star Game"}]), \
             patch.object(portai_agent_ui.Prompt,"ask",return_value="Star Game"):
            chosen=portai_agent_ui._choose_project(app,Console(file=StringIO()),None)
        self.assertEqual(chosen,project.resolve())

    def test_main_navigation_exposes_separate_agent_entry(self):
        with patch.object(portai,"banner"),patch.object(portai,"ask_prompt",side_effect=["7","0"]),patch.object(portai.console,"print"),patch.object(portai,"portai_agent_menu") as action:
            portai.menu()
        action.assert_called_once_with()

if __name__=="__main__": unittest.main()
