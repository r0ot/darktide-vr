"""Darktide game-folder profiles. Run `python -m dtprofiles --help`.

Everything that changes the game folder shows its plan and asks first, unless
--yes is given. Darktide must be closed and Steam must not be updating it.
"""
from __future__ import annotations

import argparse
import collections
import os
import sys
from pathlib import Path

from . import scan as scanmod
from .engine import Manager, Plan, ProfileError
from .steam import SteamError, find_installation, load_vanilla_index
from .store import Vault, read_json, sha256_file

DEFAULT_VAULT = Path(os.environ.get("LOCALAPPDATA", ".")) / "DarktideProfiles"


def describe(plan: Plan) -> str:
    lines = [f"Plan for {plan.profile}:"]
    special = {scanmod.BUNDLE_DATABASE: "bundle database", scanmod.GAME_EXECUTABLE: "Darktide.exe",
               scanmod.BUNDLE_DATABASE_BACKUP: "bundle database backup",
               scanmod.VR_PROXY: "d3d12.dll proxy"}
    by_component = collections.defaultdict(lambda: [0, 0])
    for key, (relative, _) in plan.writes.items():
        if key not in special:
            by_component[scanmod.component_of(key, relative) or "?"][0] += 1
    for key, relative in plan.removes.items():
        if key not in special:
            by_component[scanmod.component_of(key, relative) or "?"][1] += 1
    for key, name in special.items():
        if key in plan.writes:
            lines.append(f"  write   {name}")
        if key in plan.removes:
            lines.append(f"  remove  {name}")
    for component in sorted(by_component, key=str.lower):
        writes, removes = by_component[component]
        parts = []
        if writes:
            parts.append(f"{writes} file(s) written")
        if removes:
            parts.append(f"{removes} file(s) removed")
        lines.append(f"  {component:<40} {', '.join(parts)}")
    if plan.settings is not None:
        lines.append(f"  settings: user_settings.config from slot {plan.settings[0]!r}")
    for note in plan.notes:
        lines.append(f"  note: {note}")
    if plan.empty:
        lines.append("  nothing to change")
    return "\n".join(lines)


def ask(plan: Plan) -> bool:
    print(describe(plan))
    try:
        return input("Type yes to apply: ").strip().lower() == "yes"
    except EOFError:
        return False


def make_manager(args) -> Manager:
    installation = find_installation(Path(args.game_root) if args.game_root else None)
    vanilla = load_vanilla_index(installation)
    return Manager(Vault(Path(args.vault)), installation, vanilla)


def command_status(manager: Manager, args) -> int:
    inst = manager.installation
    print(f"game:      {inst.game_root}")
    print(f"build:     {inst.buildid}{'' if inst.fully_installed else '  (Steam is updating it)'}")
    print(f"vault:     {manager.vault.root}")
    pending = sorted(manager.vault.journal.glob("*.json"))
    if pending:
        print(f"WARNING:   an interrupted switch needs `recover` ({pending[0].name})")
    state = manager.state()
    if state is None:
        print("profile:   (not initialized; run `init`)")
        return 0
    print(f"profile:   {state['profile']}  (settings slot {state.get('settings_slot')}, "
          f"since {state.get('since')})")
    if state.get("buildid") != inst.buildid:
        print(f"note:      the game updated since the last switch "
              f"({state.get('buildid')} -> {inst.buildid})")
    game = manager.scan(full=args.full)
    have = manager.managed_current(game)
    patched = b"patch_999" in (manager.root / scanmod.BUNDLE_DATABASE).read_bytes()
    loader_present = "binaries\\mod_loader" in have
    print(f"loader:    bundle database {'patched' if patched else 'not patched'}; "
          f"mod_loader {'present' if loader_present else 'absent'}")
    if loader_present and not patched:
        print("           -> mods will NOT load: the bundle database is unpatched (a game update "
              "resets it). Switching to a modded profile patches it.")
    executable = game.files[scanmod.GAME_EXECUTABLE]
    print(f"exe:       {'vanilla' if executable.status == 'vanilla' else 'CHANGED (VR patch?)'}")
    mods = sorted({scanmod.component_of(k, relative)[4:] for k, (relative, _) in have.items()
                   if (scanmod.component_of(k, relative) or "").startswith("mod:")},
                  key=str.lower)
    print(f"mods:      {len(mods)} folder(s){': ' + ', '.join(mods) if args.verbose else ''}")
    applied = state.get("applied", {})
    drift = collections.defaultdict(list)
    for key in set(applied) | set(have):
        if applied.get(key) != have.get(key, (None, None))[1]:
            relative = have[key][0] if key in have else None
            drift[scanmod.component_of(key, relative) or "?"].append(key)
    if drift:
        print("changed since the last switch (absorbed into the profile at the next one):")
        for component in sorted(drift):
            print(f"  {component}: {len(drift[component])} file(s)")
    if game.missing_vanilla:
        print(f"WARNING:   {len(game.missing_vanilla)} vanilla file(s) missing; verify in Steam")
    unmanaged = [e.relative for e in game.changed() if scanmod.component_of(e.key) is None]
    if unmanaged:
        print(f"unmanaged: {', '.join(unmanaged)} (never touched)")
    return 0


def command_init(manager: Manager, args) -> int:
    manager.guard()
    capture_id = manager.initialize(args.label or "first capture")
    document = read_json(manager.vault.captures / f"{capture_id}.json")
    counts = collections.Counter(item["component"] or "unmanaged"
                                 for item in document["files"].values())
    print(f"captured {len(document['files'])} file(s) as {capture_id}:")
    print(f"  loader: {counts.get('loader', 0)}, mods: "
          f"{sum(v for k, v in counts.items() if k.startswith('mod:'))} in "
          f"{len([k for k in counts if k.startswith('mod:')])} folder(s), "
          f"unmanaged: {counts.get('unmanaged', 0)}")
    print(f"  settings: {', '.join(document['settings'])}")
    print(f"  bundle database patched: {document['bundle_database_patched']}")
    print("profiles: " + ", ".join(manager.profile_names()))
    return 0


def command_capture(manager: Manager, args) -> int:
    manager.guard()
    capture_id, document = manager.capture(args.label or "")
    print(f"captured {len(document['files'])} file(s) as {capture_id}")
    return 0


def command_captures(manager: Manager, args) -> int:
    for path in sorted(manager.vault.captures.glob("*.json")):
        document = read_json(path)
        print(f"{document['id']}  build {document['buildid']}  profile "
              f"{document.get('active_profile') or '-':<10} {len(document['files']):>5} files  "
              f"{document.get('label', '')}")
    return 0


def command_list(manager: Manager, args) -> int:
    state = manager.state() or {}
    for name in manager.profile_names():
        profile = manager.profile(name)
        mods = profile.get("mods")
        mod_text = f"mods of {mods['from']!r}" if isinstance(mods, dict) else f"{len(mods)} mod(s)"
        marker = "*" if state.get("profile") == name else " "
        print(f"{marker} {name:<10} loader={'on ' if profile.get('loader') else 'off'} "
              f"vr={'on ' if profile.get('vr') else 'off'} {mod_text:<16} "
              f"slot={profile.get('settings_slot')}  {profile.get('description', '')}")
    library = manager.library()["components"]
    print(f"vault: {len([c for c in library if c.startswith('mod:')])} mod(s), "
          f"loader {'stored' if 'loader' in library else 'missing'}, "
          f"VR mod {'stored' if 'vr' in library else 'not imported'}")
    return 0


def command_plan(manager: Manager, args) -> int:
    plan, _ = manager.switch(args.profile, dry_run=True)
    print(describe(plan))
    return 0


def command_switch(manager: Manager, args) -> int:
    plan, _ = manager.switch(args.profile, allow_structure=args.absorb,
                             confirm=None if args.yes else ask)
    if args.yes:
        print(describe(plan))
    print(f"now on profile {args.profile}")
    return 0


def command_restore_capture(manager: Manager, args) -> int:
    capture = args.capture
    if capture == "first":
        captures = sorted(manager.vault.captures.glob("*.json"))
        if not captures:
            raise ProfileError("no captures")
        capture = captures[0].stem
    plan = manager.restore_capture(capture, confirm=None if args.yes else ask)
    if args.yes:
        print(describe(plan))
    print(f"restored capture {capture}")
    return 0


def command_recover(manager: Manager, args) -> int:
    messages = manager.recover()
    print("\n".join(messages) if messages else "nothing to recover")
    return 0


def command_mods(manager: Manager, args) -> int:
    profile = manager.profile(args.profile)
    current = manager.resolve_mods(profile)
    available = sorted((c[4:] for c in manager.library()["components"] if c.startswith("mod:")),
                       key=str.lower)
    if args.action == "list":
        print(f"{args.profile}: {len(current)} of {len(available)} mod(s)")
        for mod in available:
            print(f"  [{'x' if mod in current else ' '}] {mod}")
        return 0
    if isinstance(profile.get("mods"), dict):
        raise ProfileError(f"{args.profile}'s mods follow {profile['mods']['from']!r}; "
                           "change that profile instead")
    names = list(args.names)
    unknown = [n for n in names if n not in available]
    if unknown:
        raise ProfileError("not in the vault: " + ", ".join(unknown))
    if args.action == "add":
        current += [n for n in names if n not in current]
    elif args.action == "remove":
        current = [m for m in current if m not in names]
    elif args.action == "next":
        source = manager.resolve_mods(manager.profile(args.source))
        count = int(names[0]) if names else 1
        pending = [m for m in sorted(source, key=str.lower) if m not in current]
        added = pending[:count]
        current += added
        print("added: " + (", ".join(added) if added else "nothing (all added)"))
    elif args.action == "clear":
        current = []
    profile["mods"] = sorted(current, key=str.lower)
    manager.save_profile(args.profile, profile)
    print(f"{args.profile}: {len(current)} mod(s). Apply with: switch {args.profile}")
    return 0


def command_vr_import(manager: Manager, args) -> int:
    count = manager.import_vr(Path(args.package))
    print(f"VR mod stored: {count} file(s) from {args.package}")
    return 0


def command_verify(manager: Manager, args) -> int:
    bad = []
    referenced = set()
    for component in manager.library()["components"].values():
        referenced.update(item["sha256"] for item in component["files"].values())
    for path in manager.vault.captures.glob("*.json"):
        document = read_json(path)
        referenced.update(item["sha256"] for item in document["files"].values())
        referenced.update(document.get("settings", {}).values())
    for digest in sorted(referenced):
        if not manager.vault.verify_blob(digest):
            bad.append(digest)
    print(f"vault: {len(referenced)} stored file(s) checked, {len(bad)} damaged or missing")
    for digest in bad:
        print(f"  {digest}")
    return 1 if bad else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="dtprofiles", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vault", default=str(DEFAULT_VAULT))
    parser.add_argument("--game-root")
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status", help="what is installed and what changed")
    status.add_argument("--full", action="store_true", help="check every vanilla file's hash")
    status.add_argument("-v", "--verbose", action="store_true")
    init = sub.add_parser("init", help="first capture; builds the profiles from what is installed")
    init.add_argument("--label")
    capture = sub.add_parser("capture", help="another restore point")
    capture.add_argument("--label")
    sub.add_parser("captures", help="list restore points")
    sub.add_parser("list", help="profiles and the vault's mods")
    plan = sub.add_parser("plan", help="what a switch would change (changes nothing)")
    plan.add_argument("profile")
    switch = sub.add_parser("switch", help="make the game folder match a profile")
    switch.add_argument("profile")
    switch.add_argument("--yes", action="store_true", help="do not ask")
    switch.add_argument("--absorb", action="store_true",
                        help="accept the loader or VR mod appearing/disappearing by hand")
    restore = sub.add_parser("restore-capture", help="put the game back exactly as captured")
    restore.add_argument("capture", help="a capture id, or 'first'")
    restore.add_argument("--yes", action="store_true")
    sub.add_parser("recover", help="roll back a switch that was interrupted")
    mods = sub.add_parser("mods", help="edit a profile's mods (vr-custom by default)")
    mods.add_argument("action", choices=("list", "add", "remove", "next", "clear"))
    mods.add_argument("names", nargs="*", help="mod folder names, or a count for `next`")
    mods.add_argument("--profile", default="vr-custom")
    mods.add_argument("--source", default="2d", help="where `next` takes mods from")
    vr = sub.add_parser("vr-import", help="store the VR mod from a runtime package")
    vr.add_argument("package")
    sub.add_parser("verify", help="check every stored file in the vault")
    args = parser.parse_args(argv)
    try:
        manager = make_manager(args)
        handler = globals()["command_" + args.command.replace("-", "_")]
        return handler(manager, args)
    except (ProfileError, SteamError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
