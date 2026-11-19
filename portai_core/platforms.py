"""Evidence based platform catalog. Console SDKs are never presumed installed."""
from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class PlatformAdapter:
    id: str
    name: str
    family: str
    generation: str
    architecture: str
    technologies: tuple[str, ...]
    toolchains: tuple[str, ...]
    dependencies: tuple[str, ...]
    limitations: tuple[str, ...]
    project_types: tuple[str, ...]

    def status(self) -> dict:
        found = {tool: shutil.which(tool) for tool in self.toolchains}
        proprietary = self.family in {"PlayStation", "Xbox", "Nintendo"}
        return {**asdict(self), "available_tools": found,
                "toolchain_available": any(found.values()),
                "compile_capable": bool(any(found.values()) and not proprietary),
                "availability": "SDK oficial/licenciado necessário" if proprietary else
                    ("toolchain detectado; projeto ainda requer validação" if any(found.values()) else "toolchain não encontrado")}


_CONSOLES = [
    ("ps1", "PlayStation", "PS1", "MIPS R3000A", "C/C++, PSY-Q, PsyQ", "mipsel-none-elf-gcc"),
    ("ps2", "PlayStation", "PS2", "Emotion Engine (MIPS-derived)", "C/C++, Sony SDK", "ee-gcc"),
    ("ps3", "PlayStation", "PS3", "Cell (PPE + SPU)", "C/C++, Sony SDK", "ppu-gcc"),
    ("ps4", "PlayStation", "PS4", "x86-64", "C/C++, Sony SDK", "orbis-clang"),
    ("ps5", "PlayStation", "PS5", "x86-64", "C/C++, Sony SDK", "prospero-clang"),
    ("xbox", "Xbox", "Xbox clássico", "x86", "C/C++, Xbox SDK", "xbsdk"),
    ("xbox360", "Xbox", "Xbox 360", "PowerPC tri-core", "C/C++, Xbox SDK", "xenon-gcc"),
    ("xboxone", "Xbox", "Xbox One", "x86-64", "C/C++, Xbox GDK", "GameDK"),
    ("xboxseries", "Xbox", "Xbox Series X/S", "x86-64", "C/C++, Xbox GDK", "GameDK"),
    ("nes", "Nintendo", "NES", "8-bit Ricoh 2A03", "6502 assembly, C", "ca65"),
    ("snes", "Nintendo", "SNES", "16-bit 65C816", "65816 assembly, C", "ca65"),
    ("n64", "Nintendo", "Nintendo 64", "64-bit MIPS R4300i", "C/C++, libdragon / SDK", "mips64-elf-gcc"),
    ("gamecube", "Nintendo", "GameCube", "PowerPC Gekko", "C/C++, devkitPPC / SDK", "powerpc-eabi-gcc"),
    ("wii", "Nintendo", "Wii", "PowerPC Broadway", "C/C++, devkitPPC / SDK", "powerpc-eabi-gcc"),
    ("wiiu", "Nintendo", "Wii U", "PowerPC Espresso", "C/C++, Nintendo SDK", "wut"),
    ("switch", "Nintendo", "Nintendo Switch", "ARMv8-A", "C/C++, Nintendo SDK", "NintendoSDK"),
]
PLATFORMS = [PlatformAdapter(id, name, name, gen, arch, (tech,), (tool,),
    ("SDK oficial e licença de desenvolvimento",),
    ("A compilação exige SDK oficial autorizado; toolchain genérico não comprova compatibilidade.",),
    ("código-fonte nativo",)) for id, name, gen, arch, tech, tool in _CONSOLES]

PLATFORM_INDEX = {p.id: p for p in PLATFORMS}
PLATFORM_INDEX.update({"android": PlatformAdapter("android", "Android", "Mobile", "Android", "ARM64 / x86-64", ("Java/Kotlin", "C/C++", "Vulkan/OpenGL ES"), ("java", "gradle", "cmake", "ninja"), ("Android SDK", "Android NDK"), ("APK só pode ser declarado após build bem-sucedido e artefato validado.",), ("Gradle", "CMake/NDK")),
    "linux": PlatformAdapter("linux", "Linux", "Desktop", "Linux", "host-dependent", ("C/C++", "Vulkan/OpenGL"), ("cc", "cmake", "ninja", "make"), (), (), ("CMake", "Make", "Meson")),
    "windows": PlatformAdapter("windows", "Windows", "Desktop", "Windows", "x86-64 / ARM64", ("C/C++", "DirectX", "Vulkan"), ("cmake", "ninja"), ("Windows SDK",), ("Build efetivo requer toolchain e SDK compatíveis.",), ("CMake", "Visual Studio"))})

def get_platform(platform_id: str) -> PlatformAdapter:
    try:
        return PLATFORM_INDEX[platform_id.lower()]
    except KeyError as exc:
        raise ValueError(f"Plataforma desconhecida: {platform_id}") from exc

def list_platforms() -> list[dict]:
    return [p.status() for p in PLATFORMS]
