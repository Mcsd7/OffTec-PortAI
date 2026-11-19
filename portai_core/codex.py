"""Snapshots and structured read-only context for the existing Codex CLI."""
from __future__ import annotations
import hashlib, os, zipfile
from datetime import datetime
from pathlib import Path

SKIP={".git","build","dist","target","node_modules",".gradle",".venv","venv","__pycache__"}
MAX_BACKUP_BYTES=1_000_000_000

def file_inventory(root: Path) -> dict[str,str]:
    result={}
    for current,dirs,names in os.walk(root,followlinks=False):
        base=Path(current); dirs[:]=[d for d in dirs if d not in SKIP and not (base/d).is_symlink()]
        for name in names:
            path=base/name
            if path.is_symlink() or not path.is_file(): continue
            try:
                digest=hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda:stream.read(1024*1024),b""): digest.update(chunk)
                result[path.relative_to(root).as_posix()]=digest.hexdigest()
            except OSError: continue
    return result

def backup_workspace(root: Path, backup_root: Path) -> Path:
    root=root.resolve(strict=True); backup_root.mkdir(parents=True,exist_ok=True)
    files=[]; total=0
    for current,dirs,names in os.walk(root,followlinks=False):
        base=Path(current); dirs[:]=[d for d in dirs if d not in SKIP and not (base/d).is_symlink()]
        for name in names:
            path=base/name
            if path.is_symlink() or not path.is_file(): continue
            size=path.stat().st_size; total+=size
            if total>MAX_BACKUP_BYTES: raise ValueError("Workspace excede limite de backup de 1 GB; Codex não foi iniciado.")
            files.append(path)
    archive=backup_root/f"{root.name}-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.zip"
    with zipfile.ZipFile(archive,"w",compression=zipfile.ZIP_DEFLATED) as zf:
        for path in files: zf.write(path,path.relative_to(root).as_posix())
    return archive

def structured_context(root: Path) -> dict:
    from .analyzer import analyze_project
    from .build import detect_tools
    return {"analysis":analyze_project(root),"environment":detect_tools(),"safety":{"original_project_read_only":True,"codex_workspace":str(root),"commands_from_project_files_are_not_auto_executed":True}}

def changed_paths(before: dict[str,str], after: dict[str,str]) -> list[str]:
    return sorted(path for path in before.keys()|after.keys() if before.get(path)!=after.get(path))
