"""Profiles, capture, planning and the journaled switch.

The game folder is described as a set of managed files (path -> SHA-256):
the mod loader's, one set per mod, the VR mod's, plus two vanilla files the
setup changes in place (the bundle database the loader patches and the
executable the VR mod patches). A profile says which of those it wants; a
switch computes the difference, stores every byte it is about to replace,
writes a journal, applies, verifies and records the new state. Any failure
rolls back from the journal; a journal left by a crash is recovered before
anything else is allowed.

Two rules protect the user's own work:

1. The game folder is the truth for the ACTIVE profile. Before a switch,
   whatever the user did to their mods while that profile was active (an
   updated mod, a new one, one deleted) is absorbed into the vault and into
   that profile, so it comes back exactly as they left it.
2. Unmanaged files (anything the tool cannot attribute to a component) are
   recorded in captures and never written or removed.
"""
from __future__ import annotations

import ctypes
import datetime as _dt
import os
import shutil
import stat
import subprocess
import uuid
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path

from . import scan as scanmod
from .scan import (BUNDLE_DATABASE, BUNDLE_DATABASE_BACKUP, GAME_EXECUTABLE, VR_MOD,
                   VR_PROXY, component_of)
from .steam import Installation, VanillaIndex, normalize
from .store import Vault, read_json, sha1_file, sha256_file, write_json

SETTINGS_DIRECTORY = Path(os.environ.get("APPDATA", "")) / "Fatshark" / "Darktide"
SETTINGS_FILES = ("user_settings.config", "launcher.config")
SLOT_FILE = "user_settings.config"

BUILT_IN_PROFILES = {
    "vanilla": {"description": "The game as Steam installs it: no mod loader, no mods, no VR.",
                "loader": False, "vr": False, "mods": [], "settings_slot": "2d"},
    "2d": {"description": "Your flat setup: the mod loader and all your mods, no VR.",
           "loader": True, "vr": False, "mods": [], "settings_slot": "2d"},
    "vr": {"description": "VR with nothing else: the mod loader and the VR mod only.",
           "loader": True, "vr": True, "mods": [], "settings_slot": "vr"},
    "vr-mods": {"description": "VR with all of your 2D profile's mods.",
                "loader": True, "vr": True, "mods": {"from": "2d"}, "settings_slot": "vr"},
    "vr-custom": {"description": "VR with mods added a batch at a time (mods add/next/remove).",
                  "loader": True, "vr": True, "mods": [], "settings_slot": "vr"},
}

# The VR mod's session settings (docs/STEAMVR-STEAM-FRAME.md); written into
# the VR component when it is imported.
DEFAULT_VR_FLAGS = {
    "darktidevr_refresh_rate.flag": "90",
    "darktidevr_motion_smoothing.flag": "off",
    "darktidevr_hide_dashboard.flag": "on",
    "darktidevr_eye_extent.flag": "2160x2160",
}


class ProfileError(RuntimeError):
    pass


def now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


# --- Is Darktide running? ---------------------------------------------------

def running_game_processes(game_root: Path) -> list[str]:
    """Any process whose image lives in the game folder (the game, its
    launcher, the crash reporter, the VR viewer if installed there)."""
    psapi = ctypes.WinDLL("psapi")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ids = (wintypes.DWORD * 8192)()
    needed = wintypes.DWORD()
    if not psapi.EnumProcesses(ids, ctypes.sizeof(ids), ctypes.byref(needed)):
        raise ProfileError("cannot list running processes")
    prefix = os.path.normcase(str(Path(game_root).resolve())) + os.sep
    found = []
    for index in range(needed.value // ctypes.sizeof(wintypes.DWORD)):
        handle = kernel32.OpenProcess(0x1000, False, ids[index])  # QUERY_LIMITED_INFORMATION
        if not handle:
            continue
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buffer))
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                image = buffer.value
                if os.path.normcase(image).startswith(prefix) or \
                        os.path.basename(image).lower() == "darktide.exe":
                    found.append(image)
        finally:
            kernel32.CloseHandle(handle)
    return found


# --- Derived files: vanilla copies and patched versions -----------------------

@dataclass
class Tools:
    """How the two in-place patches are made. Tests replace these."""
    loader_patch: object = None      # (staged bundle dir) -> None, patches in place
    vr_exe_patch: object = None      # (staged exe, backup path, tool) -> None


def run_dtkit_patch(game_root: Path):
    def patch(staged_bundle: Path) -> None:
        tool = game_root / "tools" / "dtkit-patch.exe"
        if not tool.is_file():
            raise ProfileError(f"{tool} is missing; the mod loader is not installed")
        result = subprocess.run([str(tool), "--patch", str(staged_bundle)],
                                capture_output=True, text=True, timeout=120,
                                stdin=subprocess.DEVNULL)
        if result.returncode != 0:
            raise ProfileError("dtkit-patch failed: " + (result.stdout + result.stderr).strip())
    return patch


def run_vr_exe_patch(staged_exe: Path, backup: Path, tool: Path) -> None:
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(tool),
         "-Action", "Apply", "-GameExe", str(staged_exe), "-Backup", str(backup)],
        capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    if result.returncode != 0:
        # PowerShell wraps a thrown message in its error record; the message
        # itself follows "FullyQualifiedErrorId :".
        lines = (result.stdout + result.stderr).strip().splitlines()
        reason = next((line.split(":", 1)[1].strip() for line in lines
                       if "FullyQualifiedErrorId" in line), lines[-1].strip() if lines else "")
        raise ProfileError("the VR mod's executable patch does not support this Darktide "
                           f"build yet ({reason}); VR profiles wait for a patch update")


# --- The manager ------------------------------------------------------------

@dataclass
class Plan:
    profile: str
    writes: dict[str, tuple[str, str]] = field(default_factory=dict)   # key -> (relative, sha)
    removes: dict[str, str] = field(default_factory=dict)              # key -> relative
    settings: tuple[str, str | None] | None = None                     # (slot, sha or None)
    notes: list[str] = field(default_factory=list)
    readonly: set[str] = field(default_factory=set)          # keys to leave read-only
    readonly_before: set[str] = field(default_factory=set)   # keys read-only now

    @property
    def empty(self) -> bool:
        return not self.writes and not self.removes and self.settings is None


class Manager:
    def __init__(self, vault: Vault, installation: Installation, vanilla: VanillaIndex,
                 settings_directory: Path = SETTINGS_DIRECTORY, tools: Tools | None = None,
                 log=print):
        self.vault = vault
        self.installation = installation
        self.vanilla = vanilla
        self.root = installation.game_root
        self.settings_directory = Path(settings_directory)
        self.tools = tools or Tools(loader_patch=run_dtkit_patch(installation.game_root),
                                    vr_exe_patch=run_vr_exe_patch)
        self.log = log
        vault.ensure()

    # --- vault documents ----------------------------------------------------

    def library(self) -> dict:
        return read_json(self.vault.library_path, {"components": {}})

    def save_library(self, library: dict) -> None:
        write_json(self.vault.library_path, library)

    def state(self) -> dict | None:
        return read_json(self.vault.state_path)

    def derived(self) -> dict:
        return read_json(self.vault.root / "derived.json", {})

    def save_derived(self, derived: dict) -> None:
        write_json(self.vault.root / "derived.json", derived)

    def profile(self, name: str) -> dict:
        document = read_json(self.vault.profiles / f"{name}.json")
        if document is None:
            raise ProfileError(f"no profile named {name!r} (see: list)")
        return document

    def save_profile(self, name: str, document: dict) -> None:
        document["name"] = name
        write_json(self.vault.profiles / f"{name}.json", document)

    def profile_names(self) -> list[str]:
        return sorted(p.stem for p in self.vault.profiles.glob("*.json"))

    def slots(self) -> dict:
        return read_json(self.vault.settings_path, {})

    def save_slots(self, slots: dict) -> None:
        write_json(self.vault.settings_path, slots)

    def resolve_mods(self, profile: dict, seen: tuple = ()) -> list[str]:
        mods = profile.get("mods", [])
        if isinstance(mods, dict):
            source = mods.get("from")
            if source in seen:
                raise ProfileError(f"profile mods refer to each other: {seen + (source,)}")
            return self.resolve_mods(self.profile(source), seen + (profile.get("name"),))
        return list(mods)

    # --- guards -------------------------------------------------------------

    def guard(self) -> None:
        pending = sorted(self.vault.journal.glob("*.json"))
        if pending:
            raise ProfileError(
                f"an earlier switch did not finish ({pending[0].name}); run `recover` first")
        if not self.installation.fully_installed:
            raise ProfileError("Steam is updating or validating Darktide; wait for it to finish")
        running = running_game_processes(self.root)
        if running:
            raise ProfileError("close Darktide first: " + ", ".join(running))

    # --- scanning -----------------------------------------------------------

    def scan(self, full: bool = False) -> scanmod.GameScan:
        return scanmod.scan_game(self.root, self.vanilla, full=full)

    def managed_current(self, game: scanmod.GameScan) -> dict[str, tuple[str, str]]:
        """Managed keys on disk now: key -> (relative, sha256)."""
        current = {}
        for entry in game.files.values():
            if entry.status not in ("extra", "modified"):
                continue
            if component_of(entry.key) is None:
                continue
            current[entry.key] = (entry.relative, entry.sha256)
        for key, relative in ((BUNDLE_DATABASE, "bundle\\bundle_database.data"),
                              (GAME_EXECUTABLE, "binaries\\Darktide.exe")):
            entry = game.files.get(key)
            if entry is None:
                raise ProfileError(f"{relative} is missing from the game folder")
            current[key] = (entry.relative, entry.sha256 or sha256_file(self.root / entry.relative))
        return current

    # --- vanilla and derived blobs -------------------------------------------

    def vanilla_blob(self, key: str) -> str:
        """The SHA-256 of a stored copy of this build's vanilla `key`."""
        expected = self.vanilla.files[key]
        derived = self.derived()
        known = derived.get("vanilla", {}).get(expected.sha1)
        if known and self.vault.has(known):
            return known
        candidates = [self.root / key]
        if key == BUNDLE_DATABASE:
            candidates.append(self.root / BUNDLE_DATABASE_BACKUP)
        for candidate in candidates:
            if candidate.is_file() and candidate.stat().st_size == expected.size \
                    and sha1_file(candidate) == expected.sha1:
                digest = self.vault.put_file(candidate)
                derived.setdefault("vanilla", {})[expected.sha1] = digest
                self.save_derived(derived)
                return digest
        raise ProfileError(
            f"no vanilla copy of {key} for this build is available (the game folder's copy is "
            "changed and no stored copy matches). In Steam: Darktide > Properties > "
            "Installed Files > Verify integrity, then try again")

    def patched_database(self, vanilla_digest: str) -> str:
        derived = self.derived()
        known = derived.get("loader_patch", {}).get(vanilla_digest)
        if known and self.vault.has(known):
            return known
        stage = self.vault.staging / f"bundle-{uuid.uuid4().hex}"
        try:
            stage.mkdir(parents=True)
            self.vault.copy_out(vanilla_digest, stage / "bundle_database.data")
            self.tools.loader_patch(stage)
            patched = stage / "bundle_database.data"
            if b"patch_999" not in patched.read_bytes():
                raise ProfileError("the loader patch left the bundle database unpatched")
            digest = self.vault.put_file(patched)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        derived.setdefault("loader_patch", {})[vanilla_digest] = digest
        self.save_derived(derived)
        return digest

    def patched_executable(self, vanilla_digest: str, vr_files: dict) -> str:
        derived = self.derived()
        known = derived.get("vr_exe_patch", {}).get(vanilla_digest)
        if known and self.vault.has(known):
            return known
        tool_key = f"mods\\{VR_MOD}\\tools\\set-skinner-assert-patch.ps1"
        if tool_key not in vr_files:
            raise ProfileError("the VR component has no executable patch tool")
        stage = self.vault.staging / f"exe-{uuid.uuid4().hex}"
        try:
            stage.mkdir(parents=True)
            self.vault.copy_out(vanilla_digest, stage / "Darktide.exe")
            self.vault.copy_out(vr_files[tool_key]["sha256"], stage / "set-skinner-assert-patch.ps1")
            self.tools.vr_exe_patch(stage / "Darktide.exe", stage / "pristine.exe",
                                    stage / "set-skinner-assert-patch.ps1")
            digest = self.vault.put_file(stage / "Darktide.exe")
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        if digest == vanilla_digest:
            raise ProfileError("the VR executable patch changed nothing")
        derived.setdefault("vr_exe_patch", {})[vanilla_digest] = digest
        self.save_derived(derived)
        return digest

    # --- capture ------------------------------------------------------------

    def capture(self, label: str = "") -> tuple[str, dict]:
        """Store everything that is not vanilla, and the settings; immutable."""
        game = self.scan()
        files = {}
        for entry in game.files.values():
            if entry.status == "vanilla":
                continue
            path = self.root / entry.relative
            digest = self.vault.put_file(path)
            files[entry.key] = {"relative": entry.relative, "sha256": digest,
                                "size": entry.size, "status": entry.status,
                                "component": component_of(entry.key, entry.relative)}
            if entry.readonly:
                files[entry.key]["readonly"] = True
        for key in (BUNDLE_DATABASE, GAME_EXECUTABLE):
            if key not in files:
                entry = game.files[key]
                files[key] = {"relative": entry.relative,
                              "sha256": self.vault.put_file(self.root / entry.relative),
                              "size": entry.size, "status": "vanilla",
                              "component": component_of(key)}
                derived = self.derived()
                derived.setdefault("vanilla", {})[self.vanilla.files[key].sha1] = \
                    files[key]["sha256"]
                self.save_derived(derived)
        settings = {}
        for name in SETTINGS_FILES:
            path = self.settings_directory / name
            if path.is_file():
                settings[name] = self.vault.put_file(path)
        database = (self.root / BUNDLE_DATABASE).read_bytes()
        state = self.state()
        capture_id = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        document = {
            "id": capture_id, "label": label, "captured": now(),
            "game_root": str(self.root), "buildid": self.installation.buildid,
            "depots": self.installation.depots,
            "bundle_database_patched": b"patch_999" in database,
            "active_profile": state.get("profile") if state else None,
            "settings_slot": state.get("settings_slot") if state else None,
            "files": files, "settings": settings,
        }
        write_json(self.vault.captures / f"{capture_id}.json", document)
        return capture_id, document

    def initialize(self, label: str = "first capture") -> str:
        """The first capture: also builds the library, the built-in profiles
        and the 2D settings slot from what is installed now."""
        if self.state() is not None:
            raise ProfileError("already initialized; use `capture` for another restore point")
        capture_id, document = self.capture(label)
        library = {"components": {}}
        mods: set[str] = set()
        for key, item in document["files"].items():
            component = item["component"]
            if component in (None, "executable") or key in (BUNDLE_DATABASE,
                                                            BUNDLE_DATABASE_BACKUP):
                continue
            if item["status"] == "generated":
                continue
            library["components"].setdefault(component, {"files": {}})["files"][key] = {
                "relative": item["relative"], "sha256": item["sha256"], "size": item["size"]}
            if item.get("readonly"):
                library["components"][component]["files"][key]["readonly"] = True
            if component.startswith("mod:"):
                mods.add(component[4:])
        for component in library["components"].values():
            component["updated"] = now()
        self.save_library(library)
        for name, template in BUILT_IN_PROFILES.items():
            profile = dict(template)
            if name == "2d":
                profile["mods"] = sorted(mods, key=str.lower)
                profile["loader"] = "loader" in library["components"]
            self.save_profile(name, profile)
        slots = {}
        if SLOT_FILE in document["settings"]:
            slots["2d"] = {"sha256": document["settings"][SLOT_FILE], "saved": now()}
        self.save_slots(slots)
        applied = {key: item["sha256"] for key, item in document["files"].items()
                   if item["component"] is not None and item["status"] != "generated"}
        write_json(self.vault.state_path, {
            "profile": "2d", "settings_slot": "2d", "applied": applied,
            "buildid": self.installation.buildid, "since": now(), "capture": capture_id})
        return capture_id

    # --- absorbing the user's changes ------------------------------------------

    def absorb(self, game: scanmod.GameScan, allow_structure: bool = False) -> list[str]:
        """Fold what is in the game folder into the active profile."""
        state = self.state()
        if state is None:
            raise ProfileError("not initialized; run `init` first")
        active = state["profile"]
        notes: list[str] = []
        library = self.library()
        components = library["components"]
        present: dict[str, dict] = {}
        for entry in game.files.values():
            if entry.status not in ("extra", "modified"):
                continue
            component = component_of(entry.key, entry.relative)
            if component in (None, "executable") or entry.key in (BUNDLE_DATABASE,
                                                                  BUNDLE_DATABASE_BACKUP,
                                                                  VR_PROXY):
                continue
            present.setdefault(component, {})[entry.key] = entry
        if active.startswith("capture:"):
            return notes
        profile = self.profile(active)
        # Structure: the loader or the VR mod appearing or disappearing is not
        # something to fold in silently.
        for component, flag in (("loader", "loader"), ("vr", "vr")):
            if bool(present.get(component)) != bool(profile.get(flag)):
                change = "appeared" if present.get(component) else "disappeared"
                if not allow_structure:
                    raise ProfileError(
                        f"the {component} files {change} while profile {active!r} was active. "
                        "Run `status` to see them; pass --absorb to make that part of the "
                        f"profile, or `restore-capture` to undo it")
                profile[flag] = bool(present.get(component))
                notes.append(f"profile {active}: {component} {change}, recorded")
        # Contents: updated, added or removed files are taken as they are.
        for component, entries in present.items():
            files = {key: {"relative": e.relative, "sha256": self.vault.put_file(
                         self.root / e.relative, expected=e.sha256), "size": e.size}
                     for key, e in entries.items()}
            for key, e in entries.items():
                if e.readonly:
                    files[key]["readonly"] = True
            old = components.get(component, {}).get("files")
            if old != files:
                if old is not None:
                    library.setdefault("history", []).append(
                        {"component": component, "files": old, "replaced": now()})
                components[component] = {"files": files, "updated": now()}
                notes.append(f"{component}: {'updated' if old is not None else 'new'}, stored")
        mods_present = sorted((c[4:] for c in present if c.startswith("mod:")), key=str.lower)
        declared = self.resolve_mods(profile)
        if sorted(declared, key=str.lower) != mods_present:
            added = sorted(set(mods_present) - set(declared))
            removed = sorted(set(declared) - set(mods_present))
            if isinstance(profile.get("mods"), dict):
                notes.append(f"profile {active}: its mods followed {profile['mods']['from']!r}; "
                             "they are now its own list")
            profile["mods"] = mods_present
            if added:
                notes.append(f"profile {active}: mods added in the game folder: {', '.join(added)}")
            if removed:
                notes.append(f"profile {active}: mods removed in the game folder: "
                             f"{', '.join(removed)} (kept in the vault)")
        self.save_library(library)
        self.save_profile(active, profile)
        # Settings: the file in use belongs to the active slot.
        settings_file = self.settings_directory / SLOT_FILE
        if settings_file.is_file():
            slots = self.slots()
            digest = self.vault.put_file(settings_file)
            slot = state.get("settings_slot", "2d")
            if slots.get(slot, {}).get("sha256") != digest:
                if slot in slots:
                    slots.setdefault("history", []).append(dict(slots[slot], slot=slot))
                slots[slot] = {"sha256": digest, "saved": now()}
                self.save_slots(slots)
        return notes

    # --- planning -------------------------------------------------------------

    def desired(self, profile: dict) -> tuple[dict[str, tuple[str, str]], list[str]]:
        library = self.library()["components"]
        want: dict[str, tuple[str, str]] = {}
        notes: list[str] = []
        self._want_readonly: set[str] = set()

        def add_component(name: str) -> dict:
            component = library.get(name)
            if component is None:
                raise ProfileError(f"the vault has no {name!r} (see: list)")
            for key, item in component["files"].items():
                want[key] = (item["relative"], item["sha256"])
                if item.get("readonly"):
                    self._want_readonly.add(key)
            return component["files"]

        vanilla_database = self.vanilla_blob(BUNDLE_DATABASE)
        vanilla_executable = self.vanilla_blob(GAME_EXECUTABLE)
        if profile.get("loader"):
            add_component("loader")
            want[BUNDLE_DATABASE] = ("bundle\\bundle_database.data",
                                     self.patched_database(vanilla_database))
            # What dtkit-patch itself leaves: the unpatched database of THIS
            # build, so its own toggle can never restore an older one.
            want[BUNDLE_DATABASE_BACKUP] = ("bundle\\bundle_database.data.bak", vanilla_database)
        else:
            want[BUNDLE_DATABASE] = ("bundle\\bundle_database.data", vanilla_database)
        for mod in self.resolve_mods(profile):
            add_component(f"mod:{mod}")
        if profile.get("vr"):
            vr_files = add_component("vr")
            proxy = vr_files.get(f"mods\\{VR_MOD}\\bin\\d3d12.dll")
            if proxy is None:
                raise ProfileError("the VR component has no bin\\d3d12.dll")
            want[VR_PROXY] = ("binaries\\d3d12.dll", proxy["sha256"])
            want[GAME_EXECUTABLE] = ("binaries\\Darktide.exe",
                                     self.patched_executable(vanilla_executable, vr_files))
        else:
            want[GAME_EXECUTABLE] = ("binaries\\Darktide.exe", vanilla_executable)
        if profile.get("vr") and not profile.get("loader"):
            notes.append("the VR mod needs the mod loader; this profile has it off")
        return want, notes

    def plan(self, name: str, game: scanmod.GameScan) -> Plan:
        profile = self.profile(name)
        want, notes = self.desired(profile)
        have = self.managed_current(game)
        plan = Plan(profile=name, notes=notes)
        plan.readonly = set(self._want_readonly)
        readonly_now = {e.key for e in game.files.values() if e.readonly}
        plan.readonly_before = readonly_now
        for key, (relative, digest) in want.items():
            if have.get(key, (None, None))[1] != digest or \
                    ((key in plan.readonly) != (key in readonly_now)):
                plan.writes[key] = (have.get(key, (relative,))[0] if key in have else relative,
                                    digest)
        for key, (relative, _) in have.items():
            if key not in want:
                plan.removes[key] = relative
        state = self.state() or {}
        slot = profile.get("settings_slot", "2d")
        if slot != state.get("settings_slot"):
            slots = self.slots()
            target = slots.get(slot, {}).get("sha256")
            if target is None:
                # A new slot starts from the settings in use, mod settings
                # included.
                current = self.settings_directory / SLOT_FILE
                target = self.vault.put_file(current) if current.is_file() else None
                plan.notes.append(f"settings slot {slot!r} is new; it starts from the "
                                  f"{state.get('settings_slot')!r} settings")
            plan.settings = (slot, target)
        return plan

    # --- applying ---------------------------------------------------------------

    def _write(self, relative: str, digest: str | None, readonly: bool | None = None) -> None:
        """Make `relative` hold `digest` (None: absent), then set its
        read-only attribute if `readonly` is given. Already right is left
        alone, so a rollback or a recovery can be repeated."""
        path = self.root / relative

        def make_writable():
            if path.exists() and not os.access(path, os.W_OK):
                os.chmod(path, stat.S_IWRITE | stat.S_IREAD)

        if digest is None:
            if path.exists():
                make_writable()
                path.unlink()
            return
        if not (path.is_file() and sha256_file(path) == digest):
            make_writable()
            self.vault.copy_out(digest, path)
        if readonly is not None and readonly != (not os.access(path, os.W_OK)):
            os.chmod(path, stat.S_IREAD if readonly else stat.S_IWRITE | stat.S_IREAD)

    def _remove_empty_directories(self, relatives) -> None:
        for relative in sorted(relatives, key=lambda r: -r.count("\\")):
            directory = (self.root / relative).parent
            while directory != self.root:
                key = normalize(os.path.relpath(directory, self.root))
                if key in self.vanilla.directories or key in ("mods", "binaries", "bundle"):
                    break
                try:
                    directory.rmdir()
                except OSError:
                    break
                directory = directory.parent

    def execute(self, plan: Plan, have: dict[str, tuple[str, str]], label: str,
                new_state: dict) -> dict:
        """Apply a plan under a journal; roll back on any failure. The new
        state is recorded before the journal is retired, so a crash in
        between leaves a journal to recover rather than a state that lies."""
        operations = []
        for key, (relative, digest) in plan.writes.items():
            operations.append({"key": key, "relative": relative,
                               "before": have.get(key, (None, None))[1], "after": digest,
                               "before_readonly": key in plan.readonly_before,
                               "after_readonly": key in plan.readonly})
        for key, relative in plan.removes.items():
            operations.append({"key": key, "relative": relative,
                               "before": have[key][1], "after": None,
                               "before_readonly": key in plan.readonly_before})
        for operation in operations:
            before = operation["before"]
            if before and not self.vault.has(before):
                self.vault.put_file(self.root / operation["relative"], expected=before)
        settings_operation = None
        if plan.settings is not None:
            slot, target = plan.settings
            current = self.settings_directory / SLOT_FILE
            settings_operation = {
                "path": str(current), "slot": slot, "after": target,
                "before": self.vault.put_file(current) if current.is_file() else None}
        journal_id = f"{_dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        journal = {"id": journal_id, "label": label, "profile": plan.profile, "started": now(),
                   "operations": operations, "settings": settings_operation,
                   "previous_state": self.state()}
        journal_path = self.vault.journal / f"{journal_id}.json"
        write_json(journal_path, journal)
        done = []
        try:
            for operation in operations:
                self._write(operation["relative"], operation["after"],
                            operation.get("after_readonly"))
                done.append(operation)
            self._remove_empty_directories(op["relative"] for op in operations
                                           if op["after"] is None)
            if settings_operation and settings_operation["after"]:
                self.vault.copy_out(settings_operation["after"], Path(settings_operation["path"]))
            self.verify_applied(plan, operations)
            write_json(self.vault.state_path, new_state)
        except BaseException as error:
            self.log(f"switch failed ({error}); rolling back")
            self.rollback(journal)
            raise
        result = dict(journal, finished=now(), outcome="committed")
        write_json(self.vault.history / f"{journal_id}.json", result)
        journal_path.unlink()
        return result

    def verify_applied(self, plan: Plan, operations: list[dict]) -> None:
        for operation in operations:
            path = self.root / operation["relative"]
            if operation["after"] is None:
                if path.exists():
                    raise ProfileError(f"{operation['relative']} is still present")
            elif not path.is_file() or sha256_file(path) != operation["after"]:
                raise ProfileError(f"{operation['relative']} does not hold what was written")

    def rollback(self, journal: dict) -> list[str]:
        problems = []
        for operation in reversed(journal["operations"]):
            try:
                self._write(operation["relative"], operation["before"],
                            operation.get("before_readonly"))
            except Exception as error:  # keep going: restore as much as possible
                problems.append(f"{operation['relative']}: {error}")
        settings = journal.get("settings")
        if settings and settings.get("before"):
            try:
                self.vault.copy_out(settings["before"], Path(settings["path"]))
            except Exception as error:
                problems.append(f"settings: {error}")
        self._remove_empty_directories(op["relative"] for op in journal["operations"]
                                       if op["before"] is None)
        if journal.get("previous_state") is not None:
            write_json(self.vault.state_path, journal["previous_state"])
        for operation in journal["operations"]:
            path = self.root / operation["relative"]
            ok = (not path.exists()) if operation["before"] is None else \
                (path.is_file() and sha256_file(path) == operation["before"])
            if not ok:
                problems.append(f"{operation['relative']} did not roll back")
        outcome = "rolled_back" if not problems else "rollback_incomplete"
        write_json(self.vault.history / f"{journal['id']}.json",
                   dict(journal, finished=now(), outcome=outcome, problems=problems))
        journal_path = self.vault.journal / f"{journal['id']}.json"
        if not problems:
            journal_path.unlink(missing_ok=True)
        return problems

    def recover(self) -> list[str]:
        messages = []
        for path in sorted(self.vault.journal.glob("*.json")):
            journal = read_json(path)
            problems = self.rollback(journal)
            if problems:
                messages.append(f"{path.name}: rollback incomplete: " + "; ".join(problems))
            else:
                messages.append(f"{path.name}: rolled back to before {journal['profile']!r}")
        return messages

    # --- the commands ---------------------------------------------------------

    def switch(self, name: str, allow_structure: bool = False, dry_run: bool = False,
               confirm=None) -> tuple[Plan, list[str]]:
        self.guard()
        self.profile(name)
        game = self.scan()
        # A dry run must not change the vault's view of the profile either.
        notes = [] if dry_run else self.absorb(game, allow_structure=allow_structure)
        plan = self.plan(name, game)
        plan.notes[:0] = notes
        if dry_run:
            return plan, []
        if confirm is not None and not plan.empty and not confirm(plan):
            raise ProfileError("cancelled")
        have = self.managed_current(game)
        state = self.state() or {}
        new_state = {"profile": name,
                     "settings_slot": self.profile(name).get("settings_slot", "2d"),
                     "applied": self._after(have, plan),
                     "buildid": self.installation.buildid, "since": now(),
                     "previous": state.get("profile")}
        if plan.settings is not None:
            slots = self.slots()
            slot, target = plan.settings
            if target and slot not in slots:
                slots[slot] = {"sha256": target, "saved": now()}
                self.save_slots(slots)
        if plan.empty:
            write_json(self.vault.state_path, new_state)
        else:
            self.execute(plan, have, label=f"switch to {name}", new_state=new_state)
        return plan, []

    @staticmethod
    def _after(have: dict[str, tuple[str, str]], plan: Plan) -> dict[str, str]:
        after = {key: digest for key, (_, digest) in have.items() if key not in plan.removes}
        after.update({key: digest for key, (_, digest) in plan.writes.items()})
        return after

    def restore_capture(self, capture_id: str, dry_run: bool = False,
                        confirm=None) -> Plan:
        """Put every managed file, and the settings, back as captured."""
        self.guard()
        document = read_json(self.vault.captures / f"{capture_id}.json")
        if document is None:
            raise ProfileError(f"no capture {capture_id!r}")
        game = self.scan()
        have = self.managed_current(game)
        want = {key: (item["relative"], item["sha256"]) for key, item in document["files"].items()
                if item["component"] is not None and item["status"] != "generated"}
        plan = Plan(profile=f"capture:{capture_id}")
        plan.readonly = {key for key, item in document["files"].items() if item.get("readonly")}
        plan.readonly_before = {e.key for e in game.files.values() if e.readonly}
        if document["buildid"] != self.installation.buildid:
            # Never put another build's database or executable over this one.
            database_patched = document.get("bundle_database_patched")
            vanilla_database = self.vanilla_blob(BUNDLE_DATABASE)
            want[BUNDLE_DATABASE] = ("bundle\\bundle_database.data",
                                     self.patched_database(vanilla_database)
                                     if database_patched else vanilla_database)
            if database_patched:
                want[BUNDLE_DATABASE_BACKUP] = ("bundle\\bundle_database.data.bak",
                                                vanilla_database)
            else:
                want.pop(BUNDLE_DATABASE_BACKUP, None)
            want[GAME_EXECUTABLE] = ("binaries\\Darktide.exe", self.vanilla_blob(GAME_EXECUTABLE))
            plan.notes.append(f"captured on build {document['buildid']}, now "
                              f"{self.installation.buildid}: the bundle database and executable "
                              "follow this build")
        for key, (relative, digest) in want.items():
            if have.get(key, (None, None))[1] != digest or \
                    ((key in plan.readonly) != (key in plan.readonly_before)):
                plan.writes[key] = (have[key][0] if key in have else relative, digest)
        for key, (relative, _) in have.items():
            if key not in want:
                plan.removes[key] = relative
        settings = document.get("settings", {}).get(SLOT_FILE)
        current = self.settings_directory / SLOT_FILE
        if settings and (not current.is_file() or sha256_file(current) != settings):
            plan.settings = (document.get("settings_slot") or "2d", settings)
        if dry_run:
            return plan
        if confirm is not None and not plan.empty and not confirm(plan):
            raise ProfileError("cancelled")
        new_state = {"profile": document.get("active_profile") or "2d",
                     "settings_slot": document.get("settings_slot") or "2d",
                     "applied": self._after(have, plan),
                     "buildid": self.installation.buildid, "since": now(),
                     "restored_capture": capture_id}
        if plan.empty:
            write_json(self.vault.state_path, new_state)
        else:
            self.execute(plan, have, label=f"restore capture {capture_id}", new_state=new_state)
        return plan

    # --- the VR component -------------------------------------------------------

    def import_vr(self, package: Path, flags: dict[str, str] | None = None) -> int:
        """Take the VR mod from a runtime package (zip or extracted folder)."""
        import zipfile
        package = Path(package)
        stage = self.vault.staging / f"vr-{uuid.uuid4().hex}"
        try:
            if package.is_file():
                with zipfile.ZipFile(package) as archive:
                    for member in archive.infolist():
                        target = (stage / member.filename).resolve()
                        if not str(target).startswith(str(stage.resolve())):
                            raise ProfileError(f"package path escapes: {member.filename}")
                    archive.extractall(stage)
                root = stage
            else:
                root = package
            mod = root / "mods" / VR_MOD
            if not (mod / f"{VR_MOD}.mod").is_file():
                raise ProfileError(f"{package} has no mods\\{VR_MOD}\\{VR_MOD}.mod")
            files = {}
            for path in mod.rglob("*"):
                if path.is_file():
                    relative = os.path.relpath(path, root)
                    files[normalize(relative)] = {"relative": relative,
                                                  "sha256": self.vault.put_file(path),
                                                  "size": path.stat().st_size}
            for name, value in (DEFAULT_VR_FLAGS if flags is None else flags).items():
                relative = f"mods\\{VR_MOD}\\{name}"
                files[normalize(relative)] = {"relative": relative,
                                              "sha256": self.vault.put_bytes(value.encode("ascii")),
                                              "size": len(value)}
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        library = self.library()
        old = library["components"].get("vr")
        if old:
            library.setdefault("history", []).append(
                {"component": "vr", "files": old["files"], "replaced": now()})
        library["components"]["vr"] = {"files": files, "updated": now(),
                                       "source": str(package)}
        self.save_library(library)
        return len(files)
