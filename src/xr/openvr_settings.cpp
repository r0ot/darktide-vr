#include "openvr_settings.h"

#include <Windows.h>

#include <algorithm>
#include <cstdint>
#include <cwctype>
#include <filesystem>

namespace darktidevr::xr {
namespace {

// The project's own declaration of the few OpenVR flat-API entries it calls
// (openvr_capi.h, IVRSettings_003). The function table's order is the ABI;
// the names are only for reading.
using EVRInitError = int;
using EVRSettingsError = int;
constexpr int kVRApplicationBackground = 3;
constexpr const char* kSettingsInterface = "FnTable:IVRSettings_003";

using InitInternal2Fn = std::uint32_t (*)(EVRInitError*, int, const char*);
using ShutdownInternalFn = void (*)();
using GetGenericInterfaceFn = std::intptr_t (*)(const char*, EVRInitError*);

struct IVRSettings003FnTable {
  const char* (*GetSettingsErrorNameFromEnum)(EVRSettingsError);
  void (*SetBool)(const char*, const char*, bool, EVRSettingsError*);
  void (*SetInt32)(const char*, const char*, std::int32_t, EVRSettingsError*);
  void (*SetFloat)(const char*, const char*, float, EVRSettingsError*);
  void (*SetString)(const char*, const char*, const char*, EVRSettingsError*);
  bool (*GetBool)(const char*, const char*, EVRSettingsError*);
  std::int32_t (*GetInt32)(const char*, const char*, EVRSettingsError*);
  float (*GetFloat)(const char*, const char*, EVRSettingsError*);
  void (*GetString)(const char*, const char*, char*, std::uint32_t,
                    EVRSettingsError*);
  void (*RemoveSection)(const char*, EVRSettingsError*);
  void (*RemoveKeyInSection)(const char*, const char*, EVRSettingsError*);
};

const IVRSettings003FnTable& table_of(const void* table) {
  return *static_cast<const IVRSettings003FnTable*>(table);
}

std::wstring registry_string(HKEY root, const wchar_t* subkey,
                             const wchar_t* value_name) {
  DWORD bytes{};
  const auto flags = RRF_RT_REG_SZ | RRF_SUBKEY_WOW6464KEY;
  if (RegGetValueW(root, subkey, value_name, flags, nullptr, nullptr, &bytes) !=
      ERROR_SUCCESS) {
    return {};
  }
  std::wstring value(bytes / sizeof(wchar_t), L'\0');
  if (RegGetValueW(root, subkey, value_name, flags, nullptr, value.data(),
                   &bytes) != ERROR_SUCCESS) {
    return {};
  }
  while (!value.empty() && value.back() == L'\0') {
    value.pop_back();
  }
  return value;
}

}  // namespace

std::wstring active_openxr_runtime_manifest() {
  constexpr auto key = L"SOFTWARE\\Khronos\\OpenXR\\1";
  auto runtime = registry_string(HKEY_CURRENT_USER, key, L"ActiveRuntime");
  if (runtime.empty()) {
    runtime = registry_string(HKEY_LOCAL_MACHINE, key, L"ActiveRuntime");
  }
  return runtime;
}

bool runtime_manifest_is_steamvr(const std::wstring& manifest) {
  auto name = std::filesystem::path{manifest}.filename().wstring();
  std::transform(name.begin(), name.end(), name.begin(), [](wchar_t c) {
    return static_cast<wchar_t>(std::towlower(c));
  });
  return name == L"steamxr_win64.json";
}

std::unique_ptr<OpenVrSettingsStore> OpenVrSettingsStore::connect(
    std::string* failure) {
  const auto fail = [&](const char* reason) {
    if (failure) *failure = reason;
    return std::unique_ptr<OpenVrSettingsStore>{};
  };
  const auto manifest = active_openxr_runtime_manifest();
  if (!runtime_manifest_is_steamvr(manifest)) {
    return fail("runtime-not-steamvr");
  }
  const auto library = std::filesystem::path{manifest}.parent_path() /
                       L"bin" / L"win64" / L"openvr_api.dll";
  // Its own folder first, so whatever it loads beside it is SteamVR's.
  const auto module = LoadLibraryExW(library.c_str(), nullptr,
                                     LOAD_WITH_ALTERED_SEARCH_PATH);
  if (!module) {
    return fail("openvr-api-missing");
  }
  const auto init = reinterpret_cast<InitInternal2Fn>(
      GetProcAddress(module, "VR_InitInternal2"));
  const auto shutdown = reinterpret_cast<ShutdownInternalFn>(
      GetProcAddress(module, "VR_ShutdownInternal"));
  const auto get_interface = reinterpret_cast<GetGenericInterfaceFn>(
      GetProcAddress(module, "VR_GetGenericInterface"));
  if (!init || !shutdown || !get_interface) {
    FreeLibrary(module);
    return fail("openvr-api-entry-missing");
  }
  EVRInitError error{};
  init(&error, kVRApplicationBackground, nullptr);
  if (error != 0) {
    FreeLibrary(module);
    // 121 (Init_NoServerForBackgroundApp) is the common one: SteamVR is
    // not running, and a background application does not start it.
    return fail(error == 121 ? "steamvr-not-running" : "openvr-init-failed");
  }
  const auto table = get_interface(kSettingsInterface, &error);
  if (error != 0 || table == 0) {
    shutdown();
    FreeLibrary(module);
    return fail("openvr-settings-interface-missing");
  }
  return std::unique_ptr<OpenVrSettingsStore>(new OpenVrSettingsStore(
      module, reinterpret_cast<const void*>(table)));
}

OpenVrSettingsStore::OpenVrSettingsStore(void* module, const void* table)
    : module_(module), table_(table) {}

OpenVrSettingsStore::~OpenVrSettingsStore() {
  const auto module = static_cast<HMODULE>(module_);
  if (const auto shutdown = reinterpret_cast<ShutdownInternalFn>(
          GetProcAddress(module, "VR_ShutdownInternal"))) {
    shutdown();
  }
  FreeLibrary(module);
}

std::optional<std::int32_t> OpenVrSettingsStore::get_int(const char* section,
                                                         const char* key) {
  EVRSettingsError error{};
  const auto value = table_of(table_).GetInt32(section, key, &error);
  if (error != 0) return std::nullopt;
  return value;
}

bool OpenVrSettingsStore::set_int(const char* section, const char* key,
                                  std::int32_t value) {
  EVRSettingsError error{};
  table_of(table_).SetInt32(section, key, value, &error);
  return error == 0;
}

std::optional<bool> OpenVrSettingsStore::get_bool(const char* section,
                                                  const char* key) {
  EVRSettingsError error{};
  const auto value = table_of(table_).GetBool(section, key, &error);
  if (error != 0) return std::nullopt;
  return value;
}

bool OpenVrSettingsStore::set_bool(const char* section, const char* key,
                                   bool value) {
  EVRSettingsError error{};
  table_of(table_).SetBool(section, key, value, &error);
  return error == 0;
}

}  // namespace darktidevr::xr
