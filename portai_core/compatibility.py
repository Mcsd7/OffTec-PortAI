"""Conservative compatibility findings and actionable, evidence-linked plans."""
from __future__ import annotations
from .platforms import get_platform

def compare_project(analysis: dict, source: str, target: str) -> dict:
    src=get_platform(source); dst=get_platform(target); findings=[]
    signals=analysis.get("graphics_audio_input_signals",{})
    if dst.family in {"PlayStation","Xbox","Nintendo"}:
        findings.append(_item("incompatible", "Console SDK não disponível para validação", "SDK proprietários/licenciados não são presumidos. Sem SDK oficial não há compilação verificável.", "Use SDK oficial autorizado e valide o projeto no ambiente do fabricante."))
    for label, paths in signals.get("graphics",{}).items():
        if label=="Direct3D" and target not in {"windows"}:
            findings.append(_item("incompatible", "Direct3D específico do Windows", f"Evidência em: {', '.join(paths[:5])}", "Introduza backend Vulkan/OpenGL ES ou camada de renderização multiplataforma."))
        elif label in {"Vulkan","OpenGL","OpenGL ES"}:
            findings.append(_item("adaptation", f"API gráfica detectada: {label}", f"Evidência em: {', '.join(paths[:5])}", "Verifique extensões, perfil e capacidades reais do destino."))
    if signals.get("audio",{}): findings.append(_item("unverified","Camada de áudio detectada", str(signals["audio"]),"Verifique backend e substitua middleware indisponível no destino."))
    if signals.get("input",{}): findings.append(_item("adaptation","Sistema de entrada detectado",str(signals["input"]),"Mapeie botões, eixos, hot-plug e zonas mortas para o destino."))
    found=set(analysis.get("build_systems",[]))
    if not found: findings.append(_item("unverified","Sistema de build não identificado","Nenhum manifesto de build conhecido foi encontrado.","Identifique dependências e defina um build reproduzível."))
    else: findings.append(_item("adaptation","Revisar build para arquitetura de destino",", ".join(sorted(found)),"Configure toolchain/ABI do destino e valide uma compilação limpa."))
    if not findings: findings.append(_item("compatible","Sem incompatibilidades conhecidas nas regras disponíveis","Nenhuma assinatura incompatível reconhecida.","A ausência de evidência não comprova compatibilidade; compile e teste no destino."))
    return {"source":src.id,"target":dst.id,"classification":"incompatível" if any(x["status"]=="incompatible" for x in findings) else "requer validação/adaptação","findings":findings,"plan":[x["action"] for x in findings],"compile_capable":dst.status()["compile_capable"]}

def _item(status,title,evidence,action): return {"status":status,"title":title,"evidence":evidence,"action":action}
