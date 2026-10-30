"""Extract and rebuild common localization formats without touching originals."""

from __future__ import annotations

import ast
import csv
import io
import json
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

SUPPORTED_EXTENSIONS = {
    ".json", ".xml", ".txt", ".csv", ".po", ".resx", ".properties",
    ".ini", ".yaml", ".yml", ".strings",
}
SKIP_DIRS = {
    ".git", ".gradle", ".idea", ".venv", "venv", "node_modules",
    "__pycache__", "build", "dist", "target", "bin", "obj", "workspaces",
}
PLACEHOLDER_RE = re.compile(
    r"(?:\{\{?[^{}]+\}?\}|%\d*\$?[sdif]|\$\{[^}]+\}|<[^>]+>|"
    r"\\[nrt]|\[[A-Z][A-Z0-9_.:-]*\])"
)
IDENTIFIER_KEY_RE = re.compile(r"(?:^|[._-])(?:id|uuid|guid|code)(?:$|[._-])", re.I)
ANDROID_VALUE_TAGS = {"string", "item", "plurals"}


def is_translatable(value: str, key: str = "") -> bool:
    text = value.strip()
    if not text or not any(ch.isalpha() for ch in text):
        return False
    if IDENTIFIER_KEY_RE.search(key) or re.fullmatch(r"[A-Z0-9_.:/-]+", text):
        return False
    if text.startswith(("http://", "https://", "www.")):
        return False
    return True


def project_files(root: Path, max_files: int = 10000):
    count = 0
    for current, dirs, files in os.walk(root, followlinks=False):
        base = Path(current)
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not (base / d).is_symlink()]
        for name in files:
            path = base / name
            if path.is_symlink() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            yield path
            count += 1
            if count >= max_files:
                return


def _json_strings(value: Any, prefix: str = ""):
    if isinstance(value, dict):
        for key, child in value.items():
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            yield from _json_strings(child, f"{prefix}/{escaped}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _json_strings(child, f"{prefix}/{index}")
    elif isinstance(value, str) and is_translatable(value, prefix):
        yield prefix, value


def extract(path: Path, root: Path) -> tuple[str, list[tuple[str, str, str]]]:
    """Return format name and (key, source, context) values."""
    ext = path.suffix.lower()
    text = path.read_text(encoding="utf-8-sig")
    rel = path.relative_to(root).as_posix()
    values: list[tuple[str, str, str]] = []
    if ext == ".json":
        values.extend((key, val, "json") for key, val in _json_strings(json.loads(text)))
        return "json", values
    if ext in {".xml", ".resx"}:
        tree = _parse_xml(text)
        parents = {child: parent for parent in tree.iter() for child in parent}
        android = "/res/values" in f"/{rel}/" or path.name == "strings.xml"
        for index, node in enumerate(tree.iter()):
            if not isinstance(node.tag, str):
                continue
            tag = node.tag.rsplit("}", 1)[-1]
            if node.attrib.get("translatable", "true").lower() == "false":
                continue
            if tag not in ANDROID_VALUE_TAGS | {"value"} or not node.text or not is_translatable(node.text, node.attrib.get("name", "")):
                continue
            parent = parents.get(node)
            parent_name = parent.attrib.get("name") if parent is not None and isinstance(parent.tag, str) else None
            key = node.attrib.get("name") or (f"{parent_name}.{node.attrib['quantity']}" if tag == "item" and parent_name and node.attrib.get("quantity") else node.attrib.get("quantity")) or f"{tag}[{index}]"
            values.append((key, node.text, "android" if android else "xml"))
        return ("android_xml" if android else "xml"), values
    if ext == ".csv":
        rows = list(csv.reader(io.StringIO(text, newline="")))
        for ri, row in enumerate(rows):
            for ci, cell in enumerate(row):
                if is_translatable(cell):
                    values.append((f"row[{ri}].column[{ci}]", cell, "csv"))
        return "csv", values
    if ext == ".po":
        msgid = None
        plural = False
        plural_source = None
        plural_line = 0
        for line_no, line in enumerate(text.splitlines(), 1):
            match = re.match(r'\s*msgid\s+(".*")\s*$', line)
            if match:
                try:
                    msgid = ast.literal_eval(match.group(1))
                    plural = False
                except (SyntaxError, ValueError):
                    msgid = None
                    plural = False
            elif re.match(r"\s*msgid_plural\s+", line):
                plural = True
                plural_line = line_no
                try:
                    plural_source = ast.literal_eval(re.match(r'\s*msgid_plural\s+(".*")\s*$', line).group(1))
                except (AttributeError, SyntaxError, ValueError):
                    plural_source = None
            elif msgid and line.lstrip().startswith("msgstr"):
                if plural and plural_source and is_translatable(plural_source):
                    values.append((f"plural@{plural_line}", plural_source, "po-plural-unsupported"))
                elif not plural and line.lstrip().startswith("msgstr ") and is_translatable(msgid):
                    values.append((f"msgid@{line_no}", msgid, "po"))
                msgid = None
                plural_source = None
        return "po", values
    if ext in {".txt", ".properties", ".ini", ".yaml", ".yml", ".strings"}:
        for line_no, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", ";", "//")):
                continue
            if ext in {".properties", ".ini", ".yaml", ".yml", ".strings"} and ("=" in line or ":" in line):
                sep = "=" if "=" in line else ":"
                key, value = line.split(sep, 1)
                key, value = key.strip(), value.strip().strip('"\'')
                if is_translatable(value, key):
                    values.append((f"line[{line_no}].{key}", value, ext[1:]))
            elif is_translatable(line):
                values.append((f"line[{line_no}]", line, "text"))
        return ext[1:], values
    return ext[1:], values


def rebuild(path: Path, root: Path, translations: dict[str, str]) -> bytes:
    """Rebuild one localized copy; source path is opened read-only."""
    fmt, entries = extract(path, root)
    source = path.read_text(encoding="utf-8-sig")
    if fmt == "json":
        data = json.loads(source)

        def walk(value: Any, prefix: str = ""):
            if isinstance(value, dict):
                for key, child in value.items():
                    escaped = str(key).replace("~", "~0").replace("/", "~1")
                    full = f"{prefix}/{escaped}"
                    if isinstance(child, str) and full in translations:
                        value[key] = translations[full]
                    else:
                        walk(child, full)
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    full = f"{prefix}/{index}"
                    if isinstance(child, str) and full in translations:
                        value[index] = translations[full]
                    else:
                        walk(child, full)
        walk(data)
        indent_match = re.search(r"\n([ \t]+)\"", source)
        indent = indent_match.group(1) if indent_match else None
        separators = None if indent is not None or ": " in source else (",", ":")
        rendered = json.dumps(data, ensure_ascii=False, indent=indent, separators=separators)
        if source.endswith(("\n", "\r\n")):
            rendered += "\n"
        if "\r\n" in source:
            rendered = rendered.replace("\n", "\r\n")
        return rendered.encode("utf-8")
    if fmt in {"xml", "android_xml"}:
        tree = _parse_xml(source)
        parents = {child: parent for parent in tree.iter() for child in parent}
        nodes = [n for n in tree.iter() if isinstance(n.tag, str) and n.tag.rsplit("}", 1)[-1] in ANDROID_VALUE_TAGS | {"value"} and n.text]
        for index, node in enumerate(nodes):
            parent = parents.get(node)
            parent_name = parent.attrib.get("name") if parent is not None and isinstance(parent.tag, str) else None
            tag = node.tag.rsplit("}", 1)[-1]
            key = node.attrib.get("name") or (f"{parent_name}.{node.attrib['quantity']}" if tag == "item" and parent_name and node.attrib.get("quantity") else node.attrib.get("quantity")) or f"{tag}[{list(tree.iter()).index(node)}]"
            if key in translations:
                node.text = translations[key]
        return ET.tostring(tree, encoding="utf-8", xml_declaration=source.lstrip().startswith("<?xml"))
    if fmt == "po":
        lines = source.splitlines(keepends=True)
        msgid = None
        line_no = 0
        for index, line in enumerate(lines):
            match = re.match(r'\s*msgid\s+(".*")\s*$', line)
            if match:
                try:
                    msgid = ast.literal_eval(match.group(1))
                except (SyntaxError, ValueError):
                    msgid = None
                line_no = index + 1
            elif msgid is not None and line.lstrip().startswith("msgstr"):
                key = f"msgid@{line_no}"
                if key in translations:
                    indent = line[:len(line) - len(line.lstrip())]
                    lines[index] = f'{indent}msgstr {json.dumps(translations[key], ensure_ascii=False)}' + ("\n" if line.endswith("\n") else "")
                msgid = None
        return "".join(lines).encode("utf-8")
    if fmt == "csv":
        rows = list(csv.reader(io.StringIO(source, newline="")))
        for ri, row in enumerate(rows):
            for ci, cell in enumerate(row):
                key = f"row[{ri}].column[{ci}]"
                if key in translations:
                    row[ci] = translations[key]
        output = io.StringIO(newline="")
        terminator = "\r\n" if "\r\n" in source else "\n"
        csv.writer(output, lineterminator=terminator).writerows(rows)
        return output.getvalue().encode("utf-8")
    # Text-like formats retain each line and separator; key values are rewritten only by line key.
    lines = source.splitlines(keepends=True)
    for line_no, line in enumerate(lines, 1):
        key_prefix = f"line[{line_no}]"
        if key_prefix in translations:
            newline = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
            lines[line_no - 1] = translations[key_prefix] + newline
        else:
            match = re.match(r"(\s*[^:=]+\s*[:=]\s*)(.*?)(\r?\n)?$", line)
            if match:
                key = match.group(1).strip().rstrip(":=").strip()
                full = f"{key_prefix}.{key}"
                if full in translations:
                    value = match.group(2)
                    quote = value[:1] if value[:1] in {'"', "'"} and value[-1:] == value[:1] else ""
                    replacement = quote + translations[full] + quote
                    lines[line_no - 1] = match.group(1) + replacement + (match.group(3) or "")
    return "".join(lines).encode("utf-8")


def _parse_xml(text: str) -> ET.Element:
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    return ET.fromstring(text, parser=parser)
