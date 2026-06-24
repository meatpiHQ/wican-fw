/*
 * SD-card file manager HTTP API. See sd_filemgr.h for the endpoint contract.
 *
 * Security model: every caller-supplied path is RELATIVE to /sdcard and is run
 * through sdfm_resolve(), a structural jail that walks the path with a logical
 * depth counter -- a ".." can only pop and can never take the depth below 0, so
 * the canonical result is ALWAYS confined under /sdcard regardless of encoding
 * tricks. Single-name fields (mkdir/rename) additionally pass sdfm_valid_name().
 * Destructive ops also enforce a reserved-root guard and the active-log guard.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <ctype.h>
#include <errno.h>
#include <sys/stat.h>
#include <unistd.h>
#include <dirent.h>

#include "esp_log.h"
#include "esp_http_server.h"
#include "esp_heap_caps.h"
#include "cJSON.h"

#include "esp_vfs_fat.h"
#include "sdcard.h"
#include "csv_logger.h"
#include "event_log.h"

static const char *TAG = "SD_FILEMGR";

#define SDFM_ROOT            SD_CARD_MOUNT_POINT       /* "/sdcard" */
#define SDFM_OBD_DIR         SDFM_ROOT "/obd_logs"     /* reserved: SQLite OBD logger */
/* CSV_LOGGER_DIR ("/sdcard/logs") comes from csv_logger.h -- also reserved. */

/* Fully protected subtrees: neither the dir NOR anything under it may be deleted
 * or renamed (the reserved roots above protect only the dir itself, not contents). */
#define SDFM_SVI_DIR         SDFM_ROOT "/System Volume Information"  /* FAT system dir */
#define SDFM_WICAN_DIR       SDFM_ROOT "/wican_data"                 /* device data + web assets */

#define SDFM_MAX_DEPTH       16
#define SDFM_PATH_MAX        320
#define SDFM_NAME_MAX        255
#define SDFM_LIST_CAP        1000
#define SDFM_RMTREE_DEPTH    16
#define SDFM_STREAM_BUF      (8 * 1024)
#define SDFM_POST_BODY_MAX   1024

/* ---------------- path jail ---------------- */

static int sdfm_hexval(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

/* In-place percent-decode (%xx). '+' is left as-is (encodeURIComponent uses %20). */
static void sdfm_url_decode(char *s)
{
    char *o = s;
    for (char *p = s; *p; p++)
    {
        if (*p == '%' && p[1] && p[2])
        {
            int hi = sdfm_hexval(p[1]);
            int lo = sdfm_hexval(p[2]);
            if (hi >= 0 && lo >= 0)
            {
                *o++ = (char)((hi << 4) | lo);
                p += 2;
                continue;
            }
        }
        *o++ = *p;
    }
    *o = '\0';
}

/*
 * Convert a relative path into a canonical absolute path confined under /sdcard.
 * Returns true on success (out is NUL-terminated, depth-bounded, jailed).
 */
static bool sdfm_resolve(const char *rel, char *out, size_t out_sz)
{
    if (rel == NULL) { rel = ""; }

    size_t L = strnlen(rel, 1024);
    if (L == 1024)        { return false; }    /* overlong / unterminated */
    if (rel[0] == '/')    { return false; }    /* no absolute input */
    if (strchr(rel, '\\')) { return false; }   /* no backslash separators */

    char tmp[1024];
    strlcpy(tmp, rel, sizeof(tmp));

    char *parts[SDFM_MAX_DEPTH];
    int depth = 0;
    char *save = NULL;
    for (char *tok = strtok_r(tmp, "/", &save); tok != NULL; tok = strtok_r(NULL, "/", &save))
    {
        if (tok[0] == '\0' || strcmp(tok, ".") == 0)
        {
            continue;
        }
        if (strcmp(tok, "..") == 0)
        {
            if (depth == 0) { return false; }  /* STRUCTURAL JAIL: cannot escape root */
            depth--;
            continue;
        }
        if (strlen(tok) > SDFM_NAME_MAX) { return false; }
        if (depth == SDFM_MAX_DEPTH)     { return false; }
        parts[depth++] = tok;
    }

    int n = snprintf(out, out_sz, "%s", SDFM_ROOT);
    if (n < 0 || (size_t)n >= out_sz) { return false; }
    for (int i = 0; i < depth; i++)
    {
        int m = snprintf(out + n, out_sz - n, "/%s", parts[i]);
        if (m < 0 || (size_t)(n + m) >= out_sz) { return false; }
        n += m;
    }

    /* Belt-and-suspenders: the canonical result must literally be the root or under it. */
    if (strcmp(out, SDFM_ROOT) != 0 &&
        strncmp(out, SDFM_ROOT "/", strlen(SDFM_ROOT) + 1) != 0)
    {
        return false;
    }
    return true;
}

/* Validate a single name component (mkdir folder, rename new-name). */
static bool sdfm_valid_name(const char *name)
{
    if (name == NULL || name[0] == '\0') { return false; }
    size_t L = strnlen(name, SDFM_NAME_MAX + 1);
    if (L > SDFM_NAME_MAX)        { return false; }
    if (strchr(name, '/'))        { return false; }
    if (strchr(name, '\\'))       { return false; }
    if (strstr(name, ".."))       { return false; }
    if (strcmp(name, ".") == 0)   { return false; }
    for (size_t i = 0; i < L; i++)
    {
        unsigned char c = (unsigned char)name[i];
        if (c < 0x20 || c > 0x7E) { return false; }  /* printable ASCII only (FATFS cp437) */
    }
    return true;
}

/* The reserved roots may have their CONTENTS managed, but cannot themselves be removed/renamed. */
static bool sdfm_is_reserved(const char *abspath)
{
    return strcmp(abspath, SDFM_ROOT) == 0 ||
           strcmp(abspath, CSV_LOGGER_DIR) == 0 ||
           strcmp(abspath, SDFM_OBD_DIR) == 0;
}

/* True if abspath IS a protected subtree root or lies anywhere beneath one.
 * These are locked top-to-bottom: the dir and all of its contents are read-only
 * to the file manager (delete/rename forbidden), so housekeeping can never touch
 * the FAT system folder, the device's own data/web assets, or the operational
 * event log (EVENT_LOG_DIR -- self-managed by rotation, never hand-deleted). */
static bool sdfm_is_protected(const char *abspath)
{
    static const char *const roots[] = { SDFM_SVI_DIR, SDFM_WICAN_DIR, EVENT_LOG_DIR };
    for (size_t i = 0; i < sizeof(roots) / sizeof(roots[0]); i++)
    {
        size_t rl = strlen(roots[i]);
        if (strcmp(abspath, roots[i]) == 0)                          { return true; }  /* the dir itself */
        if (strncmp(abspath, roots[i], rl) == 0 && abspath[rl] == '/') { return true; } /* under it */
    }
    return false;
}

/* ---------------- small response helpers ---------------- */

static esp_err_t sdfm_reply(httpd_req_t *req, const char *status, const char *json)
{
    httpd_resp_set_status(req, status);
    httpd_resp_set_type(req, "application/json");
    return httpd_resp_sendstr(req, json);
}

/* ---------------- recursive delete ---------------- */
/*
 * Delete a file, or recursively a directory. Only ONE DIR handle is open at a
 * time (names are gathered then the handle is closed before recursing) to respect
 * the SD VFS max_files=5 ceiling. Aborts (and reports) if it meets the active log.
 */
static bool sdfm_rmtree(char *path, size_t path_sz, int depth, int *deleted, bool *active_hit)
{
    struct stat st;
    if (stat(path, &st) != 0) { return false; }

    if (!S_ISDIR(st.st_mode))
    {
        if (csv_logger_is_active_file(path)) { *active_hit = true; return false; }
        if (unlink(path) != 0) { return false; }
        (*deleted)++;
        return true;
    }

    if (depth > SDFM_RMTREE_DEPTH) { return false; }

    DIR *dir = opendir(path);
    if (dir == NULL) { return false; }

    char **names = NULL;
    int count = 0, cap = 0;
    struct dirent *e;
    bool oom = false;
    while ((e = readdir(dir)) != NULL)
    {
        if (strcmp(e->d_name, ".") == 0 || strcmp(e->d_name, "..") == 0) { continue; }
        if (count == cap)
        {
            int ncap = cap ? cap * 2 : 16;
            char **nn = realloc(names, (size_t)ncap * sizeof(char *));
            if (nn == NULL) { oom = true; break; }
            names = nn;
            cap = ncap;
        }
        names[count] = strdup(e->d_name);
        if (names[count] == NULL) { oom = true; break; }
        count++;
    }
    closedir(dir);   /* close BEFORE recursing -- handle ceiling is 5 */

    bool ok = !oom;
    size_t base = strlen(path);
    for (int i = 0; i < count; i++)
    {
        if (ok && !*active_hit)
        {
            snprintf(path + base, path_sz - base, "/%s", names[i]);
            if (!sdfm_rmtree(path, path_sz, depth + 1, deleted, active_hit)) { ok = false; }
            path[base] = '\0';
        }
        free(names[i]);
    }
    free(names);

    if (ok && !*active_hit)
    {
        if (rmdir(path) != 0) { return false; }
        (*deleted)++;
        return true;
    }
    return false;
}

/* ---------------- GET: list / download / df ---------------- */

static esp_err_t sdfm_list(httpd_req_t *req, const char *abspath)
{
    cJSON *root = cJSON_CreateObject();
    if (root == NULL) { return sdfm_reply(req, "500 Internal Server Error", "{\"error\":\"oom\"}"); }

    /* relative path (strip the /sdcard prefix) for the UI breadcrumb */
    const char *rel = abspath + strlen(SDFM_ROOT);
    if (*rel == '/') { rel++; }
    cJSON_AddStringToObject(root, "path", rel);
    cJSON_AddBoolToObject(root, "sd_mounted", sdcard_is_mounted());
    cJSON *entries = cJSON_AddArrayToObject(root, "entries");

    DIR *dir = opendir(abspath);
    if (dir != NULL)
    {
        struct dirent *e;
        char child[SDFM_PATH_MAX];
        struct stat st;
        int n = 0;
        bool truncated = false;
        while ((e = readdir(dir)) != NULL)
        {
            if (strcmp(e->d_name, ".") == 0 || strcmp(e->d_name, "..") == 0) { continue; }
            if (n >= SDFM_LIST_CAP) { truncated = true; break; }
            if (snprintf(child, sizeof(child), "%s/%s", abspath, e->d_name) >= (int)sizeof(child)) { continue; }
            cJSON *item = cJSON_CreateObject();
            cJSON_AddStringToObject(item, "name", e->d_name);
            /* Tell the UI to hide rename/delete for reserved roots and anything
             * inside a fully protected subtree. (Backend enforces it regardless.) */
            if (sdfm_is_reserved(child) || sdfm_is_protected(child)) { cJSON_AddBoolToObject(item, "locked", true); }
            if (stat(child, &st) == 0)
            {
                bool is_dir = S_ISDIR(st.st_mode);
                cJSON_AddStringToObject(item, "type", is_dir ? "dir" : "file");
                if (!is_dir)
                {
                    cJSON_AddNumberToObject(item, "size", (double)st.st_size);
                    if (csv_logger_is_active_file(child)) { cJSON_AddBoolToObject(item, "active", true); }
                }
                cJSON_AddNumberToObject(item, "mtime", (double)st.st_mtime);
            }
            else
            {
                cJSON_AddStringToObject(item, "type", "file");
            }
            cJSON_AddItemToArray(entries, item);
            n++;
        }
        closedir(dir);
        if (truncated) { cJSON_AddBoolToObject(root, "truncated", true); }
    }
    else
    {
        /* a mounted card but missing dir -> 404; an unmounted card -> empty list */
        if (sdcard_is_mounted())
        {
            cJSON_Delete(root);
            return sdfm_reply(req, "404 Not Found", "{\"error\":\"not found\"}");
        }
    }

    char *out = cJSON_PrintUnformatted(root);
    esp_err_t r = sdfm_reply(req, "200 OK", out ? out : "{\"entries\":[]}");
    if (out) { free(out); }
    cJSON_Delete(root);
    return r;
}

static esp_err_t sdfm_download(httpd_req_t *req, const char *abspath)
{
    struct stat st;
    if (stat(abspath, &st) != 0)   { return sdfm_reply(req, "404 Not Found", "{\"error\":\"not found\"}"); }
    if (S_ISDIR(st.st_mode))       { return sdfm_reply(req, "400 Bad Request", "{\"error\":\"is a directory\"}"); }

    FILE *f = fopen(abspath, "r");
    if (f == NULL)                 { return sdfm_reply(req, "404 Not Found", "{\"error\":\"open failed\"}"); }

    const char *base = strrchr(abspath, '/');
    base = base ? base + 1 : abspath;
    const char *ext = strrchr(base, '.');
    httpd_resp_set_type(req, (ext && strcasecmp(ext, ".csv") == 0) ? "text/csv" : "application/octet-stream");
    char disp[160];
    snprintf(disp, sizeof(disp), "attachment; filename=\"%s\"", base);
    httpd_resp_set_hdr(req, "Content-Disposition", disp);

    /* INTERNAL-only buffer: this server runs alongside SD writers, and a PSRAM
     * buffer could fault during a flash-cache-disable window. The size adapts to
     * what internal RAM can actually give right now -- a fixed large block often
     * fails to allocate alongside WiFi+httpd, which would 500 the whole download. */
    size_t bufsz = SDFM_STREAM_BUF;
    char *buf = NULL;
    while (bufsz >= 1024)
    {
        buf = heap_caps_malloc(bufsz, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
        if (buf != NULL) { break; }
        bufsz /= 2;
    }
    if (buf == NULL)
    {
        fclose(f);
        return sdfm_reply(req, "500 Internal Server Error", "{\"error\":\"oom\"}");
    }

    size_t r;
    esp_err_t rc = ESP_OK;
    while ((r = fread(buf, 1, bufsz, f)) > 0)
    {
        if (httpd_resp_send_chunk(req, buf, r) != ESP_OK) { rc = ESP_FAIL; break; }
    }
    heap_caps_free(buf);
    fclose(f);
    httpd_resp_send_chunk(req, NULL, 0);
    return rc;
}

static esp_err_t sdfm_df(httpd_req_t *req)
{
    uint64_t total = 0, freeb = 0;
    if (sdcard_is_mounted())
    {
        esp_vfs_fat_info(SDFM_ROOT, &total, &freeb);   /* zeros left on failure */
    }
    char json[96];
    snprintf(json, sizeof(json), "{\"total\":%llu,\"free\":%llu}",
             (unsigned long long)total, (unsigned long long)freeb);
    return sdfm_reply(req, "200 OK", json);
}

static esp_err_t sdfm_get_handler(httpd_req_t *req)
{
    char query[640];
    char op[16] = {0};
    char path[400] = {0};

    if (httpd_req_get_url_query_str(req, query, sizeof(query)) == ESP_OK)
    {
        httpd_query_key_value(query, "op", op, sizeof(op));
        httpd_query_key_value(query, "path", path, sizeof(path));
    }
    if (op[0] == '\0') { strlcpy(op, "list", sizeof(op)); }

    if (strcmp(op, "df") == 0) { return sdfm_df(req); }

    sdfm_url_decode(path);
    char abspath[SDFM_PATH_MAX];
    if (!sdfm_resolve(path, abspath, sizeof(abspath)))
    {
        return sdfm_reply(req, "400 Bad Request", "{\"error\":\"invalid path\"}");
    }

    if (strcmp(op, "list") == 0)     { return sdfm_list(req, abspath); }
    if (strcmp(op, "download") == 0) { return sdfm_download(req, abspath); }
    return sdfm_reply(req, "400 Bad Request", "{\"error\":\"unknown op\"}");
}

/* ---------------- POST: mkdir / delete / rename ---------------- */

static esp_err_t sdfm_op_mkdir(httpd_req_t *req, const char *parent_rel, const char *name)
{
    if (!sdfm_valid_name(name)) { return sdfm_reply(req, "400 Bad Request", "{\"error\":\"invalid name\"}"); }
    char parent[SDFM_PATH_MAX];
    if (!sdfm_resolve(parent_rel, parent, sizeof(parent)))
    {
        return sdfm_reply(req, "400 Bad Request", "{\"error\":\"invalid path\"}");
    }
    char target[SDFM_PATH_MAX];
    if (snprintf(target, sizeof(target), "%s/%s", parent, name) >= (int)sizeof(target))
    {
        return sdfm_reply(req, "400 Bad Request", "{\"error\":\"path too long\"}");
    }
    struct stat st;
    if (stat(target, &st) == 0) { return sdfm_reply(req, "409 Conflict", "{\"error\":\"already exists\"}"); }
    if (mkdir(target, 0775) != 0)
    {
        ESP_LOGW(TAG, "mkdir %s failed: %s", target, strerror(errno));
        return sdfm_reply(req, "500 Internal Server Error", "{\"error\":\"mkdir failed\"}");
    }
    /* Guard against silent 8.3 truncation on an LFN-disabled build. */
    if (stat(target, &st) != 0) { return sdfm_reply(req, "500 Internal Server Error", "{\"error\":\"name not created as requested\"}"); }
    return sdfm_reply(req, "200 OK", "{\"ok\":true}");
}

static esp_err_t sdfm_op_delete(httpd_req_t *req, const char *rel)
{
    char abspath[SDFM_PATH_MAX];
    if (!sdfm_resolve(rel, abspath, sizeof(abspath)))
    {
        return sdfm_reply(req, "400 Bad Request", "{\"error\":\"invalid path\"}");
    }
    if (sdfm_is_reserved(abspath))  { return sdfm_reply(req, "403 Forbidden", "{\"error\":\"reserved path\"}"); }
    if (sdfm_is_protected(abspath)) { return sdfm_reply(req, "403 Forbidden", "{\"error\":\"protected path\"}"); }

    struct stat st;
    if (stat(abspath, &st) != 0)   { return sdfm_reply(req, "404 Not Found", "{\"error\":\"not found\"}"); }

    int deleted = 0;
    bool active_hit = false;
    char work[SDFM_PATH_MAX];
    strlcpy(work, abspath, sizeof(work));
    bool ok = sdfm_rmtree(work, sizeof(work), 0, &deleted, &active_hit);
    if (active_hit) { return sdfm_reply(req, "409 Conflict", "{\"error\":\"file is being written\"}"); }
    if (!ok)        { return sdfm_reply(req, "500 Internal Server Error", "{\"error\":\"delete failed\"}"); }

    char json[64];
    snprintf(json, sizeof(json), "{\"ok\":true,\"deleted\":%d}", deleted);
    return sdfm_reply(req, "200 OK", json);
}

static esp_err_t sdfm_op_rename(httpd_req_t *req, const char *rel, const char *name)
{
    if (!sdfm_valid_name(name)) { return sdfm_reply(req, "400 Bad Request", "{\"error\":\"invalid name\"}"); }
    char src[SDFM_PATH_MAX];
    if (!sdfm_resolve(rel, src, sizeof(src)))
    {
        return sdfm_reply(req, "400 Bad Request", "{\"error\":\"invalid path\"}");
    }
    if (sdfm_is_reserved(src))             { return sdfm_reply(req, "403 Forbidden", "{\"error\":\"reserved path\"}"); }
    if (sdfm_is_protected(src))            { return sdfm_reply(req, "403 Forbidden", "{\"error\":\"protected path\"}"); }
    if (csv_logger_is_active_file(src))    { return sdfm_reply(req, "409 Conflict", "{\"error\":\"file is being written\"}"); }

    struct stat st;
    if (stat(src, &st) != 0) { return sdfm_reply(req, "404 Not Found", "{\"error\":\"not found\"}"); }

    /* dest = same parent dir + new name */
    char dest[SDFM_PATH_MAX];
    strlcpy(dest, src, sizeof(dest));
    char *slash = strrchr(dest, '/');
    if (slash == NULL) { return sdfm_reply(req, "400 Bad Request", "{\"error\":\"invalid path\"}"); }
    if (snprintf(slash + 1, sizeof(dest) - (slash + 1 - dest), "%s", name) >= (int)(sizeof(dest) - (slash + 1 - dest)))
    {
        return sdfm_reply(req, "400 Bad Request", "{\"error\":\"path too long\"}");
    }
    if (stat(dest, &st) == 0) { return sdfm_reply(req, "409 Conflict", "{\"error\":\"destination exists\"}"); }
    if (rename(src, dest) != 0)
    {
        ESP_LOGW(TAG, "rename %s -> %s failed: %s", src, dest, strerror(errno));
        return sdfm_reply(req, "500 Internal Server Error", "{\"error\":\"rename failed\"}");
    }
    return sdfm_reply(req, "200 OK", "{\"ok\":true}");
}

static esp_err_t sdfm_post_handler(httpd_req_t *req)
{
    int total = req->content_len;
    if (total <= 0 || total > SDFM_POST_BODY_MAX)
    {
        return sdfm_reply(req, "400 Bad Request", "{\"error\":\"bad body\"}");
    }
    char *body = malloc(total + 1);
    if (body == NULL) { return sdfm_reply(req, "500 Internal Server Error", "{\"error\":\"oom\"}"); }

    int got = 0;
    while (got < total)
    {
        int r = httpd_req_recv(req, body + got, total - got);
        if (r <= 0) { free(body); return sdfm_reply(req, "400 Bad Request", "{\"error\":\"recv failed\"}"); }
        got += r;
    }
    body[total] = '\0';

    cJSON *root = cJSON_Parse(body);
    free(body);
    if (root == NULL) { return sdfm_reply(req, "400 Bad Request", "{\"error\":\"bad json\"}"); }

    cJSON *jop   = cJSON_GetObjectItem(root, "op");
    cJSON *jpath = cJSON_GetObjectItem(root, "path");
    cJSON *jname = cJSON_GetObjectItem(root, "name");
    const char *op   = cJSON_IsString(jop)   ? jop->valuestring   : "";
    const char *path = cJSON_IsString(jpath) ? jpath->valuestring : "";
    const char *name = cJSON_IsString(jname) ? jname->valuestring : "";

    esp_err_t rc;
    if (strcmp(op, "mkdir") == 0)       { rc = sdfm_op_mkdir(req, path, name); }
    else if (strcmp(op, "delete") == 0) { rc = sdfm_op_delete(req, path); }
    else if (strcmp(op, "rename") == 0) { rc = sdfm_op_rename(req, path, name); }
    else                                { rc = sdfm_reply(req, "400 Bad Request", "{\"error\":\"unknown op\"}"); }

    cJSON_Delete(root);
    return rc;
}

const httpd_uri_t sd_files_get_uri = {
    .uri = "/files",
    .method = HTTP_GET,
    .handler = sdfm_get_handler,
    .user_ctx = NULL
};

const httpd_uri_t sd_files_post_uri = {
    .uri = "/files",
    .method = HTTP_POST,
    .handler = sdfm_post_handler,
    .user_ctx = NULL
};
