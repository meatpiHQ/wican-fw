idf_component_register(
    SRCS "filesystem.c"
    INCLUDE_DIRS "."
    REQUIRES joltwallet__littlefs fatfs wear_levelling log
    PRIV_REQUIRES main
)
