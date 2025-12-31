idf_component_register(
    SRCS "usb_acm_cli.c"
    INCLUDE_DIRS "include"
    REQUIRES log cherry-embedded__cherryusb
)
