/*
 * SD-card file manager: a small HTTP API for browsing and housekeeping the SD
 * card from the web UI, strictly confined under /sdcard.
 *
 *   GET  /files?op=list&path=<rel>      -> JSON directory listing
 *   GET  /files?op=download&path=<rel>  -> stream a file (chunked)
 *   GET  /files?op=df                   -> {"total":<bytes>,"free":<bytes>}
 *   POST /files  {"op":"mkdir","path":<parent>,"name":<folder>}
 *   POST /files  {"op":"delete","path":<file-or-dir>}    (dir delete is recursive)
 *   POST /files  {"op":"rename","path":<src>,"name":<new basename>}
 *
 * All caller input is relative to /sdcard and is canonicalized through one
 * structural path jail (sdfm_resolve) before any filesystem call. The reserved
 * roots /sdcard, /sdcard/logs and /sdcard/obd_logs cannot be deleted or renamed,
 * and the CSV currently open for writing is refused (409) so logging is never
 * corrupted.
 */
#ifndef __SD_FILEMGR_H__
#define __SD_FILEMGR_H__

#include "esp_http_server.h"

#ifdef __cplusplus
extern "C" {
#endif

extern const httpd_uri_t sd_files_get_uri;   /* GET  /files (list/download/df) */
extern const httpd_uri_t sd_files_post_uri;  /* POST /files (mkdir/delete/rename) */

#ifdef __cplusplus
}
#endif

#endif /* __SD_FILEMGR_H__ */
