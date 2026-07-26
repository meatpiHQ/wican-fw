cmake_minimum_required(VERSION 3.16)

# Components live in the meatpi-components repo
# (github.com/meatpiHQ/meatpi-components). Clone it next to this
# checkout, or point the MEATPI_COMPONENTS_PATH environment variable at
# its components/ directory. The components/ dir in this repo holds
# only non-component files (docs, legacy reference).
if(DEFINED ENV{MEATPI_COMPONENTS_PATH})
    file(TO_CMAKE_PATH "$ENV{MEATPI_COMPONENTS_PATH}" MEATPI_COMPONENTS_PATH)
    if(NOT EXISTS "${MEATPI_COMPONENTS_PATH}")
        message(FATAL_ERROR "MEATPI_COMPONENTS_PATH does not exist: "
                            "${MEATPI_COMPONENTS_PATH}")
    endif()
else()
    set(MEATPI_COMPONENTS_PATH "")
    foreach(_cand
            "${CMAKE_CURRENT_LIST_DIR}/components/meatpi/components"
            "${CMAKE_CURRENT_LIST_DIR}/../meatpi-components/components"
            "${CMAKE_CURRENT_LIST_DIR}/../../wican-fw-dev/meatpi-components/components")
        get_filename_component(_cand "${_cand}" ABSOLUTE)
        if(EXISTS "${_cand}")
            set(MEATPI_COMPONENTS_PATH "${_cand}")
            break()
        endif()
    endforeach()
    if(NOT MEATPI_COMPONENTS_PATH)
        # Not cloned yet — fetch it INTO this checkout (self-contained;
        # components/meatpi/ is gitignored) so the search finds it on
        # every future configure.
        set(_meatpi_dest "${CMAKE_CURRENT_LIST_DIR}/components/meatpi")
        set(_meatpi_url "https://github.com/meatpiHQ/meatpi-components.git")
        if(DEFINED ENV{MEATPI_COMPONENTS_GIT_URL})
            set(_meatpi_url "$ENV{MEATPI_COMPONENTS_GIT_URL}")
        endif()
        message(STATUS "meatpi-components not found — cloning "
                       "${_meatpi_url} into ${_meatpi_dest}")
        execute_process(
            COMMAND ${CMAKE_COMMAND} -E env GIT_TERMINAL_PROMPT=0
                    git clone "${_meatpi_url}" "${_meatpi_dest}"
            RESULT_VARIABLE _meatpi_clone_rc)
        if(_meatpi_clone_rc EQUAL 0 AND EXISTS "${_meatpi_dest}/components")
            set(MEATPI_COMPONENTS_PATH "${_meatpi_dest}/components")
        else()
            message(FATAL_ERROR
                "meatpi-components could not be cloned automatically — "
                "clone github.com/meatpiHQ/meatpi-components to "
                "components/meatpi in this checkout (or next to it), or "
                "set MEATPI_COMPONENTS_PATH to its components/ directory")
        endif()
    endif()
endif()
message(STATUS "MeatPi components: ${MEATPI_COMPONENTS_PATH}")
list(APPEND EXTRA_COMPONENT_DIRS "${MEATPI_COMPONENTS_PATH}")

# Sibling component packs: any meatpi-components-<suffix> repo cloned
# next to the resolved base repo is picked up as an overlay
# automatically (appended AFTER the base, so a pack component with the
# same name overrides the base one — e.g. its ext_manager). Purely
# optional: a stock checkout has none and this is a no-op.
get_filename_component(_meatpi_repos_dir
                       "${MEATPI_COMPONENTS_PATH}/../.." ABSOLUTE)
file(GLOB _meatpi_packs "${_meatpi_repos_dir}/meatpi-components-*/components")
list(SORT _meatpi_packs)
foreach(_dir IN LISTS _meatpi_packs)
    list(APPEND EXTRA_COMPONENT_DIRS "${_dir}")
    message(STATUS "Component pack overlay: ${_dir}")
endforeach()

# Optional component overlays: extra component dirs appended after the
# bundled set (semicolon-separated). A later dir overrides an earlier
# component with the same name — useful for local experiments or
# alternative component implementations. Captured at configure time;
# after changing it run `idf.py reconfigure` (or delete build/).
if(DEFINED ENV{MEATPI_COMPONENT_OVERLAYS})
    file(TO_CMAKE_PATH "$ENV{MEATPI_COMPONENT_OVERLAYS}" _meatpi_overlays)
    foreach(_dir IN LISTS _meatpi_overlays)
        if(NOT EXISTS "${_dir}")
            message(FATAL_ERROR "component overlay dir does not exist: ${_dir}")
        endif()
        list(APPEND EXTRA_COMPONENT_DIRS "${_dir}")
        message(STATUS "Component overlay: ${_dir}")
    endforeach()
endif()

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(wican-fw)

# Per-function stack-frame sizes (.su files next to every .obj) for the
# stack audit (tools/stack_audit.py — TESTING.md "stack audit"). Build-time
# artifact only; zero runtime cost. Motivated by the 2026-07-22 silent
# PSRAM-stack overflow (autopid DTC job task).
idf_build_set_property(COMPILE_OPTIONS "-fstack-usage" APPEND)
