add_executable(darktidevr-steamvr-session-settings-tests main.cpp)
target_compile_options(darktidevr-steamvr-session-settings-tests PRIVATE
  /W4 /WX /permissive-)
target_compile_definitions(darktidevr-steamvr-session-settings-tests PRIVATE
  NOMINMAX WIN32_LEAN_AND_MEAN UNICODE _UNICODE)
target_link_libraries(darktidevr-steamvr-session-settings-tests PRIVATE
  darktidevr_core)
add_test(NAME steamvr_session_settings
  COMMAND darktidevr-steamvr-session-settings-tests)
set_tests_properties(steamvr_session_settings PROPERTIES
  PASS_REGULAR_EXPRESSION "steamvr_session_settings=pass"
  LABELS "settings;openxr")
