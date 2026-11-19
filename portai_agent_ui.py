"""Dedicated conversational Rich interface for PORTAI AGENT."""
from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from rich import box
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from portai_core.agent import AgentOrchestrator, AgentStore, classify_request, codex_json_event
from portai_core.analyzer import analyze_project
from portai_core.build import detect_tools
from portai_core.compatibility import compare_project
from portai_core.platforms import PLATFORM_INDEX
from portai_core.projects import load_projects, register_project
from portai_core.reports import write_report

WELCOME = ("Olá! Sou o PortAI, seu agente inteligente de desenvolvimento e portabilidade de jogos.\n\n"
    "Posso analisar projetos, identificar incompatibilidades, adaptar código, preparar ambientes, executar ferramentas e auxiliar na compilação.\n\n"
    "Conte o que você deseja fazer. Eu cuido do planejamento e da execução.")
QUICK_ACTIONS = (
    "Portar um jogo", "Analisar projeto", "Corrigir incompatibilidades", "Preparar projeto para Android",
    "Preparar projeto para Windows/Linux", "Trabalhar com plataformas PlayStation",
    "Trabalhar com plataformas Xbox", "Trabalhar com plataformas Nintendo", "Compilar projeto", "Tarefa personalizada",
)
DATA_HOME = Path(os.environ.get("PORTAI_AGENT_HOME", str(Path.home()/".local"/"share"/"offtec-portai")))


def _session_file(store: AgentStore) -> Path: return store.root/"session.json"
def _read_session(store: AgentStore) -> dict:
    try: return json.loads(_session_file(store).read_text(encoding="utf-8"))
    except (OSError,ValueError): return {}
def _write_session(store: AgentStore, data: dict) -> None: store._write(_session_file(store),data)


def _header(console, codex_status: str, width: int) -> None:
    logo=Text.assemble(("PORTAI ","bold white"),("AGENT","bold bright_magenta"),("  •  OFFTEC","dim"))
    console.print(Panel(logo,subtitle=f"AGENT CONSOLE  |  CODEX {codex_status}",border_style="bright_magenta",box=box.DOUBLE,padding=(0,1)))


def _choose_project(app, console, current: Path | None) -> Path | None:
    if current and current.is_dir() and Confirm.ask(f"Continuar no projeto atual '{current.name}'?",default=True): return current.resolve()
    recent=load_projects(app.ROOT)
    rows=[r for r in recent if Path(r.get("path","")).is_dir()]
    if rows:
        table=Table(box=box.SIMPLE,header_style="bold bright_magenta",expand=True)
        table.add_column("#",justify="right"); table.add_column("Projeto"); table.add_column("Caminho",overflow="fold")
        for index,item in enumerate(rows[:15],1): table.add_row(str(index),item.get("name","?"),item["path"])
        console.print(Panel(table,title="Projetos recentes",border_style="magenta"))
    prompt="Pasta do projeto (número recente, caminho ou 'b' para procurar)"
    selection=Prompt.ask(prompt,default="b" if not rows else "1").strip()
    if selection.lower()=="b":
        selected=app.select_project()
        return selected.resolve() if selected else None
    if selection.isdigit() and 1<=int(selection)<=len(rows): return Path(rows[int(selection)-1]["path"]).resolve()
    named=[row for row in rows if row.get("name","").casefold()==selection.casefold()]
    if len(named)==1: return Path(named[0]["path"]).resolve()
    if len(named)>1:
        console.print("[yellow]Mais de um projeto usa esse nome. Escolha pelo número da lista.[/]"); return None
    project=Path(selection).expanduser().resolve()
    if not project.is_dir():
        console.print("[yellow]Esse diretório não existe. A seleção foi cancelada.[/]"); return None
    return project


def _source_platform(message: str, analysis: dict) -> str:
    match=re.search(r"\b(?:de|from|origem)\s+(ps[1-5]|xbox(?:360|one|series)?|nes|snes|n64|gamecube|wiiu?|switch|windows|linux|android)\b",message,re.I)
    if match:
        token=match.group(1).lower().replace("playstation","ps5")
        return token if token in PLATFORM_INDEX else ("xboxseries" if token.startswith("xbox") else "unknown")
    if "Windows API" in analysis.get("graphics_audio_input_signals",{}).get("platform",{}): return "windows"
    return "unknown"


def _diagnose(project: Path, message: str, source: str, target: str, static: dict | None=None) -> dict:
    static=static or analyze_project(project)
    try:
        from game_portability import analyze_game_project
        game=analyze_game_project(project)
    except (OSError,ValueError): game={"engine":"Unknown","detected_engines":[]}
    result={"project_analysis":static,"game_analysis":game,"environment":detect_tools(),"request":classify_request(message)}
    if source in PLATFORM_INDEX and target in PLATFORM_INDEX:
        result["compatibility"]=compare_project(static,source,target)
    return result


def _task_display(task: dict, logs: list[str], width: int):
    details=Table.grid(expand=True,padding=(0,1)); details.add_column(style="bright_magenta",no_wrap=True); details.add_column(overflow="fold")
    for key,value in (("Tarefa",task.get("name")),("Projeto",Path(task["project"]).name),("Destino",task.get("target")),("Etapa",task.get("stage")),("Estado",task.get("status")),("Progresso","não mensurável")):
        details.add_row(key,str(value or "—"))
    output="\n".join(logs[-10:]) or "Aguardando saída do Codex…"
    return Panel(Group(details,Text("\n"+output)),title="Tarefa PORTAI AGENT",border_style="bright_magenta",box=box.ROUNDED)


def _friendly_answer(task: dict) -> str:
    for line in reversed((task.get("result") or {}).get("output_tail",[])):
        event=codex_json_event(line)
        if not event:
            if line.strip(): return line.strip()
            continue
        item=event.get("item")
        if isinstance(item,dict) and item.get("type") in {"agent_message","assistant_message"}:
            text=item.get("text")
            if text: return str(text)
        if isinstance(event.get("message"),str) and event.get("type") in {"agent_message","response.output_text"}:
            return event["message"]
    if task.get("last_error"): return str(task["last_error"])
    return f"Codex terminou a tarefa com estado: {task.get('status')}. Consulte o relatório para ver a saída completa."


def _run_task(app, orchestrator: AgentOrchestrator, task: dict, codex: str, console) -> dict:
    command_events: queue.Queue=queue.Queue()
    finished=threading.Event(); stop=threading.Event(); result_box=[]
    logs=list(task.get("tail_log",[]))
    def worker():
        try:
            result_box.append(orchestrator.run(task["id"],codex,
                codex_exec_builder=app.codex_exec_command,
                codex_resume_builder=app.codex_resume_command,
                on_output=lambda line: command_events.put(line),stop_event=stop,
                user_approved=True))
        except BaseException as exc: result_box.append(exc)
        finally: finished.set()
    thread=threading.Thread(target=worker,name=f"portai-agent-{task['id']}",daemon=True); thread.start()
    state=task.copy()
    try:
        with Live(_task_display(state,logs,console.width),console=console,refresh_per_second=8,transient=False) as live:
            while not finished.is_set():
                try:
                    line=command_events.get(timeout=.15); logs.append(line); del logs[:-30]
                    event=codex_json_event(line)
                    if event:
                        session=event.get("thread_id") or event.get("session_id")
                        if session:
                            state=orchestrator.store.load(task["id"]); state["session_id"]=str(session); orchestrator.store.save(state)
                except queue.Empty: pass
                try: state=orchestrator.store.load(task["id"])
                except (OSError,ValueError): pass
                live.update(_task_display(state,logs,console.width),refresh=True)
    except KeyboardInterrupt:
        stop.set(); console.print("\n[yellow]Solicitando interrupção segura do Codex…[/]")
    thread.join()
    outcome=result_box[0] if result_box else None
    if isinstance(outcome,BaseException):
        console.print(f"[red]Tarefa não pôde ser executada: {outcome}[/]"); return orchestrator.store.load(task["id"])
    outcome=outcome or orchestrator.store.load(task["id"])
    if outcome.get("status")=="completed" and outcome.get("diagnostics",{}).get("request",{}).get("intent")=="build":
        outcome["status"]="running"; outcome["stage"]="Build Manager executing a detected build"; orchestrator.store.save(outcome)
        try:
            workspace=Path(outcome["workspace"])
            if outcome.get("target")=="android":
                def build_log(line):
                    current=orchestrator.store.load(task["id"])
                    tail=current.get("build_log",[]); tail.append(line[:2000]); current["build_log"]=tail[-30:]
                    orchestrator.store.save(current)
                    console.print(f"[dim]{line}[/]")
                code,stdout,stderr,apk=app.run_build(workspace,on_output=build_log)
                status="succeeded" if code==0 and apk else "interrupted" if code==130 else "timeout" if code==124 else "failed"
                build={"status":status,"returncode":code,"stdout":stdout[-12000:],"stderr":stderr[-12000:],"artifact":str(apk) if apk else None}
            elif outcome.get("target") in {"windows","linux"}:
                from portai_core.build import run_build
                build=run_build(workspace,on_output=lambda line: console.print(f"[dim]{line}[/]"))
            else:
                build={"status":"not_run","reason":"Destino sem build local verificável ou SDK autorizado não detectado."}
            outcome=orchestrator.store.load(task["id"])
            outcome["build"]=build
            outcome["status"]="completed" if build.get("status")=="succeeded" else build.get("status") if build.get("status") in {"interrupted","timeout"} else "failed" if build.get("status")=="failed" else "blocked"
            outcome["stage"]="build finished" if build.get("status") in {"succeeded","failed"} else "build unavailable"
            outcome["result"]={**(outcome.get("result") or {}),"build":build}
            orchestrator.store.save(outcome)
            console.print(Panel(f"Build: {build.get('status')}\nArtefato: {build.get('artifact') or 'não gerado'}\n{build.get('reason','')}",title="Build Manager",border_style="green" if build.get("status")=="succeeded" else "yellow"))
        except (OSError,ValueError,subprocess.SubprocessError) as exc:
            outcome["status"]="failed"; outcome["stage"]="build failed"; outcome["last_error"]=str(exc); orchestrator.store.save(outcome)
    return outcome


def _render_history(app, store: AgentStore, session: dict, console) -> None:
    current=session.get("task_id")
    if current:
        try:
            task=store.load(current)
            for item in task.get("messages",[])[-8:]:
                style="bright_cyan" if item.get("role")=="user" else "white"
                console.print(Panel(str(item.get("content",""))[-1600:],title="Você" if item.get("role")=="user" else "Agente",border_style=style,box=box.ROUNDED))
        except (OSError,ValueError): pass
    quick=Table.grid(expand=True,padding=(0,1)); quick.add_column()
    for index,label in enumerate(QUICK_ACTIONS,1): quick.add_row(f"[bright_magenta]{index:>2}.[/] {label}")
    console.print(Panel(quick,title="Ações rápidas — escolha um número ou escreva livremente",border_style="magenta"))


def open_agent(app) -> None:
    """Run a separate agent conversation UI on top of existing PortAI services."""
    store=AgentStore(DATA_HOME)
    recovered=store.recover()
    session=_read_session(store)
    orchestrator=AgentOrchestrator(store,app.WORKSPACES,store.root/"backups")
    codex=shutil.which("codex")
    codex_status="indisponível"
    if codex:
        try:
            probe=subprocess.run([codex,"--version"],capture_output=True,text=True,timeout=5,check=False)
            codex_status=(probe.stdout or probe.stderr).strip() or ("pronto" if probe.returncode==0 else f"erro {probe.returncode}")
            if probe.returncode: codex_status="erro: "+codex_status
        except (OSError,subprocess.TimeoutExpired) as exc: codex_status=f"erro: {exc}"
    _header(app.console,codex_status,app.console.width)
    app.console.print(Panel(WELCOME,title="OFFTEC • PORTAI AGENT",border_style="bright_magenta",box=box.ROUNDED))
    if recovered: app.console.print(f"[yellow]{len(recovered)} tarefa(s) em execução foram recuperadas como interrompidas.[/]")
    if not codex:
        app.console.print(Panel("Codex CLI não foi encontrado no PATH. Nenhuma resposta de IA será simulada. Instale/configure o Codex e execute o diagnóstico do ambiente para verificar o acesso.",title="Agente indisponível",border_style="yellow"))
        if Confirm.ask("Abrir diagnóstico de ambiente?",default=True): app.check_environment()
        return
    if codex_status.startswith("erro:") or "cannot link executable" in codex_status.lower():
        app.console.print(Panel(f"Codex CLI foi localizado, mas falhou na verificação: {codex_status}\nVerifique a instalação com `codex --version`.",title="Codex não está executável",border_style="yellow"))
        if Confirm.ask("Abrir diagnóstico de ambiente?",default=True): app.check_environment()
        return
    quick_lookup={str(index):label for index,label in enumerate(QUICK_ACTIONS,1)}
    quick_prompts={"Portar um jogo":"Quero portar meu jogo para Android.","Analisar projeto":"Analise este projeto e explique sua arquitetura e dependências.","Corrigir incompatibilidades":"Encontre e corrija incompatibilidades neste projeto.","Preparar projeto para Android":"Prepare este projeto para Android ARM64.","Preparar projeto para Windows/Linux":"Prepare este projeto para compilar em Windows ou Linux.","Trabalhar com plataformas PlayStation":"Analise o projeto para uma plataforma PlayStation e informe requisitos do SDK oficial.","Trabalhar com plataformas Xbox":"Analise o projeto para Xbox e informe requisitos do GDK oficial.","Trabalhar com plataformas Nintendo":"Analise o projeto para Nintendo e informe requisitos do SDK oficial.","Compilar projeto":"Tente compilar este projeto com as ferramentas disponíveis.","Tarefa personalizada":""}
    while True:
        _header(app.console,codex_status,app.console.width)
        _render_history(app,store,session,app.console)
        raw=Prompt.ask("[bold bright_magenta]PortAI Agent[/]  mensagem  •  /tasks  •  /resume ID  •  /quit",default="/quit").strip()
        if raw in {"/quit","/exit","0"}: return
        if raw=="/tasks":
            table=Table(box=box.SIMPLE,header_style="bold bright_magenta",expand=True)
            for name in ("ID","Tarefa","Estado","Etapa","Projeto"): table.add_column(name,overflow="fold")
            for task in store.list_tasks(25): table.add_row(task["id"],task["name"],task["status"],task.get("stage","—"),Path(task["project"]).name)
            app.console.print(table); continue
        resume_task=None
        if raw.startswith("/resume "):
            task_id=raw.split(maxsplit=1)[1]
            try: resume_task=store.load(task_id)
            except (OSError,ValueError) as exc: app.console.print(f"[red]Tarefa não encontrada: {exc}[/]"); continue
            if not resume_task.get("session_id"):
                app.console.print("[yellow]A tarefa não possui uma sessão Codex retomável. Use a tarefa nova com o mesmo projeto para continuar com o contexto salvo.[/]"); continue
            task=resume_task
        else:
            message=quick_prompts.get(quick_lookup.get(raw,""),raw)
            if not message: message=Prompt.ask("Descreva a tarefa")
            project=Path(session["project"]) if session.get("project") else None
            project=_choose_project(app,app.console,project)
            if project is None: continue
            try:
                register_project(app.ROOT,project)
                workspace=app.find_workspace(project)
                if workspace is None:
                    if not Confirm.ask("Criar cópia isolada e autorizada para o agente trabalhar?",default=True): continue
                    workspace=app.make_workspace(project)
                    app.save_workspace_state(workspace,{"source_path":str(project),"workspace":str(workspace),"status":"PortAI Agent workspace","session_id":None,"analysis_done":False,"history":[]})
                routing=classify_request(message)
                target=routing["target"]
                if routing["intent"] in {"port","build"} and target=="unknown":
                    target=Prompt.ask("Plataforma de destino",choices=sorted(PLATFORM_INDEX),default="android")
                static=analyze_project(project)
                source=_source_platform(message,static)
                diagnostics=_diagnose(project,message,source,target,static)
                previous=store.load(session["task_id"]) if session.get("task_id") else None
                task=orchestrator.prepare(message,project,workspace,diagnostics,source,target,previous)
                session.update({"project":str(project),"workspace":str(workspace),"task_id":task["id"]}); _write_session(store,session)
            except (OSError,ValueError) as exc:
                app.console.print(f"[red]Não foi possível preparar a tarefa: {exc}[/]"); continue
        task["messages"].append({"role":"assistant","content":"Diagnóstico local concluído; enviando contexto estruturado ao Codex.","at":task.get("last_activity")})
        store.save(task)
        task=_run_task(app,orchestrator,task,codex,app.console)
        report_data={"project":task["project"],"source":task.get("source"),"target":task.get("target"),"objective":task.get("objective"),"diagnostics":task.get("diagnostics"),"task":task,"result":task.get("result"),"changed_files":task.get("changed_files")}
        try:
            md,js=write_report(report_data,app.REPORTS)
            task=store.load(task["id"]); task["report"]={"markdown":str(md),"json":str(js)}
            response=(task.get("result") or {}).get("output_tail",[])
            task["messages"].append({"role":"assistant","content":_friendly_answer(task),"at":task.get("last_activity")})
            store.save(task)
            session["task_id"]=task["id"]; _write_session(store,session)
            app.console.print(Panel(f"Estado: {task['status']}\nEtapa: {task['stage']}\nAlterações: {len(task.get('changed_files',[]))}\nRelatório: {md.name} / {js.name}",title="Resultado da tarefa",border_style="green" if task["status"]=="completed" else "yellow"))
        except (OSError,ValueError) as exc: app.console.print(f"[yellow]Tarefa salva; não foi possível escrever relatório: {exc}[/]")

