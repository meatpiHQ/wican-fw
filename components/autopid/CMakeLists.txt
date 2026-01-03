idf_component_register(
    SRCS "autopid.c" "autopid_config.c" "autopid_http.c" "autopid_http_test_pid.c" "autopid_http_test_pid_util.c"
    INCLUDE_DIRS "."
    REQUIRES main )
