# The following lines of boilerplate have to be in your project's
# CMakeLists in this exact order for cmake to work correctly
cmake_minimum_required(VERSION 3.5)

set(WICAN_V210 1)
set(WICAN_V300 2)
set(WICAN_USB_V100 3)
set(WICAN_PRO 4)

add_definitions(-DWICAN_V210=${WICAN_V210})
add_definitions(-DWICAN_V300=${WICAN_V300})
add_definitions(-DWICAN_USB_V100=${WICAN_USB_V100})
add_definitions(-DWICAN_PRO=${WICAN_PRO})

# Uncomment and use one of the following as needed
# set(HARDWARE_VER WICAN_V210)
# set(HARDWARE_VER WICAN_V300)
# set(HARDWARE_VER WICAN_USB_V100)
set(HARDWARE_VER WICAN_PRO)

add_definitions(-DHARDWARE_VER=${HARDWARE_VER})

set(PROJECT_PREF "wican-fw")
set(PROJECT_VER "v298_hv300")
#set(PROJECT_VER "v155_hv210")

# set(PROJECT_PREF "wican-fw-usb")
# set(PROJECT_VER "v298_hv150")
set(PROJECT_VERU ${PROJECT_VER})
set(PROJECT_BIN "${PROJECT_PREF}_${PROJECT_VERU}")
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project("${PROJECT_BIN}")

