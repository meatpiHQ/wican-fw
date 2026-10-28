cmake_minimum_required(VERSION 3.16)

# Components live in the meatpi-components repo
# (github.com/meatpiHQ/meatpi-components). Clone it next to this
# checkout, or point the MEATPI_COMPONENTS_PATH environment variable at
# its components/ directory. The components/ dir in this repo holds
# only non-component files (docs, legacy reference). Search order when
# the variable is unset: the dev layout (components/meatpi-components two
# levels up, the tree build.ps1 builds), a clone beside this checkout,
# then the clone inside this checkout (components/meatpi, the auto-clone
# below). The in-tree clone comes LAST so a stale copy of it never
# shadows a live tree (it did until 2026-10-29).
if(DEFINED ENV{MEATPI_COMPONENTS_PATH})
    file(TO_CMAKE_PATH "$ENV{MEATPI_COMPONENTS_PATH}" MEATPI_COMPONENTS_PATH)
    if(NOT EXISTS "${MEATPI_COMPONENTS_PATH}")
        message(FATAL_ERROR "MEATPI_COMPONENTS_PATH does not exist: "
                            "${MEATPI_COMPONENTS_PATH}")
    endif()
else()
    set(MEATPI_COMPONENTS_PATH "")
    foreach(_cand
            "${CMAKE_CURRENT_LIST_DIR}/../../components/meatpi-components/components"
            "${CMAKE_CURRENT_LIST_DIR}/../meatpi-components/components"
            "${CMAKE_CURRENT_LIST_DIR}/components/meatpi/components")
        get_filename_component(_cand "${_cand}" ABSOLUTE)
        if(EXISTS "${_cand}")
            set(MEATPI_COMPONENTS_PATH "${_cand}")
            break()
        endif()
    endforeach()
    if(NOT MEATPI_COMPONENTS_PATH)
        # Not cloned yet: fetch it INTO this checkout (self-contained;
        # components/meatpi/ is gitignored) so the search finds it on
        # every future configure.
        set(_meatpi_dest "${CMAKE_CURRENT_LIST_DIR}/components/meatpi")
        set(_meatpi_url "https://github.com/meatpiHQ/meatpi-components.git")
        if(DEFINED ENV{MEATPI_COMPONENTS_GIT_URL})
            set(_meatpi_url "$ENV{MEATPI_COMPONENTS_GIT_URL}")
        endif()
        message(STATUS "meatpi-components not found: cloning "
                       "${_meatpi_url} into ${_meatpi_dest}")
        execute_process(
            COMMAND ${CMAKE_COMMAND} -E env GIT_TERMINAL_PROMPT=0
                    git clone "${_meatpi_url}" "${_meatpi_dest}"
            RESULT_VARIABLE _meatpi_clone_rc)
        if(_meatpi_clone_rc EQUAL 0 AND EXISTS "${_meatpi_dest}/components")
            set(MEATPI_COMPONENTS_PATH "${_meatpi_dest}/components")
        else()
            message(FATAL_ERROR
                "meatpi-components could not be cloned automatically: "
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
# same name overrides the base one, e.g. its ext_manager). Purely
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
# component with the same name: useful for local experiments or
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

# ---- project name and version (2026-10-18, internal/TASK_fw_update.md) -----
# The project name is CONSTANT per hardware line: wican-fw_<hw prefix>
# (`wican-fw_obd_pro`), so the app descriptor's project_name identifies the
# product (ota_manager refuses foreign images on it) and the image is
# build/wican-fw_obd_pro.bin whatever the commit. The app's version string
# (esp_app_desc) is the release number in version.txt (`6.1.0-alfa.1`: IDF
# reads the file because CONFIG_APP_PROJECT_VER_FROM_CONFIG is off); the
# git describe of the build goes into the WICAN_FW_BUILD define, which main
# hands to dev_status_manager (`fw_build` on /api/info and the About page).
# build/flash_args and build/project_description.json (app_bin) always name
# the current image - tools/fw_bin.py resolves it for scripts.
execute_process(
    COMMAND git describe --tags --always --dirty
    WORKING_DIRECTORY "${CMAKE_CURRENT_LIST_DIR}"
    OUTPUT_VARIABLE WICAN_GIT_DESC
    OUTPUT_STRIP_TRAILING_WHITESPACE
    ERROR_QUIET)
if(NOT WICAN_GIT_DESC)
    set(WICAN_GIT_DESC "nogit")
endif()
string(REPLACE "." "" WICAN_GIT_TAG "${WICAN_GIT_DESC}")

set(WICAN_HW_CFG "")
foreach(_cfg "${CMAKE_CURRENT_LIST_DIR}/sdkconfig"
             "${CMAKE_CURRENT_LIST_DIR}/sdkconfig.defaults")
    if(EXISTS "${_cfg}")
        file(STRINGS "${_cfg}" _hw_line REGEX "^CONFIG_WICAN_HW_VERSION=")
        if(_hw_line)
            string(REGEX REPLACE "^CONFIG_WICAN_HW_VERSION=\"?([^\"]*)\"?$"
                   "\\1" WICAN_HW_CFG "${_hw_line}")
            break()
        endif()
    endif()
endforeach()
string(TOUPPER "${WICAN_HW_CFG}" _hw_upper)
if(_hw_upper MATCHES "USB")
    set(WICAN_HW_PREF "usb")
elseif(_hw_upper STREQUAL "" OR _hw_upper MATCHES "PRO")
    set(WICAN_HW_PREF "obd_pro")
else()
    set(WICAN_HW_PREF "obd")
endif()

set(WICAN_PRJ_BIN "wican-fw_${WICAN_HW_PREF}")
file(STRINGS "${CMAKE_CURRENT_LIST_DIR}/version.txt" WICAN_VERSION_TXT)
message(STATUS "WiCAN binary: ${WICAN_PRJ_BIN}.bin (hw '${WICAN_HW_CFG}', version ${WICAN_VERSION_TXT}, build ${WICAN_GIT_DESC})")
project("${WICAN_PRJ_BIN}")

# the build id for main (dev_status_manager_set_app_build): main's objects
# alone depend on it, so a new commit does not recompile every component
idf_component_get_property(_wican_main_lib main COMPONENT_LIB)
target_compile_definitions(${_wican_main_lib} PRIVATE
                           WICAN_FW_BUILD="${WICAN_GIT_DESC}")

# Per-function stack-frame sizes (.su files next to every .obj) for the
# stack audit (tools/stack_audit.py: TESTING.md "stack audit"). Build-time
# artifact only; zero runtime cost. Motivated by the 2026-07-22 silent
# PSRAM-stack overflow (autopid DTC job task).
idf_build_set_property(COMPILE_OPTIONS "-fstack-usage" APPEND)
