# The following lines of boilerplate have to be in your project's
# CMakeLists in this exact order for cmake to work correctly
cmake_minimum_required(VERSION 3.5)


set(PROJECT_PREF "wican-fw")
set(PROJECT_VER "v292_hv300")
#set(PROJECT_VER "v155_hv210")

# set(PROJECT_PREF "wican-fw-usb")
# set(PROJECT_VER "v292_hv150")

set(PROJECT_VERU ${PROJECT_VER})
set(PROJECT_BIN "${PROJECT_PREF}_${PROJECT_VERU}")
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project("${PROJECT_BIN}")
