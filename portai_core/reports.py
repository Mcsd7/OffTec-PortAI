"""Portable JSON and Markdown operation reports."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

def write_report(report: dict[str,Any], directory: Path) -> tuple[Path,Path]:
    directory.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    js=directory/f"portai-{stamp}.json"; md=directory/f"portai-{stamp}.md"
    payload={"generated_at":datetime.now(timezone.utc).isoformat(timespec="seconds"),**report}
    js.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    lines=["# OffTec PortAI — Diagnóstico","",f"- Projeto: `{payload.get('project','não informado')}`",f"- Origem: {payload.get('source','não informada')}",f"- Destino: {payload.get('target','não informado')}",f"- Resultado: **{payload.get('classification',payload.get('verdict','não concluído'))}**","","## Registro completo","", "```json", json.dumps(payload,indent=2,ensure_ascii=False),"```"]
    md.write_text("\n".join(lines)+"\n",encoding="utf-8")
    return md,js
