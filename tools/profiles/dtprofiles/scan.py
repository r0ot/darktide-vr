"""What is in the game folder, against what Steam says vanilla is.

Every file is one of:

- vanilla: in Steam's manifest with the same size (and the same SHA-1 where
  checked);
- modified: in Steam's manifest but different;
- extra: not in Steam's manifest at all.

Extras and modified files are then assigned to a component (the mod loader,
one per mod, the VR mod) or left unmanaged. Unmanaged files are recorded in a
capture but never written or removed by a switch: a file the tool cannot
attribute is the user's.
"""
from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

from .steam import VanillaIndex, normalize
from .store import sha1_file, sha256_file

# The Darktide Mod Loader, the Auto Mod Loader (which replaces
# mods/base/mod_manager.lua) and the Darktide Mod Framework. Paths are
# normalized keys; a trailing backslash means everything below.
LOADER_FILES = (
    "binaries\\mod_loader",
    "bundle\\9ba626afa44a3aa3.patch_999",
    "mods\\mod_load_order.txt",
    "toggle_darktide_mods.bat",
    "readme.md",
    "tools\\dtkit-patch.exe",
    "tools\\readme.md",
)
LOADER_TREES = ("mods\\base\\", "mods\\dmf\\")

# Written by the game or by mods while they run; recorded, never compared or
# switched, so a launch does not count as a change to a profile.
GENERATED = re.compile(r"^mods\\auto_mod_loader_log\.txt$|\.dtprofiles-tmp$")

# Written by the VR mod while it runs: every pixel shader its native module
# sees, about 1,700 files a launch, never read back. They belong to the VR
# component, so leaving VR removes them with it (AML would otherwise find a
# darktidevr folder), but they are never stored nor written back.
RUNTIME_OUTPUT = re.compile(r"^mods\\darktidevr\\bin\\(billboard|blended)_pixel_shaders\\",
                            re.IGNORECASE)


def is_runtime_output(key: str) -> bool:
    return RUNTIME_OUTPUT.match(key) is not None


# The files the loader patch and the VR mod change in place.
BUNDLE_DATABASE = "bundle\\bundle_database.data"
BUNDLE_DATABASE_BACKUP = "bundle\\bundle_database.data.bak"
GAME_EXECUTABLE = "binaries\\darktide.exe"
VR_MOD = "darktidevr"
VR_PROXY = "binaries\\d3d12.dll"

# Vanilla files whose SHA-1 is checked on every scan: everything a mod setup
# is known to change in place, and the small files beside them. The 15,000
# content bundles are size-checked only unless a full verification is asked
# for.
_CONTENT_BUNDLE = re.compile(r"^[0-9a-f]{16}(\.stream)?$")


def _hashed_always(key: str) -> bool:
    parts = key.split("\\")
    if len(parts) == 1:
        return True                                   # the game folder's own files
    if len(parts) == 2 and parts[0] == "binaries":
        return True
    return len(parts) == 2 and parts[0] == "bundle" and not _CONTENT_BUNDLE.match(parts[1])


@dataclass
class FileEntry:
    key: str          # normalized relative path
    relative: str     # as found on disk
    size: int
    status: str       # vanilla | modified | extra | generated
    sha256: str | None = None
    # The Windows read-only attribute: git marks its object files read-only,
    # and a mod that ships its .git folder (Power_DI does) has them.
    readonly: bool = False


@dataclass
class GameScan:
    root: Path
    files: dict[str, FileEntry] = field(default_factory=dict)
    missing_vanilla: list[str] = field(default_factory=list)
    directories: set[str] = field(default_factory=set)  # every directory on disk

    def changed(self) -> list[FileEntry]:
        return [f for f in self.files.values() if f.status in ("modified", "extra")]


def component_of(key: str, relative: str | None = None) -> str | None:
    """The component a non-vanilla path belongs to, or None (unmanaged).
    With `relative` (the path as found on disk) a mod keeps its folder's own
    capitalization, which is the name the user knows it by."""
    if key in LOADER_FILES or key.startswith(LOADER_TREES):
        return "loader"
    if key in (BUNDLE_DATABASE, BUNDLE_DATABASE_BACKUP):
        return "loader"
    if key == VR_PROXY or key == GAME_EXECUTABLE:
        return "vr" if key == VR_PROXY else "executable"
    parts = key.split("\\")
    if len(parts) >= 3 and parts[0] == "mods":
        if parts[1] == VR_MOD:
            return "vr"
        if relative is not None:
            return "mod:" + relative.replace("/", "\\").strip("\\").split("\\")[1]
        return f"mod:{parts[1]}"
    return None


def mod_folder_of(key: str) -> str | None:
    parts = key.split("\\")
    if len(parts) >= 3 and parts[0] == "mods" and parts[1] not in ("base", "dmf"):
        return parts[1]
    return None


def scan_game(root: Path, vanilla: VanillaIndex, full: bool = False,
              hash_changed: bool = True) -> GameScan:
    """Walk the whole game folder once. `full` checks every vanilla SHA-1."""
    root = Path(root)
    scan = GameScan(root=root)
    seen: set[str] = set()
    for directory, subdirectories, names in os.walk(root):
        relative_directory = os.path.relpath(directory, root)
        if relative_directory != ".":
            scan.directories.add(normalize(relative_directory))
        for name in names:
            path = Path(directory) / name
            relative = os.path.relpath(path, root)
            key = normalize(relative)
            seen.add(key)
            info = path.stat()
            size = info.st_size
            expected = vanilla.files.get(key)
            if GENERATED.search(key):
                status = "generated"
            elif expected is None:
                status = "extra"
            elif expected.size != size:
                status = "modified"
            elif full or _hashed_always(key):
                status = "vanilla" if sha1_file(path) == expected.sha1 else "modified"
            else:
                status = "vanilla"
            entry = FileEntry(key=key, relative=relative, size=size, status=status,
                              readonly=bool(info.st_file_attributes &
                                            stat.FILE_ATTRIBUTE_READONLY))
            if hash_changed and status in ("modified", "extra"):
                entry.sha256 = sha256_file(path)
            scan.files[key] = entry
    scan.missing_vanilla = sorted(k for k in vanilla.files if k not in seen)
    return scan
