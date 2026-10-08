#include "core/steamvr_session_settings.h"

#include <cstdlib>
#include <fstream>
#include <sstream>
#include <system_error>

namespace darktidevr::core {
namespace {

constexpr const char* kHeader = "darktidevr_steamvr_backup=1";

bool refresh_rate_in_range(long value) { return value >= 30 && value <= 1000; }

std::optional<std::string> read_text(const std::filesystem::path& path) {
  std::error_code error;
  if (!std::filesystem::exists(path, error)) {
    return std::nullopt;
  }
  std::ifstream input(path, std::ios::binary);
  if (!input) {
    return std::nullopt;
  }
  std::ostringstream text;
  text << input.rdbuf();
  return text.str();
}

// Written beside the destination and renamed over it, so a crash part-way
// leaves either no backup or a whole one.
bool write_text(const std::filesystem::path& path, const std::string& text) {
  std::error_code error;
  std::filesystem::create_directories(path.parent_path(), error);
  auto temporary = path;
  temporary += ".tmp";
  {
    std::ofstream output(temporary, std::ios::binary | std::ios::trunc);
    if (!output) {
      return false;
    }
    output << text;
    output.flush();
    if (!output) {
      return false;
    }
  }
  std::filesystem::rename(temporary, path, error);
  if (error) {
    std::filesystem::remove(temporary, error);
    return false;
  }
  return true;
}

// Sets every value present. Stops at the first failure and says so.
bool set_values(SteamVrSettingsStore& store, const SteamVrSessionValues& values,
                SteamVrSessionValues* set) {
  if (values.refresh_rate_hz) {
    if (!store.set_int(kSteamVrSection, kSteamVrRefreshRateKey,
                       *values.refresh_rate_hz)) {
      return false;
    }
    if (set) set->refresh_rate_hz = values.refresh_rate_hz;
  }
  if (values.motion_smoothing) {
    if (!store.set_bool(kSteamVrSection, kSteamVrMotionSmoothingKey,
                        *values.motion_smoothing)) {
      return false;
    }
    if (set) set->motion_smoothing = values.motion_smoothing;
  }
  return true;
}

}  // namespace

std::string serialize_steamvr_backup(const SteamVrSessionValues& values) {
  std::string text = std::string(kHeader) + "\n";
  if (values.refresh_rate_hz) {
    text += "refresh_rate_hz=" + std::to_string(*values.refresh_rate_hz) + "\n";
  }
  if (values.motion_smoothing) {
    text += std::string("motion_smoothing=") +
            (*values.motion_smoothing ? "1" : "0") + "\n";
  }
  return text;
}

std::optional<SteamVrSessionValues> parse_steamvr_backup(
    const std::string& text) {
  std::istringstream lines(text);
  std::string line;
  if (!std::getline(lines, line)) {
    return std::nullopt;
  }
  if (!line.empty() && line.back() == '\r') line.pop_back();
  if (line != kHeader) {
    return std::nullopt;
  }
  SteamVrSessionValues values;
  while (std::getline(lines, line)) {
    if (!line.empty() && line.back() == '\r') line.pop_back();
    if (line.empty()) continue;
    const auto equals = line.find('=');
    if (equals == std::string::npos) return std::nullopt;
    const auto key = line.substr(0, equals);
    const auto value = line.substr(equals + 1);
    char* end{};
    const auto number = std::strtol(value.c_str(), &end, 10);
    if (value.empty() || *end != '\0') return std::nullopt;
    if (key == "refresh_rate_hz" && refresh_rate_in_range(number)) {
      values.refresh_rate_hz = static_cast<std::int32_t>(number);
    } else if (key == "motion_smoothing" && (number == 0 || number == 1)) {
      values.motion_smoothing = number == 1;
    } else {
      return std::nullopt;
    }
  }
  return values;
}

SteamVrSessionResult restore_steamvr_session_settings(
    SteamVrSettingsStore& store, const std::filesystem::path& backup_path) {
  SteamVrSessionResult result;
  const auto text = read_text(backup_path);
  if (!text) {
    result.ok = true;
    return result;
  }
  const auto values = parse_steamvr_backup(*text);
  if (!values) {
    result.detail = "backup-unreadable";
    return result;
  }
  if (!set_values(store, *values, &result.applied)) {
    // Kept: the next attempt can still put the user's values back.
    result.detail = "restore-set-failed";
    return result;
  }
  std::error_code error;
  std::filesystem::remove(backup_path, error);
  result.ok = true;
  return result;
}

SteamVrSessionResult apply_steamvr_session_settings(
    SteamVrSettingsStore& store, const SteamVrSessionValues& request,
    const std::filesystem::path& backup_path) {
  SteamVrSessionResult result;
  // A backup here means a session ended without restoring (the game was
  // killed, the PC crashed). Its values are the user's; the live settings
  // are the mod's. Restore first, or this session would back up the mod's
  // values as if they were the user's and lose the real ones for good.
  {
    std::error_code error;
    if (std::filesystem::exists(backup_path, error)) {
      const auto restored = restore_steamvr_session_settings(store, backup_path);
      if (!restored.ok) {
        result.detail = "stale-" + restored.detail;
        return result;
      }
      result.recovered_stale_backup = true;
    }
  }
  if (request.empty()) {
    result.ok = true;
    return result;
  }

  SteamVrSessionValues backup;
  SteamVrSessionValues changes;
  if (request.refresh_rate_hz) {
    const auto current =
        store.get_int(kSteamVrSection, kSteamVrRefreshRateKey);
    if (!current) {
      result.detail = "read-refresh-rate-failed";
      return result;
    }
    result.previous.refresh_rate_hz = current;
    if (*current != *request.refresh_rate_hz) {
      backup.refresh_rate_hz = current;
      changes.refresh_rate_hz = request.refresh_rate_hz;
    }
  }
  if (request.motion_smoothing) {
    const auto current =
        store.get_bool(kSteamVrSection, kSteamVrMotionSmoothingKey);
    if (!current) {
      result.detail = "read-motion-smoothing-failed";
      return result;
    }
    result.previous.motion_smoothing = current;
    if (*current != *request.motion_smoothing) {
      backup.motion_smoothing = current;
      changes.motion_smoothing = request.motion_smoothing;
    }
  }
  if (changes.empty()) {
    result.ok = true;  // Already as asked; nothing to put back later.
    return result;
  }
  if (!write_text(backup_path, serialize_steamvr_backup(backup))) {
    result.detail = "backup-write-failed";
    return result;
  }
  if (!set_values(store, changes, &result.applied)) {
    // Put back whatever did change; the backup holds exactly those keys.
    set_values(store, backup, nullptr);
    std::error_code error;
    std::filesystem::remove(backup_path, error);
    result.applied = {};
    result.detail = "set-failed";
    return result;
  }
  result.ok = true;
  return result;
}

}  // namespace darktidevr::core
