# Edit following two lines to set component requirements (see docs)
set(COMPONENT_REQUIRES )
set(COMPONENT_PRIV_REQUIRES )

set(COMPONENT_SRCS "main.c" "tcp_server.c" "config_server.c" "realdash.c" "slcan.c" "can.c" "ble.c")
set(COMPONENT_ADD_INCLUDEDIRS "")

register_component()
