#pragma once

#ifdef __cplusplus
extern "C" {
#endif

// Simple compile-time configuration (no Kconfig)
// Adjust these defines as needed or override via compiler -D flags.

#ifndef DEBUG_LOGS_ENABLE
#define DEBUG_LOGS_ENABLE 1
#endif

#ifndef DEBUG_LOGS_QUEUE_LEN
#define DEBUG_LOGS_QUEUE_LEN 32
#endif

#ifndef DEBUG_LOGS_MAX_MESSAGE
#define DEBUG_LOGS_MAX_MESSAGE (10*1024)
#endif

#ifndef DEBUG_LOGS_UDP_PORT
#define DEBUG_LOGS_UDP_PORT 5000
#endif

#ifndef DEBUG_LOGS_UDP_DEST_IP
#define DEBUG_LOGS_UDP_DEST_IP "255.255.255.255"  // broadcast
#endif

#ifndef DEBUG_LOGS_LEVEL
#define DEBUG_LOGS_LEVEL 3  // default DEBUG
#endif

#define DEBUG_LOG_MAX_LINE DEBUG_LOGS_MAX_MESSAGE

#ifdef __cplusplus
}
#endif
