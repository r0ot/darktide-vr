add_executable(darktidevr-particle-simulation-once-tests main.cpp)
target_compile_options(darktidevr-particle-simulation-once-tests PRIVATE
  /W4 /WX /permissive-)
target_compile_definitions(darktidevr-particle-simulation-once-tests PRIVATE
  NOMINMAX WIN32_LEAN_AND_MEAN UNICODE _UNICODE)
target_include_directories(darktidevr-particle-simulation-once-tests PRIVATE
  "${PROJECT_SOURCE_DIR}/src")
add_test(NAME particle_simulation_once
  COMMAND darktidevr-particle-simulation-once-tests)
set_tests_properties(particle_simulation_once PROPERTIES
  PASS_REGULAR_EXPRESSION "particle_simulation_once=pass"
  LABELS "native;particles;offline")
