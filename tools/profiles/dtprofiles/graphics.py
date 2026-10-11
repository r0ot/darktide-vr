"""Graphics settings per profile; everything else in the settings file shared.

user_settings.config holds the video options and, beside them, everything
the user has that is not about the picture: every mod's own settings (the
DMF block is most of the file), sound, network, language. A settings slot
owns only the GRAPHICS keys below. On a switch the file in use keeps
everything it has -- that is the living part, whatever the user last changed
in either profile -- and takes the target slot's graphics. A mod settings
block the file in use lacks (the VR mod's own, when coming from 2D, where it
does not load) is taken from the slot rather than lost.

The video menu stores two layers: `master_render_settings` holds the menu's
choices, `render_settings` (and `texture_settings`, `performance_settings`)
the engine values each choice stands for. `OPTIONS` is the game's own mapping
(scripts/settings/options/render_settings.lua of the current build, read from
the community source dump), so a change made here leaves both layers as the
menu would.
"""
from __future__ import annotations

from . import sjson
from .sjson import Scalar, Table

# The top-level keys a settings slot owns.
GRAPHICS = (
    "adapter_index", "aspect_ratio", "borderless_fullscreen", "fullscreen",
    "fullscreen_output", "gamma", "last_fullscreen_resolution", "last_windowed_resolution",
    "master_render_settings", "mesh_streamer_settings", "performance_settings",
    "render_settings", "screen_mode", "screen_resolution", "texture_settings", "vsync",
)
MODS = "mods_settings"
# Mod settings that belong to a slot as the graphics do, because they are
# made for one display: a HUD layout arranged for a monitor is wrong in the
# headset and the other way round (user, 10 October: a separate VR layout for
# Custom HUD). A slot without one leaves the mod at its defaults.
SLOT_MODS = ("custom_hud",)

_TEXTURES = [f"content/texture_categories/{name}" for name in (
    "character_bc", "character_bca", "character_bcm", "character_hm", "character_mask",
    "character_mask2", "character_nm", "character_orm", "environment_bc", "environment_bca",
    "environment_hm", "environment_nm", "environment_orm", "weapon_bc", "weapon_bca",
    "weapon_hm", "weapon_mask", "weapon_nm", "weapon_orm")]


def _volumetric(shafts, high_quality, shadows, local_lights, reprojection, size):
    return {"render": {
        "light_shafts_enabled": shafts, "volumetric_extrapolation_high_quality": high_quality,
        "volumetric_extrapolation_volumetric_shadows": shadows,
        "volumetric_lighting_local_lights": local_lights,
        "volumetric_reprojection_amount": reprojection, "volumetric_volumes_enabled": True,
        "volumetric_data_size": size}}


def _lights(local_filter, sun_filter, sun_shadows, sun_size, atlas):
    return {"render": {
        "local_lights_max_dynamic_shadow_distance": 50,
        "local_lights_max_non_shadow_casting_distance": 0,
        "local_lights_max_static_shadow_distance": 100,
        "local_lights_shadows_enabled": True, "static_sun_shadows": True,
        "local_lights_shadow_map_filter_quality": local_filter,
        "sun_shadow_map_filter_quality": sun_filter, "sun_shadows": sun_shadows,
        "sun_shadow_map_size": [sun_size, sun_size],
        "static_sun_shadow_map_size": [2048, 2048],
        "local_lights_shadow_atlas_size": [atlas, atlas]}}


def _upscaler(quality):
    return {"render": {"dlss_enabled": True, "upscaling_quality": quality},
            "master": {"fsr": 0, "fsr2": 0, "xess": 0}}


# master_render_settings option -> value -> what the menu writes with it.
# "render", "master", "performance", "texture" name the block each goes to.
OPTIONS: dict[str, dict] = {
    "dlss": {
        0: {"render": {"dlss_enabled": False}},
        1: _upscaler("auto"), 2: _upscaler("ultra_performance"), 3: _upscaler("performance"),
        4: _upscaler("balanced"), 5: _upscaler("quality"), 6: _upscaler("native"),
    },
    "dlss_g": {
        0: {"render": {"dlss_g_enabled": False}},
        1: {"render": {"dlss_g_enabled": True, "dlss_g_frames_to_generate": 1},
            "master": {"nv_reflex_low_latency": 1}, "top": {"vsync": False}},
        2: {"render": {"dlss_g_enabled": True, "dlss_g_frames_to_generate": 2},
            "master": {"nv_reflex_low_latency": 1}, "top": {"vsync": False}},
        3: {"render": {"dlss_g_enabled": True, "dlss_g_frames_to_generate": 3},
            "master": {"nv_reflex_low_latency": 1}, "top": {"vsync": False}},
    },
    "nv_reflex_low_latency": {
        0: {"render": {"nv_low_latency_boost": False, "nv_low_latency_mode": False}},
        1: {"render": {"nv_low_latency_boost": False, "nv_low_latency_mode": True}},
        2: {"render": {"nv_low_latency_boost": True, "nv_low_latency_mode": True}},
    },
    "nv_reflex_framerate_cap": {
        value: {"render": {"nv_framerate_cap": cap}}
        for value, cap in enumerate((0, 30, 40, 60, 72, 90, 120))
    },
    "anti_aliasing_solution": {
        0: {"render": {"fxaa_enabled": False, "taa_enabled": False}},
        1: {"render": {"fxaa_enabled": True, "taa_enabled": False}},
        2: {"render": {"fxaa_enabled": False, "taa_enabled": True}},
    },
    "rt_reflections_quality": {
        "off": {"render": {"rt_checkerboard_reflections": False, "rt_reflections_enabled": False,
                           "world_space_motion_vectors": False}},
        "low": {"master": {"ssr_quality": "high"},
                "render": {"dxr": True, "rt_mixed_reflections": True,
                           "rt_reflections_enabled": True, "ssr_enabled": True,
                           "world_space_motion_vectors": True}},
        "high": {"master": {"ssr_quality": "off"},
                 "render": {"dxr": True, "rt_mixed_reflections": False,
                            "rt_reflections_enabled": True, "world_space_motion_vectors": True}},
    },
    "rtxgi_quality": {
        "off": {"render": {"baked_ddgi": True, "rtxgi_enabled": False}},
        "low": {"render": {"baked_ddgi": True, "dxr": True, "rtxgi_enabled": True,
                           "rtxgi_scale": 0.5}},
        "medium": {"render": {"baked_ddgi": False, "dxr": True, "rtxgi_enabled": True,
                              "rtxgi_scale": 0.5}},
        "high": {"render": {"baked_ddgi": False, "dxr": True, "rtxgi_enabled": True,
                            "rtxgi_scale": 1}},
    },
    "gi_quality": {
        "low": {"render": {"rtxgi_scale": 0.5}},
        "high": {"render": {"rtxgi_scale": 1}},
    },
    "ambient_occlusion_quality": {
        "off": {"render": {"ao_enabled": False, "gtao_enabled": False, "gtao_quality": 0}},
        **{name: {"render": {"ao_enabled": True, "gtao_enabled": True, "gtao_quality": level,
                             "cacao_enabled": False}}
           for level, name in enumerate(("low", "medium", "high", "extreme"))},
    },
    "light_quality": {
        "low": _lights("low", "low", False, 4, 512),
        "medium": _lights("low", "medium", True, 2048, 1024),
        "high": _lights("high", "high", True, 2048, 2048),
        "extreme": _lights("high", "high", True, 2048, 4096),
    },
    "volumetric_fog_quality": {
        "low": _volumetric(False, False, False, False, 0.875, [80, 64, 96]),
        "medium": _volumetric(True, True, False, True, 0.625, [96, 80, 128]),
        "high": _volumetric(True, True, False, True, 0, [128, 96, 160]),
        "extreme": _volumetric(True, True, True, True, -0.875, [144, 112, 196]),
    },
    "dof_quality": {
        "off": {"render": {"dof_enabled": False, "dof_high_quality": False}},
        "medium": {"render": {"dof_enabled": True, "dof_high_quality": False}},
        "high": {"render": {"dof_enabled": True, "dof_high_quality": True}},
    },
    "lens_flare_quality": {
        "off": {"render": {"lens_flares_enabled": False, "sun_flare_enabled": False}},
        "sun_light_only": {"render": {"lens_flares_enabled": False, "sun_flare_enabled": True}},
        "all_lights": {"render": {"lens_flares_enabled": True, "sun_flare_enabled": True}},
    },
    "ssr_quality": {
        "off": {"render": {"ssr_enabled": False, "ssr_high_quality": False}},
        "medium": {"render": {"ssr_enabled": True, "ssr_high_quality": False}},
        "high": {"render": {"ssr_enabled": True, "ssr_high_quality": True}},
    },
    "texture_quality": {
        name: {"texture": {path: level for path in _TEXTURES}}
        for level, name in enumerate(("high", "medium", "low"))
    },
}

# Plain values the menu writes directly (sliders and switches), by block.
SLIDERS = {
    "render": ("lod_object_multiplier", "lod_scatter_density", "sharpness", "sharpen_enabled",
               "bloom_enabled", "skin_material_enabled", "motion_blur_enabled",
               "lens_quality_enabled", "lens_quality_color_fringe_enabled",
               "lens_quality_distortion_enabled", "vertical_fov", "rough_transparency_enabled"),
    "performance": ("max_ragdolls", "max_impact_decals", "max_blood_decals",
                    "max_footstep_decals", "decal_lifetime"),
}

_BLOCKS = {"render": "render_settings", "master": "master_render_settings",
           "performance": "performance_settings", "texture": "texture_settings"}


class GraphicsError(ValueError):
    pass


def _value(text: str):
    """A command-line value as the type the game stores."""
    # "off" stays a string: it is a quality level (rtxgi_quality=off).
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def _table(document: Table, name: str) -> Table:
    table = document.get(name)
    if not isinstance(table, Table):
        table = Table()
        document.put(name, table)
    return table


def _to_sjson(value):
    if isinstance(value, list):
        return [sjson.scalar(item) for item in value]
    return sjson.scalar(value)


def apply(document: Table, setting: str) -> list[str]:
    """Apply `name=value` the way the video menu would; returns what changed
    as `block.key=value` lines. `name` is a menu option (OPTIONS), a slider
    (`render.lod_object_multiplier`, `performance.max_ragdolls`), or any
    `render.KEY` the menu does not show."""
    if "=" not in setting:
        raise GraphicsError(f"expected NAME=VALUE, got {setting!r}")
    name, text = (part.strip() for part in setting.split("=", 1))
    changes: list[str] = []

    def write(block: str, key: str, value) -> None:
        if block == "top":
            target = document
        else:
            target = _table(document, _BLOCKS[block])
        new = _to_sjson(value)
        old = target.get(key)
        if old != new:
            target.put(key, new)
            changes.append(f"{_BLOCKS.get(block, '')}{'.' if block != 'top' else ''}{key}="
                           f"{_show(new)}")

    if name in OPTIONS:
        choices = OPTIONS[name]
        value = _value(text)
        if value not in choices:
            raise GraphicsError(f"{name}: one of {', '.join(map(str, choices))}")
        write("master", name, value)
        for block, values in choices[value].items():
            for key, item in values.items():
                write(block, key, item)
        write("master", "graphics_quality", "custom")
        return changes
    if "." in name:
        block, key = name.split(".", 1)
        if block not in _BLOCKS or block == "master":
            raise GraphicsError(f"{name}: use render.KEY, performance.KEY or a menu option")
        write(block, key, _value(text))
        if block == "performance":
            write("master", "graphics_quality", "custom")
        return changes
    raise GraphicsError(f"unknown setting {name!r}; see `graphics options`")


def _show(value) -> str:
    if isinstance(value, list):
        return "[" + " ".join(_show(item) for item in value) + "]"
    if isinstance(value, Table):
        return "{...}"
    return value.raw


def merge(current: Table, slot: Table) -> Table:
    """The file in use with `slot`'s graphics: every graphics key as the
    slot has it (absent where the slot has none), the SLOT_MODS settings
    likewise, everything else as it is now, and the slot's other mod
    settings blocks the file in use does not have."""
    result = current.copy()
    for key in GRAPHICS:
        if key in slot:
            result.put(key, sjson.deep_copy(slot[key]))
        elif key in result:
            del result[key]
    ours, theirs = result.get(MODS), slot.get(MODS)
    for name in SLOT_MODS:
        block = theirs.get(name) if isinstance(theirs, Table) else None
        if isinstance(block, Table):
            if not isinstance(ours, Table):
                ours = Table()
                result.put(MODS, ours)
            ours.put(name, sjson.deep_copy(block))
        elif isinstance(ours, Table) and name in ours:
            del ours[name]
    if isinstance(ours, Table) and isinstance(theirs, Table):
        for name, block in theirs.items():
            if name not in ours and isinstance(block, Table):
                ours.put(name, block.copy())
    elif ours is None and isinstance(theirs, Table):
        result.put(MODS, theirs.copy())
    return result


def graphics_of(document: Table) -> dict[str, object]:
    return {key: document[key] for key in GRAPHICS if key in document}


def flatten(document: Table) -> dict[str, str]:
    """`block.key` -> text for every graphics value, for showing and diffs."""
    flat: dict[str, str] = {}
    for key, value in graphics_of(document).items():
        if isinstance(value, Table):
            for inner, item in value.items():
                flat[f"{key}.{inner}"] = _show(item)
        else:
            flat[key] = _show(value)
    return flat


def summary(document: Table) -> list[str]:
    """The settings that decide most of the cost, one per line."""
    master = document.get("master_render_settings", Table())
    render = document.get("render_settings", Table())
    resolution = document.get("screen_resolution")
    lines = []
    if isinstance(resolution, list):
        lines.append(f"resolution {'x'.join(_show(v) for v in resolution)} "
                     f"({_show(document.get('screen_mode', sjson.scalar('?')))})")
    for key in ("graphics_quality", "dlss", "dlss_g", "rt_reflections_quality", "rtxgi_quality",
                "gi_quality", "light_quality", "volumetric_fog_quality",
                "ambient_occlusion_quality", "ssr_quality", "texture_quality", "dof_quality",
                "lens_flare_quality", "nv_reflex_low_latency"):
        if key in master:
            lines.append(f"{key} = {_show(master[key])}")
    for key in ("upscaling_quality", "lod_object_multiplier", "lod_scatter_density",
                "sharpness", "vertical_fov"):
        if key in render:
            lines.append(f"render.{key} = {_show(render[key])}")
    performance = document.get("performance_settings")
    if isinstance(performance, Table):
        lines.append("performance: " + ", ".join(f"{k}={_show(v)}" for k, v in performance.items()))
    return lines
