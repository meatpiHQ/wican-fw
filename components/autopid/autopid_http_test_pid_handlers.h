#pragma once

#include <stdbool.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

bool autopid_test_pid_handle_std(const char *name,
                                 const char *protocol,
                                 const char *rxheader,
                                 double *out_value,
                                 const char **out_unit,
                                 char *err_msg,
                                 size_t err_msg_sz);

bool autopid_test_pid_handle_custom_vehicle(const char *pid_cmd,
                                            const char *expr,
                                            const char *init,
                                            const char *pid_init,
                                            double *out_value,
                                            char *err_msg,
                                            size_t err_msg_sz);

#ifdef __cplusplus
}
#endif
