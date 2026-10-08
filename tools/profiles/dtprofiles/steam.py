"""Steam: where Darktide is installed, which build, and what vanilla is.

The vanilla file list comes from Steam's own depot manifests in
`Steam/depotcache`, one per installed depot of app 1361210, named
`<depot>_<manifest>.manifest`. Each lists every file the depot installs with
its size and SHA-1 (8 October 2026: 243,214 + 264 + 35 entries for build
25681127). That is what makes "is this file vanilla" a lookup instead of a
guess, and it is the only thing the tool trusts as vanilla.
"""
from __future__ import annotations

import os
import re
import struct
import winreg
from dataclasses import dataclass, field
from pathlib import Path

APP_ID = "1361210"

_PAYLOAD = 0x71F617D0
_METADATA = 0x1F4812BE
_SIGNATURE = 0x1B81B817
_END = 0x32C415AB
_DIRECTORY_FLAG = 64


class SteamError(RuntimeError):
    pass


# --- Valve KeyValues (text) -------------------------------------------------

_TOKEN = re.compile(r'"((?:\\.|[^"\\])*)"|([{}])|//[^\r\n]*|\s+')


def parse_keyvalues(text: str) -> dict:
    """The text KeyValues grammar of appmanifest/libraryfolders files."""
    root: dict = {}
    stack = [root]
    pending = None
    position = 0
    while position < len(text):
        match = _TOKEN.match(text, position)
        if not match:
            raise SteamError(f"unsupported KeyValues token at {position}")
        position = match.end()
        quoted, brace = match.group(1), match.group(2)
        if quoted is None and brace is None:
            continue
        if brace == "{":
            if pending is None:
                raise SteamError("KeyValues object without a key")
            child: dict = {}
            stack[-1][pending] = child
            stack.append(child)
            pending = None
        elif brace == "}":
            if pending is not None or len(stack) == 1:
                raise SteamError("unbalanced KeyValues")
            stack.pop()
        else:
            value = re.sub(r"\\([\\\"])", r"\1", quoted)
            if pending is None:
                pending = value
            else:
                stack[-1][pending] = value
                pending = None
    if pending is not None or len(stack) != 1:
        raise SteamError("incomplete KeyValues")
    return root


# --- Depot manifests (binary protobuf sections) -----------------------------

def _varint(buffer: bytes, index: int) -> tuple[int, int]:
    shift = result = 0
    while True:
        byte = buffer[index]
        index += 1
        result |= (byte & 0x7F) << shift
        if byte < 0x80:
            return result, index
        shift += 7


def _fields(buffer: bytes):
    index = 0
    while index < len(buffer):
        key, index = _varint(buffer, index)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, index = _varint(buffer, index)
        elif wire == 2:
            length, index = _varint(buffer, index)
            value = buffer[index:index + length]
            index += length
        elif wire == 1:
            value = buffer[index:index + 8]
            index += 8
        elif wire == 5:
            value = buffer[index:index + 4]
            index += 4
        else:
            raise SteamError(f"unsupported protobuf wire type {wire}")
        yield number, wire, value


@dataclass(frozen=True)
class DepotFile:
    size: int
    sha1: str


@dataclass
class DepotManifest:
    depot: int
    manifest: int
    files: dict[str, DepotFile] = field(default_factory=dict)  # key: normalized path
    directories: set[str] = field(default_factory=set)


def normalize(relative: str) -> str:
    """Case-folded, backslash-separated: the key every index uses."""
    return relative.replace("/", "\\").strip("\\").lower()


def read_depot_manifest(path: Path) -> DepotManifest:
    data = path.read_bytes()
    sections: dict[int, bytes] = {}
    index = 0
    while index + 4 <= len(data):
        (magic,) = struct.unpack_from("<I", data, index)
        if magic == _END:
            break
        (length,) = struct.unpack_from("<I", data, index + 4)
        index += 8
        sections[magic] = data[index:index + length]
        index += length
    if _PAYLOAD not in sections or _METADATA not in sections:
        raise SteamError(f"not a depot manifest: {path}")
    metadata = {number: value for number, wire, value in _fields(sections[_METADATA])
                if wire == 0}
    if metadata.get(4):
        raise SteamError(f"depot manifest has encrypted file names: {path}")
    manifest = DepotManifest(depot=int(metadata.get(1, 0)), manifest=int(metadata.get(2, 0)))
    for number, _, mapping in _fields(sections[_PAYLOAD]):
        if number != 1:
            continue
        name, size, flags, sha1 = "", 0, 0, ""
        for field_number, _, value in _fields(mapping):
            if field_number == 1:
                name = value.decode("utf-8")
            elif field_number == 2:
                size = value
            elif field_number == 3:
                flags = value
            elif field_number == 5:
                sha1 = value.hex()
        key = normalize(name)
        if flags & _DIRECTORY_FLAG:
            manifest.directories.add(key)
        else:
            manifest.files[key] = DepotFile(size=int(size), sha1=sha1)
    return manifest


# --- Installation -----------------------------------------------------------

@dataclass
class Installation:
    steam_root: Path
    library: Path            # the steamapps folder holding the appmanifest
    game_root: Path
    buildid: str
    target_buildid: str
    state_flags: int
    depots: dict[str, str]   # depot id -> manifest id

    @property
    def fully_installed(self) -> bool:
        # 4 = StateFullyInstalled; anything else (updating, validating,
        # update required) means Steam may be writing the game folder.
        return self.state_flags == 4 and self.buildid == self.target_buildid


def steam_roots() -> list[Path]:
    roots = []
    for hive, key, name in (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath")):
        try:
            with winreg.OpenKey(hive, key) as handle:
                roots.append(Path(winreg.QueryValueEx(handle, name)[0]))
        except OSError:
            pass
    program_files = os.environ.get("ProgramFiles(x86)")
    if program_files:
        roots.append(Path(program_files) / "Steam")
    unique = []
    for root in roots:
        if root.is_dir() and all(os.path.normcase(root) != os.path.normcase(u) for u in unique):
            unique.append(root)
    return unique


def _libraries(steam_root: Path) -> list[Path]:
    libraries = [steam_root / "steamapps"]
    folders = steam_root / "steamapps" / "libraryfolders.vdf"
    if folders.is_file():
        parsed = parse_keyvalues(folders.read_text(encoding="utf-8", errors="replace"))
        for entry in parsed.get("libraryfolders", {}).values():
            if isinstance(entry, dict) and "path" in entry:
                libraries.append(Path(entry["path"]) / "steamapps")
    return libraries


def find_installation(game_root: Path | None = None,
                      roots: list[Path] | None = None) -> Installation:
    found = []
    for steam_root in roots if roots is not None else steam_roots():
        for library in _libraries(steam_root):
            manifest = library / f"appmanifest_{APP_ID}.acf"
            if not manifest.is_file():
                continue
            state = parse_keyvalues(manifest.read_text(encoding="utf-8", errors="replace"))
            app = state.get("AppState", {})
            root = library / "common" / app.get("installdir", "")
            if not (root / "binaries" / "Darktide.exe").is_file():
                continue
            depots = {depot: values.get("manifest", "")
                      for depot, values in app.get("InstalledDepots", {}).items()
                      if isinstance(values, dict)}
            found.append(Installation(
                steam_root=steam_root, library=library, game_root=root,
                buildid=app.get("buildid", ""), target_buildid=app.get("TargetBuildID", ""),
                state_flags=int(app.get("StateFlags", "0") or 0), depots=depots))
    unique = {os.path.normcase(i.game_root.resolve()): i for i in found}
    candidates = list(unique.values())
    if game_root is not None:
        wanted = os.path.normcase(Path(game_root).resolve())
        candidates = [i for i in candidates if os.path.normcase(i.game_root.resolve()) == wanted]
        if not candidates:
            raise SteamError(f"no Steam installation of Darktide at {game_root}")
    if not candidates:
        raise SteamError("Darktide is not installed in any Steam library")
    if len(candidates) > 1:
        raise SteamError("several Darktide installations; pass --game-root: " +
                         ", ".join(str(i.game_root) for i in candidates))
    return candidates[0]


@dataclass
class VanillaIndex:
    """Every file and directory the installed build's depots put on disk."""
    files: dict[str, DepotFile]
    directories: set[str]
    depots: dict[str, str]

    def is_vanilla_path(self, key: str) -> bool:
        return key in self.files or key in self.directories


def load_vanilla_index(installation: Installation) -> VanillaIndex:
    files: dict[str, DepotFile] = {}
    directories: set[str] = set()
    for depot, manifest_id in installation.depots.items():
        name = f"{depot}_{manifest_id}.manifest"
        candidates = [installation.steam_root / "depotcache" / name,
                      installation.library.parent / "depotcache" / name]
        path = next((c for c in candidates if c.is_file()), None)
        if path is None:
            raise SteamError(
                f"Steam's manifest for depot {depot} ({manifest_id}) is not in depotcache; "
                "verifying the game's files in Steam writes it")
        manifest = read_depot_manifest(path)
        if str(manifest.depot) != depot or str(manifest.manifest) != manifest_id:
            raise SteamError(f"{path} does not describe depot {depot} manifest {manifest_id}")
        files.update(manifest.files)
        directories |= manifest.directories
    return VanillaIndex(files=files, directories=directories, depots=dict(installation.depots))
