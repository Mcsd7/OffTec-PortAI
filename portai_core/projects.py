"""Small local project registry; external project files are only read here."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

def registry_path(root: Path) -> Path: return root/".portai-projects.json"
def load_projects(root: Path) -> list[dict]:
    try:
        value=json.loads(registry_path(root).read_text(encoding="utf-8"))
        return value if isinstance(value,list) else []
    except (OSError,ValueError): return []
def register_project(root: Path, project: Path) -> dict:
    root=root.expanduser().resolve(); project=project.expanduser().resolve(strict=True)
    if not project.is_dir(): raise ValueError("Projeto precisa ser um diretório")
    rows=load_projects(root); key=str(project)
    record={"path":key,"name":project.name,"last_seen":datetime.now(timezone.utc).isoformat(timespec="seconds")}
    rows=[r for r in rows if r.get("path")!=key]; rows.insert(0,record)
    root.mkdir(parents=True,exist_ok=True); registry_path(root).write_text(json.dumps(rows[:100],indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    return record
