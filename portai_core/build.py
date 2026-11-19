"""Local tool discovery and safe build execution (argv only, bounded timeout)."""
from __future__ import annotations
import os, shutil, subprocess
from pathlib import Path

TOOLS=("python","git","cc","clang","gcc","c++","g++","cmake","ninja","make","java","gradle","adb","aapt","apksigner","codex")
def detect_tools() -> dict:
    env=os.environ
    sdk=env.get("ANDROID_HOME") or env.get("ANDROID_SDK_ROOT")
    ndk=env.get("ANDROID_NDK_HOME") or env.get("ANDROID_NDK_ROOT")
    return {"host":os.uname().machine if hasattr(os,"uname") else "unknown","tools":{name:shutil.which(name) for name in TOOLS},"android_sdk":str(Path(sdk).expanduser()) if sdk else None,"android_sdk_found":bool(sdk and Path(sdk).expanduser().is_dir()),"android_ndk":str(Path(ndk).expanduser()) if ndk else None,"android_ndk_found":bool(ndk and Path(ndk).expanduser().is_dir()),"termux":bool(os.environ.get("TERMUX_VERSION") or "/com.termux/" in str(Path.home())),"limitations":(["Termux ARM64 may not support proprietary desktop/console toolchains."] if os.environ.get("TERMUX_VERSION") else [])}

def detect_build_command(project: Path) -> list[str] | None:
    project=project.resolve(strict=True)
    wrapper=project/"gradlew"
    if wrapper.is_file() and os.access(wrapper,os.X_OK): return [str(wrapper),"assembleDebug"]
    if (project/"CMakeLists.txt").is_file() and shutil.which("cmake"):
        if shutil.which("ninja"): return [shutil.which("cmake"),"-S",str(project),"-B",str(project/"build"),"-G","Ninja"]
        return [shutil.which("cmake"),"-S",str(project),"-B",str(project/"build")]
    if (project/"Makefile").is_file() and shutil.which("make"): return [shutil.which("make"),"-C",str(project)]
    if (project/"build.gradle").is_file() or (project/"build.gradle.kts").is_file():
        gradle=shutil.which("gradle")
        return [gradle,"assembleDebug"] if gradle else None
    return None

def run_build(project: Path, timeout: int=1800) -> dict:
    project=project.resolve(strict=True)
    if not project.is_dir(): raise ValueError("Build target precisa ser diretório")
    if not 1<=timeout<=7200: raise ValueError("Timeout deve ficar entre 1 e 7200 segundos")
    command=detect_build_command(project)
    if not command: return {"status":"not_run","reason":"Nenhum build conhecido com ferramenta compatível detectado.","command":None}
    try:
        proc=subprocess.run(command,cwd=str(project),capture_output=True,text=True,timeout=timeout,check=False,shell=False)
        if proc.returncode == 0 and command[0] and Path(command[0]).name == "cmake" and "-S" in command:
            build_cmd=[command[0],"--build",str(project/"build")]
            built=subprocess.run(build_cmd,cwd=str(project),capture_output=True,text=True,timeout=timeout,check=False,shell=False)
            proc=subprocess.CompletedProcess(build_cmd,built.returncode,proc.stdout+"\n"+built.stdout,proc.stderr+"\n"+built.stderr)
        return {"status":"succeeded" if proc.returncode==0 else "failed","returncode":proc.returncode,"command":command,"stdout":proc.stdout[-20000:],"stderr":proc.stderr[-20000:],"artifact":None}
    except subprocess.TimeoutExpired as exc:
        return {"status":"timeout","command":command,"stdout":str(exc.stdout or "")[-20000:],"stderr":str(exc.stderr or "")[-20000:]}
    except OSError as exc:
        return {"status":"failed","command":command,"error":str(exc)}
