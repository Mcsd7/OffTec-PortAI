"""Bounded, read-only heuristic game-project analyzer."""
from __future__ import annotations
import os, re
from pathlib import Path

SKIP = {".git", ".gradle", ".idea", ".venv", "venv", "node_modules", "build", "dist", "target", "bin", "obj", "__pycache__"}
LIMIT = 5000
MARKERS = {"unity": ("ProjectSettings/ProjectVersion.txt",), "unreal": ("*.uproject",), "godot": ("project.godot",), "cmake": ("CMakeLists.txt",), "sdl": ("CMakeLists.txt", "meson.build", "Makefile")}
TOKENS = {
 "graphics": {"Direct3D": r"\b(D3D11|D3D12|Direct3D|ID3D)\w*", "Vulkan": r"\bVulkan\b|vk[A-Z]\w+", "OpenGL": r"\b(OpenGL|gl[A-Z]\w+)\b", "OpenGL ES": r"\bGLES\w*|OpenGL ES", "Metal": r"\bMetal\b|MTLDevice"},
 "audio": {"XAudio2": r"\bXAudio2\b", "FMOD": r"\bFMOD\b", "OpenAL": r"\bOpenAL\b|AL/al.h", "SDL_mixer": r"SDL_mixer"},
 "input": {"XInput": r"\bXInput\w*", "Raw Input": r"RegisterRawInputDevices", "SDL input": r"SDL_(GameController|Joystick)"},
 "platform": {"Windows API": r"windows\.h|WinMain|CreateWindowEx|CreateProcessW", "Linux/POSIX": r"unistd\.h|pthread_|/proc/", "Android": r"AndroidManifest\.xml|__ANDROID__|android\."}}

def analyze_project(root: str | Path) -> dict:
    root = Path(root).expanduser().resolve(strict=True)
    if not root.is_dir(): raise ValueError("Caminho do projeto precisa ser um diretório")
    files=[]; contents={}; truncated=False
    for current, dirs, names in os.walk(root, followlinks=False):
        base=Path(current); dirs[:]=[d for d in dirs if d not in SKIP and not (base/d).is_symlink()]
        for name in names:
            p=base/name
            if p.is_symlink() or not p.is_file(): continue
            rel=p.relative_to(root).as_posix(); files.append(rel)
            if len(files)>=LIMIT: truncated=True; break
            if p.suffix.lower() in {".c", ".h", ".cc", ".cpp", ".hpp", ".cxx", ".cs", ".py", ".java", ".kt", ".js", ".ts", ".rs", ".go", ".gd", ".shader", ".glsl", ".xml", ".json", ".toml", ".txt", ".cmake", ".gradle", ".kts", ".uproject", ".godot"}:
                try:
                    if p.stat().st_size <= 1_000_000: contents[rel]=p.read_text(errors="replace")
                except OSError: pass
        if truncated: break
    joined="\n".join(contents.values()); low=joined.lower()
    engines=[]
    if any(Path(f).name=="ProjectVersion.txt" and f.startswith("ProjectSettings/") for f in files): engines.append("Unity")
    if any(f.endswith(".uproject") for f in files): engines.append("Unreal Engine")
    if "project.godot" in files: engines.append("Godot")
    if not engines and re.search(r"\bSDL(_IMAGE|_MIXER|_TTF)?\b|find_package\s*\(\s*sdl",joined,re.I): engines.append("SDL")
    build=[f for f in files if Path(f).name in {"CMakeLists.txt","Makefile","meson.build","Cargo.toml","package.json","pyproject.toml","build.gradle","build.gradle.kts","pom.xml","AndroidManifest.xml"}]
    langs={}
    for f in files:
        ext=Path(f).suffix.lower()
        if ext: langs[ext]=langs.get(ext,0)+1
    assets=[f for f in files if Path(f).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".ogg", ".wav", ".mp3", ".mp4", ".obj", ".fbx", ".glb", ".gltf", ".ttf", ".otf"}]
    signals={category:{label:sorted(f for f,text in contents.items() if re.search(pattern,text,re.I))[:40] for label,pattern in patterns.items() if any(re.search(pattern,text,re.I) for text in contents.values())} for category,patterns in TOKENS.items()}
    signals={k:v for k,v in signals.items() if v}
    deps=sorted({match for text in contents.values() for match in re.findall(r"(?:find_package\s*\(\s*|#include\s*[<\"])([A-Za-z_][\w.+-]*)",text,re.I)})[:100]
    return {"project":str(root),"name":root.name,"engines":engines or ["Unknown/custom"],"languages":langs,"build_systems":sorted({Path(f).name for f in build}),"dependencies":deps,"assets":{"count":len(assets),"samples":assets[:50]},"graphics_audio_input_signals":signals,"files_scanned":len(files),"text_files_read":len(contents),"scan_truncated":truncated,"limitations":["Heuristic static scan; no source compilation or asset decoding.","Dependencies and API availability require target SDK validation."]}
