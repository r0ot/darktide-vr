"""The game-folder profile manager against a fake Steam install.

Builds a Steam root (appmanifest, depot manifests in Steam's own binary
format) and a game folder with a mod loader, mods, a user file and settings,
then drives every command that changes the folder and checks the bytes.
Nothing here touches a real game or a real Steam install.
"""
import hashlib
import os
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "profiles"))

from dtprofiles import engine, graphics, sjson, steam  # noqa: E402
from dtprofiles.engine import Manager, ProfileError, Tools  # noqa: E402
from dtprofiles.store import Vault, read_json, sha256_file  # noqa: E402

# --- Steam's manifest format, written ----------------------------------------


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _field(number: int, value) -> bytes:
    if isinstance(value, int):
        return _varint(number << 3) + _varint(value)
    return _varint(number << 3 | 2) + _varint(len(value)) + value


def write_depot_manifest(path: Path, depot: int, manifest: int, files: dict, dirs=()) -> None:
    payload = b""
    for name, data in files.items():
        mapping = (_field(1, name.encode()) + _field(2, len(data)) + _field(3, 0) +
                   _field(5, hashlib.sha1(data).digest()))
        payload += _field(1, mapping)
    for name in dirs:
        payload += _field(1, _field(1, name.encode()) + _field(2, 0) + _field(3, 64))
    metadata = _field(1, depot) + _field(2, manifest) + _field(4, 0)
    blob = b""
    for magic, body in ((0x71F617D0, payload), (0x1F4812BE, metadata), (0x1B81B817, b"")):
        blob += struct.pack("<II", magic, len(body)) + body
    path.write_bytes(blob + struct.pack("<I", 0x32C415AB))


VANILLA_DB_V1 = b"BUNDLEDB-v1" + bytes(range(200))
VANILLA_EXE_V1 = b"MZ-darktide-v1" + bytes(300)
VANILLA_DB_V2 = b"BUNDLEDB-v2" + bytes(range(210))
VANILLA_EXE_V2 = b"MZ-darktide-v2" + bytes(310)


def fake_loader_patch(stage: Path) -> None:
    database = stage / "bundle_database.data"
    data = database.read_bytes()
    (stage / "bundle_database.data.bak").write_bytes(data)
    database.write_bytes(data + b"|patch_999")


def fake_vr_exe_patch(staged_exe: Path, backup: Path, tool: Path) -> None:
    assert tool.is_file()
    data = bytearray(staged_exe.read_bytes())
    data[2] ^= 0xFF
    staged_exe.write_bytes(bytes(data))


class Fixture:
    def __init__(self, root: Path):
        self.root = root
        self.steam = root / "Steam"
        self.game = self.steam / "steamapps" / "common" / "Darktide"
        self.settings = root / "AppData" / "Fatshark" / "Darktide"
        self.vault_root = root / "vault"
        self.build("100", VANILLA_DB_V1, VANILLA_EXE_V1, manifest_id=11)
        # The game as the user has it: loader, two mods, a stale .bak, a note.
        g = self.game
        self.write("binaries/mod_loader", b"dml loader")
        self.write("bundle/9ba626afa44a3aa3.patch_999", b"dml bundle")
        self.write("bundle/bundle_database.data.bak", b"OLD BUILD DATABASE")
        self.write("mods/base/mod_manager.lua", b"-- AML")
        self.write("mods/dmf/dmf.mod", b"dmf")
        self.write("mods/mod_load_order.txt", b"Alpha\r\nBeta\r\n")
        self.write("mods/auto_mod_loader_log.txt", b"log")
        self.write("mods/Alpha/Alpha.mod", b"alpha v1")
        self.write("mods/Alpha/scripts/alpha.lua", b"return 1")
        self.write("mods/Beta/Beta.mod", b"beta v1")
        self.write("toggle_darktide_mods.bat", b"@echo off")
        self.write("tools/dtkit-patch.exe", b"dtkit")
        self.write("New Text Document.txt", b"my notes")
        self.settings.mkdir(parents=True)
        (self.settings / "user_settings.config").write_bytes(b"settings: flat + mod settings")
        (self.settings / "launcher.config").write_bytes(b"launcher")

    def write(self, relative: str, data: bytes) -> None:
        path = self.game / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def read(self, relative: str) -> bytes:
        return (self.game / relative).read_bytes()

    def build(self, buildid: str, database: bytes, executable: bytes, manifest_id: int) -> None:
        library = self.steam / "steamapps"
        library.mkdir(parents=True, exist_ok=True)
        (self.steam / "depotcache").mkdir(exist_ok=True)
        content = {"bundle\\bundle_database.data": database, "bundle\\0123456789abcdef": b"x" * 64,
                   "bundle\\settings.ini": b"[s]"}
        binaries = {"binaries\\Darktide.exe": executable, "binaries\\steam_api64.dll": b"steam"}
        write_depot_manifest(self.steam / "depotcache" / f"1361211_{manifest_id}.manifest",
                             1361211, manifest_id, content, dirs=("bundle",))
        write_depot_manifest(self.steam / "depotcache" / f"1361213_{manifest_id + 1}.manifest",
                             1361213, manifest_id + 1, binaries, dirs=("binaries",))
        (library / "appmanifest_1361210.acf").write_text(f'''"AppState"
{{
\t"appid"\t\t"1361210"
\t"installdir"\t\t"Darktide"
\t"StateFlags"\t\t"4"
\t"buildid"\t\t"{buildid}"
\t"TargetBuildID"\t\t"{buildid}"
\t"InstalledDepots"
\t{{
\t\t"1361211" {{ "manifest" "{manifest_id}" "size" "1" }}
\t\t"1361213" {{ "manifest" "{manifest_id + 1}" "size" "1" }}
\t}}
}}
''')
        for relative, data in {**content, **binaries}.items():
            self.write(relative.replace("\\", "/"), data)

    def manager(self, tools: Tools | None = None) -> Manager:
        installation = steam.find_installation(roots=[self.steam])
        vanilla = steam.load_vanilla_index(installation)
        manager = Manager(Vault(self.vault_root), installation, vanilla,
                          settings_directory=self.settings,
                          tools=tools or Tools(loader_patch=fake_loader_patch,
                                               vr_exe_patch=fake_vr_exe_patch),
                          log=lambda message: None)
        # Never the real one: a real backup would make a switch run a restore.
        manager.steamvr_backup = self.root / "DarktideVR" / "steamvr-session-backup.txt"
        return manager

    def snapshot(self) -> dict:
        files = {}
        for path in sorted(self.game.rglob("*")):
            if path.is_file():
                files[path.relative_to(self.game).as_posix()] = sha256_file(path)
        files["<settings>"] = sha256_file(self.settings / "user_settings.config")
        return files

    def vr_package(self) -> Path:
        package = self.root / "package"
        mod = package / "mods" / "darktidevr"
        for relative, data in {"darktidevr.mod": b"vr mod", "scripts/vr.lua": b"vr",
                               "bin/d3d12.dll": b"proxy", "bin/viewer.exe": b"viewer",
                               "tools/set-skinner-assert-patch.ps1": b"# patch tool"}.items():
            path = mod / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return package


class ProfilesTest(unittest.TestCase):
    def setUp(self):
        # The fixture's game is never running; the real one may be (the owner
        # plays while these run), and the check looks for any Darktide.exe.
        self._running = engine.running_game_processes
        engine.running_game_processes = lambda root: []
        self.addCleanup(setattr, engine, "running_game_processes", self._running)
        self.temporary = Path(tempfile.mkdtemp(prefix="dtprofiles-test-"))
        self.fixture = Fixture(self.temporary)
        self.manager = self.fixture.manager()

    def tearDown(self):
        shutil.rmtree(self.temporary, ignore_errors=True)

    def init(self):
        return self.manager.initialize()

    # --- reading -------------------------------------------------------------

    def test_depot_manifest_round_trip(self):
        installation = steam.find_installation(roots=[self.fixture.steam])
        index = steam.load_vanilla_index(installation)
        self.assertEqual(index.files["binaries\\darktide.exe"].sha1,
                         hashlib.sha1(VANILLA_EXE_V1).hexdigest())
        self.assertIn("bundle", index.directories)

    def test_init_captures_everything_not_vanilla(self):
        capture_id = self.init()
        document = read_json(self.manager.vault.captures / f"{capture_id}.json")
        components = {item["component"] for item in document["files"].values()}
        self.assertIn("mod:Alpha", components)
        self.assertIn("mod:Beta", components)
        self.assertIn("loader", components)
        self.assertEqual(document["files"]["new text document.txt"]["component"], None)
        self.assertIn("user_settings.config", document["settings"])
        self.assertEqual(self.manager.profile("2d")["mods"], ["Alpha", "Beta"])
        self.assertEqual(self.manager.state()["profile"], "2d")
        with self.assertRaises(ProfileError):
            self.manager.initialize()

    # --- switching -----------------------------------------------------------

    def test_2d_patches_the_database_and_replaces_the_stale_backup(self):
        self.init()
        self.manager.switch("2d")
        self.assertTrue(self.fixture.read("bundle/bundle_database.data").endswith(b"patch_999"))
        self.assertEqual(self.fixture.read("bundle/bundle_database.data.bak"), VANILLA_DB_V1)

    def test_vanilla_round_trip_is_exact(self):
        self.init()
        self.manager.switch("2d")
        modded = self.fixture.snapshot()
        self.manager.switch("vanilla")
        game = self.fixture.game
        self.assertEqual(self.fixture.read("bundle/bundle_database.data"), VANILLA_DB_V1)
        for gone in ("binaries/mod_loader", "mods/Alpha", "mods/base", "tools",
                     "toggle_darktide_mods.bat", "bundle/bundle_database.data.bak"):
            self.assertFalse((game / gone).exists(), gone)
        self.assertEqual(self.fixture.read("New Text Document.txt"), b"my notes")
        self.assertTrue((game / "bundle" / "0123456789abcdef").is_file())
        self.manager.switch("2d")
        self.assertEqual(self.fixture.snapshot(), modded)

    def test_plan_changes_nothing(self):
        self.init()
        before = self.fixture.snapshot()
        plan, _ = self.manager.switch("vanilla", dry_run=True)
        self.assertTrue(plan.removes)
        self.assertEqual(self.fixture.snapshot(), before)

    def test_user_changes_are_absorbed_into_the_active_profile(self):
        self.init()
        self.manager.switch("2d")
        self.fixture.write("mods/Alpha/Alpha.mod", b"alpha v2")           # updated
        self.fixture.write("mods/Gamma/Gamma.mod", b"gamma")               # added
        shutil.rmtree(self.fixture.game / "mods" / "Beta")                 # removed
        plan, _ = self.manager.switch("vanilla")
        self.assertTrue(any("Gamma" in note for note in plan.notes))
        self.assertEqual(self.manager.profile("2d")["mods"], ["Alpha", "Gamma"])
        self.manager.switch("2d")
        self.assertEqual(self.fixture.read("mods/Alpha/Alpha.mod"), b"alpha v2")
        self.assertTrue((self.fixture.game / "mods" / "Gamma" / "Gamma.mod").is_file())
        self.assertFalse((self.fixture.game / "mods" / "Beta").exists())
        self.assertIn("mod:Beta", self.manager.library()["components"])   # still in the vault

    def test_a_mod_added_to_the_active_profile_is_installed_not_dropped(self):
        self.init()
        self.manager.import_vr(self.fixture.vr_package())
        self.manager.switch("2d")
        profile = self.manager.profile("vr-custom")
        profile["mods"] = ["Alpha"]
        self.manager.save_profile("vr-custom", profile)
        self.manager.switch("vr-custom")
        profile = self.manager.profile("vr-custom")
        profile["mods"] = ["Alpha", "Beta"]                               # `mods add` while active
        self.manager.save_profile("vr-custom", profile)
        plan, _ = self.manager.switch("vr-custom")
        self.assertFalse(any("removed in the game folder" in note for note in plan.notes))
        self.assertEqual(self.manager.profile("vr-custom")["mods"], ["Alpha", "Beta"])
        self.assertTrue((self.fixture.game / "mods" / "Beta").is_dir())

    def test_vr_profiles_settings_slots_and_incremental_mods(self):
        self.init()
        self.manager.import_vr(self.fixture.vr_package())
        self.manager.switch("vr")
        game = self.fixture.game
        self.assertEqual(self.fixture.read("binaries/d3d12.dll"), b"proxy")
        self.assertNotEqual(self.fixture.read("binaries/Darktide.exe"), VANILLA_EXE_V1)
        self.assertEqual(self.fixture.read("mods/darktidevr/darktidevr_refresh_rate.flag"), b"90")
        self.assertFalse((game / "mods" / "Alpha").exists())
        settings = self.fixture.settings / "user_settings.config"
        self.assertEqual(settings.read_bytes(), b"settings: flat + mod settings")  # seeded
        settings.write_bytes(b"settings: vr")
        self.manager.switch("2d")
        self.assertEqual(settings.read_bytes(), b"settings: flat + mod settings")
        self.assertFalse((game / "binaries" / "d3d12.dll").exists())
        self.assertEqual(self.fixture.read("binaries/Darktide.exe"), VANILLA_EXE_V1)
        self.manager.switch("vr-mods")
        self.assertEqual(settings.read_bytes(), b"settings: vr")
        self.assertTrue((game / "mods" / "Alpha").is_dir())
        self.assertTrue((game / "mods" / "darktidevr").is_dir())
        # Incremental: vr-custom gains one mod from 2D at a time.
        profile = self.manager.profile("vr-custom")
        profile["mods"] = ["Alpha"]
        self.manager.save_profile("vr-custom", profile)
        self.manager.switch("vr-custom")
        self.assertTrue((game / "mods" / "Alpha").is_dir())
        self.assertFalse((game / "mods" / "Beta").exists())

    def test_unsupported_exe_patch_stops_before_any_change(self):
        def refuse(*_):
            raise ProfileError("unsupported build")
        manager = self.fixture.manager(Tools(loader_patch=fake_loader_patch, vr_exe_patch=refuse))
        manager.initialize()
        manager.import_vr(self.fixture.vr_package())
        before = self.fixture.snapshot()
        with self.assertRaises(ProfileError):
            manager.switch("vr")
        self.assertEqual(self.fixture.snapshot(), before)

    # --- failures ------------------------------------------------------------

    def test_a_failed_write_rolls_back_everything(self):
        self.init()
        self.manager.switch("2d")
        before = self.fixture.snapshot()
        state = self.manager.state()
        real = self.manager.vault.copy_out
        calls = {"n": 0}

        def flaky(digest, destination):
            calls["n"] += 1
            if calls["n"] == 3:
                raise OSError("disk full")
            return real(digest, destination)
        self.manager.vault.copy_out = flaky
        self.manager.import_vr(self.fixture.vr_package())
        with self.assertRaises(OSError):
            self.manager.switch("vr-mods")
        self.manager.vault.copy_out = real
        self.assertEqual(self.fixture.snapshot(), before)
        self.assertEqual(self.manager.state()["profile"], state["profile"])
        self.assertFalse(list(self.manager.vault.journal.glob("*.json")))

    def test_an_interrupted_switch_is_recovered(self):
        self.init()
        self.manager.switch("2d")
        before = self.fixture.snapshot()
        real_write = self.manager._write
        calls = {"n": 0}

        def crash(relative, digest, readonly=None):
            calls["n"] += 1
            if calls["n"] == 4:
                raise KeyboardInterrupt  # the process dies here
            return real_write(relative, digest, readonly)
        self.manager._write = crash
        self.manager.rollback = lambda journal: None  # ...and never got to roll back
        with self.assertRaises(KeyboardInterrupt):
            self.manager.switch("vanilla")
        self.assertTrue(list(self.manager.vault.journal.glob("*.json")))
        fresh = self.fixture.manager()
        with self.assertRaises(ProfileError):
            fresh.switch("2d")                       # refuses until recovered
        messages = fresh.recover()
        self.assertTrue(messages and "rolled back" in messages[0])
        self.assertEqual(self.fixture.snapshot(), before)
        self.assertEqual(fresh.state()["profile"], "2d")

    def test_loader_appearing_by_hand_is_not_absorbed_silently(self):
        self.init()
        self.manager.switch("vanilla")
        self.fixture.write("mods/base/mod_manager.lua", b"-- installed by hand")
        with self.assertRaises(ProfileError):
            self.manager.switch("2d")
        self.manager.switch("2d", allow_structure=True)

    def test_restore_capture_is_exact(self):
        capture_id = self.init()
        captured = self.fixture.snapshot()
        self.manager.import_vr(self.fixture.vr_package())
        self.manager.switch("vr-mods")
        self.manager.restore_capture(capture_id)
        self.assertEqual(self.fixture.snapshot(), captured)

    def test_a_game_update_between_switches(self):
        self.init()
        self.manager.switch("2d")
        # Steam updates the game: new database and executable, loader patch gone.
        self.fixture.build("200", VANILLA_DB_V2, VANILLA_EXE_V2, manifest_id=21)
        manager = self.fixture.manager()
        manager.switch("2d")
        self.assertEqual(self.fixture.read("bundle/bundle_database.data"),
                         VANILLA_DB_V2 + b"|patch_999")
        self.assertEqual(self.fixture.read("bundle/bundle_database.data.bak"), VANILLA_DB_V2)
        manager.switch("vanilla")
        self.assertEqual(self.fixture.read("bundle/bundle_database.data"), VANILLA_DB_V2)
        # The first capture was build 100: its database must not come back.
        captures = sorted(manager.vault.captures.glob("*.json"))
        manager.restore_capture(captures[0].stem)
        self.assertEqual(self.fixture.read("bundle/bundle_database.data"), VANILLA_DB_V2)
        self.assertEqual(self.fixture.read("binaries/Darktide.exe"), VANILLA_EXE_V2)
        self.assertEqual(self.fixture.read("mods/Alpha/Alpha.mod"), b"alpha v1")

    def test_read_only_files_switch_out_and_come_back_read_only(self):
        # Power_DI ships its .git folder; git marks object files read-only and
        # Windows refuses to delete them (the first real round trip failed on
        # one, 8 October).
        obj = self.fixture.game / "mods" / "Beta" / ".git" / "objects" / "00" / "abcdef"
        obj.parent.mkdir(parents=True)
        obj.write_bytes(b"git object")
        os.chmod(obj, 0o444)
        self.init()
        self.manager.switch("2d")
        before = self.fixture.snapshot()
        self.manager.switch("vanilla")
        self.assertFalse(obj.exists())
        self.manager.switch("2d")
        self.assertEqual(self.fixture.snapshot(), before)
        self.assertFalse(os.access(obj, os.W_OK), "the object came back writable")

    def test_a_rollback_can_be_repeated(self):
        self.init()
        self.manager.switch("2d")
        before = self.fixture.snapshot()
        real_write = self.manager._write
        calls = {"n": 0}

        def crash(relative, digest, readonly=None):
            calls["n"] += 1
            if calls["n"] == 3:
                raise KeyboardInterrupt
            return real_write(relative, digest, readonly)
        self.manager._write = crash
        self.manager.rollback = lambda journal: None
        with self.assertRaises(KeyboardInterrupt):
            self.manager.switch("vanilla")
        fresh = self.fixture.manager()
        journal = read_json(next(fresh.vault.journal.glob("*.json")))
        self.assertEqual(fresh.rollback(dict(journal)), [])
        self.assertEqual(fresh.rollback(dict(journal)), [])    # again: nothing to redo
        self.assertEqual(self.fixture.snapshot(), before)

    def test_leaving_vr_restores_steamvr_settings_left_by_a_killed_session(self):
        self.init()
        self.manager.import_vr(self.fixture.vr_package())
        self.manager.switch("vr")
        self.manager.steamvr_backup.parent.mkdir(parents=True)
        self.manager.steamvr_backup.write_text("darktidevr_steamvr_backup=1\nrefresh_rate_hz=144\n")
        calls = []
        self.manager.restore_steamvr_settings = lambda: calls.append(1) or "restored"
        plan, _ = self.manager.switch("2d")
        self.assertEqual(calls, [1])
        self.assertIn("restored", plan.notes)
        self.manager.switch("vr")                        # entering VR: the viewer's job
        self.assertEqual(calls, [1])

    def test_a_vr_import_while_vr_is_active_is_installed_not_overwritten(self):
        self.init()
        self.manager.import_vr(self.fixture.vr_package())
        self.manager.switch("vr")
        package = self.fixture.vr_package()
        (package / "mods" / "darktidevr" / "scripts" / "vr.lua").write_bytes(b"vr v2")
        self.manager.import_vr(package)
        self.manager.switch("vr")
        self.assertEqual(self.fixture.read("mods/darktidevr/scripts/vr.lua"), b"vr v2")
        # ...and a change made in the game after that is absorbed as usual.
        self.fixture.write("mods/darktidevr/darktidevr_refresh_rate.flag", b"120")
        self.manager.switch("2d")
        self.manager.switch("vr")
        self.assertEqual(self.fixture.read("mods/darktidevr/darktidevr_refresh_rate.flag"), b"120")

    def test_a_running_game_blocks_a_switch(self):
        self.init()
        engine.running_game_processes = lambda root: [r"C:\game\Darktide.exe"]
        before = self.fixture.snapshot()
        with self.assertRaises(ProfileError):
            self.manager.switch("vanilla")
        self.assertEqual(self.fixture.snapshot(), before)

    def test_steam_updating_blocks_a_switch(self):
        self.init()
        self.manager.installation.state_flags = 6
        with self.assertRaises(ProfileError):
            self.manager.switch("vanilla")

    def test_vr_shader_dumps_leave_with_vr_and_are_never_stored(self):
        self.init()
        self.manager.import_vr(self.fixture.vr_package())
        self.manager.switch("vr")
        dump = "mods/darktidevr/bin/blended_pixel_shaders/ps-0123456789abcdef.bin"
        self.fixture.write(dump, b"dxil")
        self.manager.switch("2d")
        self.assertFalse((self.fixture.game / "mods" / "darktidevr").exists())
        stored = self.manager.library()["components"]["vr"]["files"]
        self.assertFalse(any("pixel_shaders" in key for key in stored))
        self.manager.switch("vr")
        self.assertFalse((self.fixture.game / dump).exists())


# --- graphics settings --------------------------------------------------------

SETTINGS_2D = """
adapter_index = 0
fullscreen = true
master_render_settings = {
\tdlss = 5
\tdlss_g = 1
\tgraphics_quality = "custom"
\trt_reflections_quality = "high"
}
mods_settings = {
\tAlpha = {
\t\tcolour = "red"
\t\tkeys = [
\t\t\t"r"
\t\t\t"left shift"
\t\t]
\t}
\toptions_menu_last_selected = "Alpha"
}
render_settings = {
\tdlss_enabled = true
\tdlss_g_enabled = true
\tlocal_lights_shadow_atlas_size = [
\t\t4096
\t\t4096
\t]
\trt_reflections_enabled = true
\tsharpness = 0.5
\tupscaling_quality = "quality"
\tvolumetric_reprojection_amount = -0.875
}
screen_mode = "fullscreen"
sound_settings = {
\toption_master_slider = 13
}
texture_settings = {
\t"content/texture_categories/character_bc" = 0
}
"""


class GraphicsTest(unittest.TestCase):
    def test_the_file_round_trips_byte_for_byte(self):
        self.assertEqual(sjson.dumps(sjson.parse(SETTINGS_2D)), SETTINGS_2D)
        self.assertFalse(sjson.round_trips("a = {\n"))
        self.assertFalse(sjson.round_trips("\na=1\n"))       # not the game's own spacing

    def test_a_menu_option_writes_both_layers(self):
        document = sjson.parse(SETTINGS_2D)
        changes = graphics.apply(document, "rt_reflections_quality=off")
        render = document["render_settings"]
        self.assertEqual(document["master_render_settings"]["rt_reflections_quality"].raw, '"off"')
        self.assertEqual(render["rt_reflections_enabled"].raw, "false")
        self.assertEqual(render["world_space_motion_vectors"].raw, "false")   # added, in order
        self.assertLess(list(render).index("volumetric_reprojection_amount"),
                        list(render).index("world_space_motion_vectors"))
        self.assertIn("render_settings.rt_reflections_enabled=false", changes)
        graphics.apply(document, "dlss=4")
        self.assertEqual(render["upscaling_quality"].raw, '"balanced"')
        graphics.apply(document, "light_quality=high")
        self.assertEqual([v.raw for v in render["local_lights_shadow_atlas_size"]], ["2048", "2048"])
        graphics.apply(document, "render.sharpness=0.3")
        self.assertEqual(render["sharpness"].raw, "0.3")
        self.assertEqual(graphics.apply(document, "dlss=4"), [])               # already so
        for bad in ("dlss=9", "nonsense=1", "master.dlss=1", "dlss"):
            with self.assertRaises(graphics.GraphicsError):
                graphics.apply(document, bad)

    def test_merge_takes_the_slots_graphics_and_keeps_everything_else(self):
        live = sjson.parse(SETTINGS_2D)
        vr = sjson.parse(SETTINGS_2D)
        graphics.apply(vr, "dlss=3")
        vr.put("borderless_fullscreen", False)
        vr["mods_settings"].put("darktidevr", sjson.Table(hud_distance=sjson.scalar(2)))
        graphics.apply(live, "dlss=6")                        # the 2D file in use
        live["sound_settings"].put("option_master_slider", 40)
        live["mods_settings"]["Alpha"].put("colour", "blue")
        merged = graphics.merge(live, vr)
        self.assertEqual(merged["render_settings"]["upscaling_quality"].raw, '"performance"')
        self.assertEqual(merged["borderless_fullscreen"].raw, "false")
        self.assertEqual(merged["sound_settings"]["option_master_slider"].raw, "40")
        self.assertEqual(merged["mods_settings"]["Alpha"]["colour"].raw, '"blue"')
        self.assertIn("darktidevr", merged["mods_settings"])
        back = graphics.merge(merged, sjson.parse(SETTINGS_2D))
        self.assertNotIn("borderless_fullscreen", back)      # 2D never had it
        self.assertEqual(back["render_settings"]["upscaling_quality"].raw, '"quality"')
        self.assertEqual(back["mods_settings"]["Alpha"]["colour"].raw, '"blue"')
        self.assertEqual(sjson.parse(sjson.dumps(back)), back)

    def test_custom_hud_layout_belongs_to_the_slot(self):
        flat = sjson.parse(SETTINGS_2D)
        flat["mods_settings"].put("custom_hud", sjson.Table(opacity=sjson.scalar(0.7)))
        vr = sjson.parse(SETTINGS_2D)                         # no layout of its own yet
        merged = graphics.merge(flat, vr)
        self.assertNotIn("custom_hud", merged["mods_settings"])  # VR starts at defaults
        merged["mods_settings"].put("custom_hud", sjson.Table(opacity=sjson.scalar(0.4)))
        back = graphics.merge(merged, flat)                   # to 2D: its own layout
        self.assertEqual(back["mods_settings"]["custom_hud"]["opacity"].raw, "0.7")
        again = graphics.merge(back, merged)                  # to VR: the VR layout
        self.assertEqual(again["mods_settings"]["custom_hud"]["opacity"].raw, "0.4")


class GraphicsProfilesTest(unittest.TestCase):
    """The switch with settings files the game wrote: the graphics follow the
    profile, the rest of the file is the user's, living, in both."""

    tearDown = ProfilesTest.tearDown
    init = ProfilesTest.init

    def setUp(self):
        ProfilesTest.setUp(self)
        self.settings = self.fixture.settings / "user_settings.config"
        self.settings.write_text(SETTINGS_2D, encoding="utf-8", newline="\n")

    def read(self):
        return sjson.parse(self.settings.read_text(encoding="utf-8"))

    def edit(self, change):
        document = self.read()
        change(document)
        self.settings.write_text(sjson.dumps(document), encoding="utf-8", newline="\n")

    def test_graphics_follow_the_profile_and_mod_settings_live_in_both(self):
        self.init()
        self.manager.import_vr(self.fixture.vr_package())
        self.manager.switch("vr")                          # new slot: a copy of 2D
        self.edit(lambda d: graphics.apply(d, "rt_reflections_quality=off"))   # in VR
        self.edit(lambda d: d["mods_settings"].put(
            "darktidevr", sjson.Table(hud_distance=sjson.scalar(2))))
        plan, _ = self.manager.switch("2d")
        self.assertTrue(any("graphics from slot '2d'" in note for note in plan.notes))
        document = self.read()
        self.assertEqual(document["master_render_settings"]["rt_reflections_quality"].raw, '"high"')
        self.edit(lambda d: d["mods_settings"]["Alpha"].put("colour", "green"))  # in 2D
        self.manager.switch("vr")
        document = self.read()
        self.assertEqual(document["master_render_settings"]["rt_reflections_quality"].raw, '"off"')
        self.assertEqual(document["mods_settings"]["Alpha"]["colour"].raw, '"green"')
        self.assertEqual(document["mods_settings"]["darktidevr"]["hud_distance"].raw, "2")

    def test_changing_another_slot_leaves_the_file_in_use_alone(self):
        self.init()
        self.manager.import_vr(self.fixture.vr_package())
        self.manager.switch("vr")
        self.manager.switch("2d")
        before = self.settings.read_bytes()
        changes = self.manager.change_graphics("vr", ["dlss_g=0", "dlss=3"])
        self.assertIn("render_settings.dlss_g_enabled=false", changes)
        self.assertEqual(self.settings.read_bytes(), before)
        document, live = self.manager.settings_document("vr")
        self.assertFalse(live)
        self.assertEqual(document["master_render_settings"]["dlss"].raw, "3")
        self.manager.switch("vr")
        self.assertEqual(self.read()["render_settings"]["upscaling_quality"].raw, '"performance"')
        # The active slot is the file in use, and the game must be closed.
        engine.running_game_processes = lambda root: [r"C:\game\Darktide.exe"]
        with self.assertRaises(ProfileError):
            self.manager.change_graphics("vr", ["dlss=5"])
        engine.running_game_processes = lambda root: []
        self.manager.change_graphics("live", ["dlss=5"])
        self.assertEqual(self.read()["render_settings"]["upscaling_quality"].raw, '"quality"')

    def test_a_file_the_parser_cannot_read_is_switched_whole(self):
        self.init()
        self.manager.import_vr(self.fixture.vr_package())
        self.manager.switch("vr")
        self.settings.write_bytes(b"settings: written by something else")
        plan, _ = self.manager.switch("2d")
        self.assertTrue(any("whole stored" in note for note in plan.notes))
        self.assertEqual(self.settings.read_text(encoding="utf-8"), SETTINGS_2D)


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=2).result
    print("dtprofiles=" + ("pass" if result.wasSuccessful() else "fail"))
    sys.exit(0 if result.wasSuccessful() else 1)
