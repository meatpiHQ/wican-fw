# The following lines of boilerplate have to be in your project's
# CMakeLists in this exact order for cmake to work correctly
cmake_minimum_required(VERSION 3.5)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)

# CherryUSB (no Kconfig): compile host + USB-Ethernet class drivers.
# Runtime selection is handled by the usb_eth_host component.
set(CONFIG_CHERRYUSB_HOST 1 CACHE INTERNAL "Enable CherryUSB host stack")
set(CONFIG_CHERRYUSB_HOST_DWC2_ESP 1 CACHE INTERNAL "Use ESP DWC2 host glue")
set(CONFIG_CHERRYUSB_OSAL "idf" CACHE INTERNAL "Use ESP-IDF OSAL")

# USB ETH RX buffer sizes: must be >= TCP_WND (20480) for best throughput.
# CherryUSB NCM source: "Rx packet is overflow, please reduce tcp window size
# or increase CONFIG_USBHOST_CDC_NCM_ETH_MAX_RX_SIZE"
# The NCM/RNDIS/ASIX/RTL8152 drivers each have separate RX/TX config names.
# The ECM driver uses a single CONFIG_USBHOST_CDC_ECM_ETH_MAX_SIZE (one frame = 1514B; no aggregation).
# TX buffers stay at 2048: lwIP doesn't support multi-pbuf so larger TX brings no gain.
# Define all here (project-level) to keep managed_components untouched.
add_compile_definitions(CONFIG_USBHOST_CDC_NCM_ETH_MAX_RX_SIZE=16384)
add_compile_definitions(CONFIG_USBHOST_CDC_NCM_ETH_MAX_TX_SIZE=2048)
add_compile_definitions(CONFIG_USBHOST_RNDIS_ETH_MAX_RX_SIZE=16384)
add_compile_definitions(CONFIG_USBHOST_RNDIS_ETH_MAX_TX_SIZE=2048)
add_compile_definitions(CONFIG_USBHOST_ASIX_ETH_MAX_RX_SIZE=16384)
add_compile_definitions(CONFIG_USBHOST_ASIX_ETH_MAX_TX_SIZE=2048)
add_compile_definitions(CONFIG_USBHOST_RTL8152_ETH_MAX_RX_SIZE=16384)
add_compile_definitions(CONFIG_USBHOST_RTL8152_ETH_MAX_TX_SIZE=2048)


set(WICAN_V210 1)
set(WICAN_V300 2)
set(WICAN_USB_V100 3)
set(WICAN_PRO 4)

set(WICAN_V210_STR "OBD")
set(WICAN_V300_STR "OBD")
set(WICAN_USB_V100_STR "USB")
set(WICAN_PRO_STR "OBD-PRO")

# Set these without quotes, as they're string literals
set(WICAN_V210_PREF "obd")
set(WICAN_V300_PREF "obd")
set(WICAN_USB_V100_PREF "usb")
set(WICAN_PRO_PREF "obd_pro")

add_definitions(-DWICAN_V210=${WICAN_V210})
add_definitions(-DWICAN_V300=${WICAN_V300})
add_definitions(-DWICAN_USB_V100=${WICAN_USB_V100})
add_definitions(-DWICAN_PRO=${WICAN_PRO})

add_definitions(-DWICAN_V210_STR="${WICAN_V210_STR}")
add_definitions(-DWICAN_V300_STR="${WICAN_V300_STR}")
add_definitions(-DWICAN_USB_V100_STR="${WICAN_USB_V100_STR}")
add_definitions(-DWICAN_PRO_STR="${WICAN_PRO_STR}")

execute_process(
    COMMAND git describe --tags --always --dirty
    WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
    OUTPUT_VARIABLE GIT_SHA
    OUTPUT_STRIP_TRAILING_WHITESPACE
)

set(GIT_SHA1 "${GIT_SHA}")
string(REPLACE "." "" GIT_SHA "${GIT_SHA}")

add_definitions(-DGIT_SHA="${GIT_SHA1}")

# Uncomment and use one of the following as needed
# set(HARDWARE_VER ${WICAN_V210})
# set(HARDWARE_VER ${WICAN_V300})
# set(HARDWARE_VER ${WICAN_USB_V100})
set(HARDWARE_VER ${WICAN_PRO})

add_definitions(-DHARDWARE_VER=${HARDWARE_VER})

set(PROJECT_PREF "wican-fw")

# Set HW_PREF based on HARDWARE_VER
if (HARDWARE_VER EQUAL ${WICAN_PRO})
    set(HW_PREF "${WICAN_PRO_PREF}")
    set(HW_VER "${WICAN_PRO_STR}")
elseif (HARDWARE_VER EQUAL ${WICAN_USB_V100})
    set(HW_PREF "${WICAN_USB_V100_PREF}")
    set(HW_VER "${WICAN_USB_V100_STR}")
elseif (HARDWARE_VER EQUAL ${WICAN_V210})
    set(HW_PREF "${WICAN_V210_PREF}")
    set(HW_VER "${WICAN_V210_STR}")
else()
    set(HW_PREF "${WICAN_V300_PREF}")
    set(HW_VER "${WICAN_V300_STR}")
endif()

add_definitions(-DHARDWARE_VERSION="${HW_VER}")

# Form the project binary name
set(PRJ_BIN "${PROJECT_PREF}_${HW_PREF}_${GIT_SHA}")
project("${PRJ_BIN}")
