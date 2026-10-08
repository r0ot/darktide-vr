#pragma once

#include "core/steamvr_session_settings.h"

#include <memory>
#include <string>

namespace darktidevr::xr {

// The OpenXR runtime manifest the loader will use (HKCU, then HKLM), empty
// when none is registered.
std::wstring active_openxr_runtime_manifest();

// SteamVR's manifest is steamxr_win64.json; the same test the readiness gate
// makes (tools/unattended/xr-readiness.ps1, Get-XrRuntimeProfile).
bool runtime_manifest_is_steamvr(const std::wstring& manifest);

// Closes SteamVR's dashboard with the vrcmd.exe SteamVR ships beside its
// runtime (`vrcmd --hidedashboard`), without waiting for it. While the
// dashboard is up SteamVR keeps the controllers from the application -- its
// interaction profile reads <null> -- and the OpenXR session still says
// FOCUSED, so nothing in OpenXR can tell (8 October). False with a reason
// when SteamVR is not the runtime or vrcmd cannot be started.
bool hide_steamvr_dashboard(std::string* failure);

// SteamVR's live settings through SteamVR's OWN openvr_api.dll, loaded from
// the runtime's bin\win64 folder: nothing of OpenVR is built or shipped here.
// It connects as a background application, which never starts SteamVR and
// fails if it is not running. The few flat-API signatures used are declared
// in openvr_settings.cpp, as the Streamline ABI is in the producer.
//
// Use it in a process that has no OpenXR instance: the SteamVR OpenXR runtime
// is the same vrclient this loads, and the viewer keeps the two apart by
// running this in a child process of itself (--steamvr-settings).
class OpenVrSettingsStore final : public core::SteamVrSettingsStore {
 public:
  // Null with a reason when SteamVR is not the runtime, its openvr_api.dll
  // is missing, or SteamVR is not running.
  static std::unique_ptr<OpenVrSettingsStore> connect(std::string* failure);
  ~OpenVrSettingsStore() override;

  OpenVrSettingsStore(const OpenVrSettingsStore&) = delete;
  OpenVrSettingsStore& operator=(const OpenVrSettingsStore&) = delete;

  std::optional<std::int32_t> get_int(const char* section,
                                      const char* key) override;
  bool set_int(const char* section, const char* key,
               std::int32_t value) override;
  std::optional<bool> get_bool(const char* section, const char* key) override;
  bool set_bool(const char* section, const char* key, bool value) override;

 private:
  OpenVrSettingsStore(void* module, const void* table);
  void* module_{};
  const void* table_{};
};

}  // namespace darktidevr::xr
