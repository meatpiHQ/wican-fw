# Install script for directory: C:/Users/Ali/.espressif/frameworks/esp-idf-v4.4

# Set the install prefix
if(NOT DEFINED CMAKE_INSTALL_PREFIX)
  set(CMAKE_INSTALL_PREFIX "C:/Program Files (x86)/bootloader")
endif()
string(REGEX REPLACE "/$" "" CMAKE_INSTALL_PREFIX "${CMAKE_INSTALL_PREFIX}")

# Set the install configuration name.
if(NOT DEFINED CMAKE_INSTALL_CONFIG_NAME)
  if(BUILD_TYPE)
    string(REGEX REPLACE "^[^A-Za-z0-9_]+" ""
           CMAKE_INSTALL_CONFIG_NAME "${BUILD_TYPE}")
  else()
    set(CMAKE_INSTALL_CONFIG_NAME "")
  endif()
  message(STATUS "Install configuration: \"${CMAKE_INSTALL_CONFIG_NAME}\"")
endif()

# Set the component getting installed.
if(NOT CMAKE_INSTALL_COMPONENT)
  if(COMPONENT)
    message(STATUS "Install component: \"${COMPONENT}\"")
    set(CMAKE_INSTALL_COMPONENT "${COMPONENT}")
  else()
    set(CMAKE_INSTALL_COMPONENT)
  endif()
endif()

# Is this installation the result of a crosscompile?
if(NOT DEFINED CMAKE_CROSSCOMPILING)
  set(CMAKE_CROSSCOMPILING "TRUE")
endif()

# Set default install directory permissions.
if(NOT DEFINED CMAKE_OBJDUMP)
  set(CMAKE_OBJDUMP "C:/Users/Ali/.espressif/tools/riscv32-esp-elf/esp-2021r2-patch2-8.4.0/riscv32-esp-elf/bin/riscv32-esp-elf-objdump.exe")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for each subdirectory.
  include("C:/Users/Ali/Desktop/Projects/project_wican/wican-fw/build/bootloader/esp-idf/newlib/cmake_install.cmake")
  include("C:/Users/Ali/Desktop/Projects/project_wican/wican-fw/build/bootloader/esp-idf/hal/cmake_install.cmake")
  include("C:/Users/Ali/Desktop/Projects/project_wican/wican-fw/build/bootloader/esp-idf/soc/cmake_install.cmake")
  include("C:/Users/Ali/Desktop/Projects/project_wican/wican-fw/build/bootloader/esp-idf/micro-ecc/cmake_install.cmake")
  include("C:/Users/Ali/Desktop/Projects/project_wican/wican-fw/build/bootloader/esp-idf/spi_flash/cmake_install.cmake")
  include("C:/Users/Ali/Desktop/Projects/project_wican/wican-fw/build/bootloader/esp-idf/bootloader_support/cmake_install.cmake")
  include("C:/Users/Ali/Desktop/Projects/project_wican/wican-fw/build/bootloader/esp-idf/efuse/cmake_install.cmake")
  include("C:/Users/Ali/Desktop/Projects/project_wican/wican-fw/build/bootloader/esp-idf/esp_system/cmake_install.cmake")
  include("C:/Users/Ali/Desktop/Projects/project_wican/wican-fw/build/bootloader/esp-idf/esp_hw_support/cmake_install.cmake")
  include("C:/Users/Ali/Desktop/Projects/project_wican/wican-fw/build/bootloader/esp-idf/riscv/cmake_install.cmake")
  include("C:/Users/Ali/Desktop/Projects/project_wican/wican-fw/build/bootloader/esp-idf/esp32c3/cmake_install.cmake")
  include("C:/Users/Ali/Desktop/Projects/project_wican/wican-fw/build/bootloader/esp-idf/esp_common/cmake_install.cmake")
  include("C:/Users/Ali/Desktop/Projects/project_wican/wican-fw/build/bootloader/esp-idf/esp_rom/cmake_install.cmake")
  include("C:/Users/Ali/Desktop/Projects/project_wican/wican-fw/build/bootloader/esp-idf/log/cmake_install.cmake")
  include("C:/Users/Ali/Desktop/Projects/project_wican/wican-fw/build/bootloader/esp-idf/esptool_py/cmake_install.cmake")
  include("C:/Users/Ali/Desktop/Projects/project_wican/wican-fw/build/bootloader/esp-idf/partition_table/cmake_install.cmake")
  include("C:/Users/Ali/Desktop/Projects/project_wican/wican-fw/build/bootloader/esp-idf/bootloader/cmake_install.cmake")
  include("C:/Users/Ali/Desktop/Projects/project_wican/wican-fw/build/bootloader/esp-idf/freertos/cmake_install.cmake")
  include("C:/Users/Ali/Desktop/Projects/project_wican/wican-fw/build/bootloader/esp-idf/main/cmake_install.cmake")

endif()

