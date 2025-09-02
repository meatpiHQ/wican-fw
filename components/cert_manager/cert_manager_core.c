/*
 * This file is part of the WiCAN project.
 *
 * Copyright (C) 2022  Meatpi Electronics.
 * Written by Ali Slim <ali@meatpi.com>
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <http://www.gnu.org/licenses/>.
 */

#include "cert_manager_core.h"
#include <sys/stat.h>
#include <dirent.h>
#include <string.h>
#include <stdio.h>
#include <errno.h>
#include <unistd.h>
#include "esp_log.h"
#include "filesystem.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "esp_heap_caps.h"

#define TAG "cert_manager_core"

static cert_set_entry_t g_sets[CERT_MANAGER_MAX_SETS];
static size_t g_set_count = 0;
static SemaphoreHandle_t g_sets_mutex; // protects g_sets[] and g_set_count

#define LOCK()   do { if(g_sets_mutex) xSemaphoreTake(g_sets_mutex, portMAX_DELAY); } while(0)
#define UNLOCK() do { if(g_sets_mutex) xSemaphoreGive(g_sets_mutex); } while(0)

// ---------- helpers ----------
bool cert_manager_core_valid_set_name(const char *name)
{
    if (!name) 
        return false;
    size_t l = strlen(name);
    if (l == 0 || l > 24) 
        return false;
    for (size_t i = 0; i < l; i++)
    {
        char c = name[i];
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' || c == '-')) 
            return false;
    }
    if (strcmp(name, ".") == 0 || strcmp(name, "..") == 0) 
        return false;
    return true;
}

// Safe path building helpers (avoid snprintf to silence format-truncation warnings)
static bool path_join2(char *dst, size_t dst_sz, const char *a, const char *b)
{
    size_t la = strlen(a), lb = strlen(b);
    if(la + 1 + lb + 1 > dst_sz) 
        return false; // a + '/' + b + NUL
    memcpy(dst, a, la);
    dst[la] = '/';
    memcpy(dst+la+1, b, lb);
    dst[la+1+lb] = '\0';
    return true;
}

static bool path_join3(char *dst, size_t dst_sz, const char *a, const char *b, const char *c)
{
    size_t la=strlen(a), lb=strlen(b), lc=strlen(c);
    if(la + 1 + lb + 1 + lc + 1 > dst_sz) 
        return false; // a+'/'+b+'/'+c+NUL
    memcpy(dst,a,la); 
    dst[la]='/';
    memcpy(dst+la+1,b,lb); 
    dst[la+1+lb]='/';
    memcpy(dst+la+1+lb+1,c,lc); 
    dst[la+1+lb+1+lc]='\0';
    return true;
}

void cert_manager_core_build_set_file_path(const char *set, const char *fname, char *out, size_t out_sz)
{
    if(!path_join3(out,out_sz,CERT_MANAGER_DIR,set,fname)) 
    {
        if(out_sz) 
            out[0]='\0';
    }
}

// Recursively delete a directory tree (files + subdirs). Limited depth to prevent mistakes.
static void delete_dir_recursive(const char *dir_path, int depth)
{
    if(depth > 4) 
    { 
        ESP_LOGW(TAG, "delete_dir_recursive: depth limit exceeded at %s", dir_path); 
        return; 
    }
    DIR *d = opendir(dir_path);
    if(!d) 
    {
        ESP_LOGW(TAG, "delete_dir_recursive: opendir failed for %s errno=%d", dir_path, errno);
        return;
    }
    struct dirent *ent;
    char child[256];
    while((ent = readdir(d)) != NULL) 
    {
        const char *name = ent->d_name;
        if(strcmp(name, ".")==0 || strcmp(name,"..")==0) 
            continue;
        if(!path_join2(child, sizeof(child), dir_path, name)) 
            continue;
        struct stat st;
        if(stat(child,&st)!=0) 
        {
            ESP_LOGW(TAG, "stat failed on %s errno=%d", child, errno);
            continue;
        }
        if(S_ISDIR(st.st_mode)) 
        {
            delete_dir_recursive(child, depth+1);
            if(rmdir(child)!=0) 
            {
                ESP_LOGW(TAG, "Failed to rmdir %s errno=%d", child, errno);
            } 
            else 
            {
                ESP_LOGD(TAG, "rmdir %s", child);
            }
        } 
        else 
        {
            if(remove(child)!=0) 
            {
                ESP_LOGW(TAG, "Failed to remove file %s errno=%d", child, errno);
            } 
            else 
            {
                ESP_LOGD(TAG, "removed file %s", child);
            }
        }
    }
    closedir(d);
}

esp_err_t cert_manager_core_ensure_base_dir(void)
{
    struct stat st;
    if(stat(CERT_MANAGER_DIR, &st)==0) 
    {
        if(S_ISDIR(st.st_mode)) 
            return ESP_OK;
        ESP_LOGE(TAG, "%s exists but not a directory", CERT_MANAGER_DIR);
        return ESP_FAIL;
    }
    if(mkdir(CERT_MANAGER_DIR, 0777)==0) 
    {
        ESP_LOGI(TAG, "Created %s", CERT_MANAGER_DIR);
        return ESP_OK;
    }
    ESP_LOGE(TAG, "mkdir(%s) failed errno=%d", CERT_MANAGER_DIR, errno);
    return ESP_FAIL;
}

// Free buffers held by a single entry (caller holds lock)
static void free_entry_buffers(cert_set_entry_t *e)
{
    if(!e) 
        return;
    free(e->ca_data); 
    e->ca_data = NULL; 
    e->ca_len = 0;
    free(e->client_cert_data); 
    e->client_cert_data = NULL; 
    e->client_cert_len = 0;
    free(e->client_key_data); 
    e->client_key_data = NULL; 
    e->client_key_len = 0;
}

static void clear_sets_unlocked(void)
{
    for(size_t i=0;i<g_set_count;i++) 
    {
        free_entry_buffers(&g_sets[i]);
    }
    g_set_count = 0;
    memset(g_sets, 0, sizeof(g_sets));
}

// Rescan directory and update entries while preserving already loaded buffers.
void cert_manager_core_scan_sets_unlocked(void)
{
    // Snapshot existing entries so we can reuse their loaded buffers.
    cert_set_entry_t old[CERT_MANAGER_MAX_SETS];
    size_t old_count = g_set_count;
    memcpy(old, g_sets, sizeof(old));
    bool reused[CERT_MANAGER_MAX_SETS];
    for(size_t i=0;i<old_count;i++) 
        reused[i] = false;

    // Reset current table (but don't free old buffers yet; we may reuse).
    g_set_count = 0;
    memset(g_sets, 0, sizeof(g_sets));

    DIR *d = opendir(CERT_MANAGER_DIR);
    if(!d) 
    { 
        ESP_LOGW(TAG, "opendir(%s) failed errno=%d", CERT_MANAGER_DIR, errno); 
        goto cleanup; 
    }
    struct dirent *ent;
    while((ent = readdir(d)) != NULL && g_set_count < CERT_MANAGER_MAX_SETS) 
    {
        if(ent->d_type != DT_DIR && ent->d_type != DT_UNKNOWN) 
            continue; // only directories
        const char *name = ent->d_name;
        if(name[0] == '.') 
            continue; // skip . and hidden
        if(!cert_manager_core_valid_set_name(name)) 
            continue;

        // Find matching old entry to preserve loaded buffers
        cert_set_entry_t *e = &g_sets[g_set_count];
        size_t match_idx = (size_t)-1;
        for(size_t i=0;i<old_count;i++) 
        {
            if(!reused[i] && strcmp(old[i].name, name)==0) 
            { 
                match_idx = i; 
                break; 
            }
        }
        if(match_idx != (size_t)-1) 
        {
            *e = old[match_idx]; // copy entire struct including pointers
            reused[match_idx] = true;
        } 
        else 
        {
            memset(e,0,sizeof(*e));
            strncpy(e->name, name, sizeof(e->name)-1);
        }

        // Refresh file presence flags (does not clear existing loaded buffers)
        char path[160]; 
        struct stat st;
        cert_manager_core_build_set_file_path(name, "ca.pem", path, sizeof(path));
        e->has_ca = (stat(path,&st)==0 && S_ISREG(st.st_mode));
        cert_manager_core_build_set_file_path(name, "client.crt", path, sizeof(path));
        e->has_client_cert = (stat(path,&st)==0 && S_ISREG(st.st_mode));
        cert_manager_core_build_set_file_path(name, "client.key", path, sizeof(path));
        e->has_client_key = (stat(path,&st)==0 && S_ISREG(st.st_mode));
        g_set_count++;
    }
    if(d) 
        closedir(d);

cleanup:
    // Free buffers belonging to sets that were not reused (deleted from filesystem)
    for(size_t i=0;i<old_count;i++) 
    {
        if(!reused[i]) 
        {
            free_entry_buffers(&old[i]);
        }
    }
}

cert_set_entry_t *cert_manager_core_find_set_unlocked(const char *name)
{
    for(size_t i=0;i<g_set_count;i++) 
    {
        if(strcmp(g_sets[i].name,name)==0) 
            return &g_sets[i];
    }
    return NULL;
}

const cert_set_entry_t *cert_manager_core_find_set_locked(const char *name)
{
    for(size_t i=0;i<g_set_count;i++) 
    {
        if(strcmp(g_sets[i].name,name)==0) 
            return &g_sets[i];
    }
    return NULL;
}

// helper to load file fully into RAM
esp_err_t cert_manager_core_load_file_alloc_simple(const char *path, char **buf_out, size_t *len_out)
{
    *buf_out = NULL;
    *len_out = 0;
    struct stat st;
    if(stat(path, &st) != 0) 
    {
        return ESP_ERR_NOT_FOUND;
    }
    FILE *f = fopen(path, "rb");
    if(!f) 
    {
        return ESP_FAIL;
    }
    size_t need = st.st_size + 1;
    char *b = (char*)heap_caps_malloc(need, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    bool used_psram = true;
    if(!b) 
    { // fallback internal RAM
        used_psram = false;
        b = (char*)heap_caps_malloc(need, MALLOC_CAP_8BIT);
        ESP_LOGW(TAG, "load_file_alloc_simple: using internal RAM");
    }
    if(!b) 
    { // final fallback
        used_psram = false;
        b = (char*)malloc(need);
        ESP_LOGW(TAG, "load_file_alloc_simple: using fallback malloc");
    }
    if(!b) 
    {
        fclose(f);
        return ESP_ERR_NO_MEM;
    }
    memset(b, 0, need);
    size_t r = fread(b, 1, st.st_size, f);
    fclose(f);
    if(r != (size_t)st.st_size) 
    {
        free(b);
        return ESP_FAIL;
    }
    b[r] = '\0';
    *buf_out = b;
    *len_out = r+1;
    ESP_EARLY_LOGD(TAG, "Loaded %s (%u bytes) into %s", path, (unsigned)r, used_psram?"PSRAM":"INTRAM");
    return ESP_OK;
}

esp_err_t cert_manager_core_load_set_component(const char *set, const char *fname, char **buf_out, size_t *len_out)
{
    if(!cert_manager_core_valid_set_name(set)) 
    {
        return ESP_ERR_INVALID_ARG;
    }
    char path[192];
    cert_manager_core_build_set_file_path(set, fname, path, sizeof(path));
    return cert_manager_core_load_file_alloc_simple(path, buf_out, len_out);
}

const char *cert_manager_core_get_cached_ptr(const char *set_name, size_t *len_out, char *which)
{
    if(len_out) 
        *len_out = 0;
    if(!cert_manager_core_valid_set_name(set_name)) 
        return NULL;
    LOCK();
    const cert_set_entry_t *e = cert_manager_core_find_set_locked(set_name);
    const char *ptr = NULL; 
    size_t len=0;
    if(e) 
    {
        if(which[0]=='c' && which[1]=='a') 
        { 
            ptr = e->ca_data; 
            len = e->ca_len; 
        }
        else if(which[0]=='c' && which[1]=='e') 
        { 
            ptr = e->client_cert_data; 
            len = e->client_cert_len; 
        }
        else if(which[0]=='c' && which[1]=='k') 
        { 
            ptr = e->client_key_data; 
            len = e->client_key_len; 
        }
    }
    UNLOCK();
    if(len_out) 
        *len_out = len;
    return ptr;
}

esp_err_t cert_manager_core_preload_sets(const char *const *set_names, size_t count)
{
    // Preload specific sets (or all if set_names==NULL) into RAM cache
    if(!set_names) 
    {
        // Preload all currently discovered sets
        LOCK();
        for(size_t i=0;i<g_set_count;i++) 
        {
            cert_set_entry_t *e = &g_sets[i];
            if(e->has_ca && !e->ca_data) 
                cert_manager_core_load_set_component(e->name,"ca.pem", &e->ca_data,&e->ca_len);
            if(e->has_client_cert && !e->client_cert_data) 
                cert_manager_core_load_set_component(e->name,"client.crt", &e->client_cert_data,&e->client_cert_len);
            if(e->has_client_key && !e->client_key_data) 
                cert_manager_core_load_set_component(e->name,"client.key", &e->client_key_data,&e->client_key_len);
        }
        UNLOCK();
        return ESP_OK;
    }
    for(size_t i = 0; i < count; i++) 
    {
        if(!set_names[i]) 
        {
            continue;
        }
        char path[192];
        cert_manager_core_build_set_file_path(set_names[i], "ca.pem", path, sizeof(path));
        struct stat st;
        if(stat(path, &st) != 0) 
        {
            ESP_LOGW(TAG, "Preload: ca.pem missing for %s", set_names[i]);
        }
        // Load components present
        LOCK();
        cert_set_entry_t *e = cert_manager_core_find_set_unlocked(set_names[i]);
        if(e) 
        {
            if(e->has_ca && !e->ca_data) 
                cert_manager_core_load_set_component(e->name,"ca.pem", &e->ca_data,&e->ca_len);
            if(e->has_client_cert && !e->client_cert_data) 
                cert_manager_core_load_set_component(e->name,"client.crt", &e->client_cert_data,&e->client_cert_len);
            if(e->has_client_key && !e->client_key_data) 
                cert_manager_core_load_set_component(e->name,"client.key", &e->client_key_data,&e->client_key_len);
        }
        UNLOCK();
    }
    return ESP_OK;
}

esp_err_t cert_manager_core_delete_set(const char *name)
{
    if(!cert_manager_core_valid_set_name(name)) 
    {
        return ESP_ERR_INVALID_ARG;
    }

    LOCK();
    cert_set_entry_t *e = cert_manager_core_find_set_unlocked(name);
    UNLOCK();
    if(!e) 
    {
        ESP_LOGW(TAG, "Delete: set '%s' not found", name);
        return ESP_ERR_NOT_FOUND;
    }

    ESP_LOGI(TAG, "Deleting cert set '%s'", name);
    char dir[160];
    if(!path_join2(dir,sizeof(dir),CERT_MANAGER_DIR,name)) 
        return ESP_ERR_INVALID_SIZE;

    char path[192];
    const char *files[] = {"ca.pem","client.crt","client.key"};
    bool removed_all_known = true;
    for(size_t i = 0; i < 3; i++) 
    {
        cert_manager_core_build_set_file_path(name, files[i], path, sizeof(path));
        if(remove(path)==0) 
        {
            ESP_LOGD(TAG, "Deleted file %s", path);
        } 
        else if(errno != ENOENT) 
        {
            ESP_LOGW(TAG, "Could not delete file %s errno=%d", path, errno);
            removed_all_known = false;
        }
    }
    // Attempt recursive cleanup if directory not empty
    if(!removed_all_known) 
    {
        ESP_LOGD(TAG, "Performing recursive cleanup for %s", dir);
    }
    delete_dir_recursive(dir, 0);
    if(rmdir(dir)==0) 
    {
        ESP_LOGD(TAG, "Removed directory %s", dir);
    } 
    else 
    {
        // One more attempt after recursive (maybe hidden files remained)
        delete_dir_recursive(dir, 0);
        if(rmdir(dir)!=0) 
        {
            ESP_LOGW(TAG, "Final rmdir failed for %s errno=%d", dir, errno);
        } 
        else 
        {
            ESP_LOGD(TAG, "Removed directory %s on second attempt", dir);
        }
    }
    LOCK();
    // Remove from in-memory list (but preserve cached data until reboot)
    cert_set_entry_t *victim = cert_manager_core_find_set_unlocked(name);
    if(victim) 
    {
        // Just mark the set as not having files (so it won't show in lists)
        // but keep cached data intact until reboot
        victim->has_ca = false;
        victim->has_client_cert = false;
        victim->has_client_key = false;
        // Don't free cached buffers - they stay until reboot
    }
    UNLOCK();
    return ESP_OK;
}

// Detailed report of all certificate sets and their files (logs only)
esp_err_t cert_manager_core_log_report(void)
{
    ESP_LOGI(TAG, "--- Certificate Sets Report Begin ---");
    if(cert_manager_core_ensure_base_dir()!=ESP_OK) 
    {
        ESP_LOGW(TAG, "Base directory not available; report aborted");
        return ESP_FAIL;
    }
    DIR *root = opendir(CERT_MANAGER_DIR);
    if(!root) 
    {
        ESP_LOGW(TAG, "opendir(%s) failed errno=%d", CERT_MANAGER_DIR, errno);
        return ESP_FAIL;
    }
    size_t set_count = 0;
    size_t total_files = 0;
    size_t total_bytes = 0;
    struct dirent *ent;
    while((ent = readdir(root)) != NULL) 
    {
        const char *dname = ent->d_name;
        if(dname[0]=='.') 
            continue; // skip . and hidden
        if(!cert_manager_core_valid_set_name(dname)) 
            continue;
        char set_dir[512];
        const char *base = CERT_MANAGER_DIR;
        size_t base_len = strlen(base);
        size_t name_len = strlen(dname);
        if(base_len + 1 + name_len >= sizeof(set_dir)) 
        {
            ESP_LOGW(TAG, "Set name too long; skipping '%.*s'", 32, dname);
            continue;
        }
        memcpy(set_dir, base, base_len);
        set_dir[base_len] = '/';
        memcpy(set_dir + base_len + 1, dname, name_len + 1); // include NUL
        struct stat st_dir;
        if(stat(set_dir, &st_dir)!=0 || !S_ISDIR(st_dir.st_mode)) 
            continue;
        set_count++;
        // Log only first 24 chars of name to keep message bounded
        char name_preview[25];
        strncpy(name_preview, dname, 24); 
        name_preview[24]='\0';
        ESP_LOGI(TAG, "Set: name_len=%u name=%.24s", (unsigned)name_len, name_preview);
        // Track expected files
        const char *expected[] = {"ca.pem","client.crt","client.key"};
        bool seen_expected[3] = {false,false,false};
        DIR *sd = opendir(set_dir);
        if(!sd) 
        {
            ESP_LOGW(TAG, "  Cannot open set dir errno=%d", errno);
            continue;
        }
        struct dirent *fe;
        while((fe = readdir(sd)) != NULL) 
        {
            const char *fname = fe->d_name;
            if(strcmp(fname, ".")==0 || strcmp(fname, "..")==0) 
                continue;
            char full[768];
            size_t dir_len = strlen(set_dir);
            size_t fn_len = strlen(fname);
            if(dir_len + 1 + fn_len >= sizeof(full)) 
            {
                ESP_LOGW(TAG, "  Path too long; skipping file %.40s/%.24s", set_dir, fname);
                continue;
            }
            memcpy(full, set_dir, dir_len);
            full[dir_len] = '/';
            memcpy(full + dir_len + 1, fname, fn_len + 1);
            struct stat fst;
            if(stat(full,&fst)!=0) 
            {
                ESP_LOGW(TAG, "  stat failed %.60s errno=%d", full, errno);
                continue;
            }
            if(S_ISDIR(fst.st_mode)) 
            {
                ESP_LOGW(TAG, "  Unexpected subdir %.60s", full);
                continue;
            }
            size_t fsz = (size_t)fst.st_size;
            total_files++;
            total_bytes += fsz;
            for(size_t i=0;i<3;i++) 
            {
                if(strcmp(fname, expected[i])==0) 
                    seen_expected[i] = true;
            }
            char file_preview[65];
            strncpy(file_preview, full, 64); 
            file_preview[64]='\0';
            ESP_LOGI(TAG, "  File: %.60s size=%u bytes", file_preview, (unsigned)fsz);
        }
        closedir(sd);
        for(size_t i=0;i<3;i++) 
        {
            if(!seen_expected[i]) 
            {
                ESP_LOGW(TAG, "  Missing expected file: %.24s/%s", dname, expected[i]);
            }
        }
    }
    closedir(root);
    ESP_LOGI(TAG, "--- Certificate Sets Report Summary: sets=%u files=%u total_bytes=%u ---", (unsigned)set_count, (unsigned)total_files, (unsigned)total_bytes);
    ESP_LOGI(TAG, "--- Certificate Sets Report End ---");
    return ESP_OK;
}

// Log details of a single set from cache (no filesystem access)
esp_err_t cert_manager_core_log_set(const char *set_name)
{
    if(!cert_manager_core_valid_set_name(set_name)) 
        return ESP_ERR_INVALID_ARG;
    size_t ca_len=0, cc_len=0, ck_len=0;
    const char *ca = cert_manager_core_get_cached_ptr(set_name,&ca_len,"ca");
    const char *cc = cert_manager_core_get_cached_ptr(set_name,&cc_len,"ce");
    const char *ck = cert_manager_core_get_cached_ptr(set_name,&ck_len,"ck");
    if(!ca && !cc && !ck) 
    {
        ESP_LOGW(TAG, "Set '%s' has no cached components", set_name);
        return ESP_ERR_NOT_FOUND;
    }
    ESP_LOGI(TAG, "Set '%s' cache status:", set_name);
    ESP_LOG_BUFFER_HEXDUMP(TAG, ca, ca_len, ESP_LOG_WARN);
    if(ca) 
        ESP_LOGI(TAG, "  ca.pem: %u bytes ptr=%p, \n file::\n %s", (unsigned)ca_len, (void*)ca, ca); 
    else 
        ESP_LOGI(TAG, "  ca.pem: (absent)");
    if(cc) 
        ESP_LOGI(TAG, "  client.crt: %u bytes ptr=%p, \n file::\n %s", (unsigned)cc_len, (void*)cc, cc); 
    else 
        ESP_LOGI(TAG, "  client.crt: (absent)");
    if(ck) 
        ESP_LOGI(TAG, "  client.key: %u bytes ptr=%p, \n file::\n %s", (unsigned)ck_len, (void*)ck, ck); 
    else 
        ESP_LOGI(TAG, "  client.key: (absent)");
    return ESP_OK;
}

esp_err_t cert_manager_core_init(void)
{
    struct stat st;
    if(stat(FS_MOUNT_POINT,&st)!=0) 
    { 
        ESP_LOGE(TAG,"Mount point %s not accessible", FS_MOUNT_POINT); 
        return ESP_FAIL; 
    }
    if(cert_manager_core_ensure_base_dir()!=ESP_OK) 
    {
        return ESP_FAIL;
    }
    if(!g_sets_mutex) 
    { 
        g_sets_mutex = xSemaphoreCreateMutex(); 
    }
    LOCK(); 
    cert_manager_core_scan_sets_unlocked(); 
    UNLOCK();
    ESP_LOGI(TAG,"Found %u cert sets", (unsigned)g_set_count);
    size_t ps_before = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    // Preload all sets into memory (PSRAM preferred)
    cert_manager_core_preload_sets(NULL, 0);
    size_t ps_after = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    if(ps_before && ps_after) 
    {
        ESP_LOGI(TAG, "PSRAM usage by cert preload: before=%u after=%u used=%u bytes", (unsigned)ps_before, (unsigned)ps_after, (unsigned)(ps_before-ps_after));
    }
    cert_manager_core_log_report();
    return ESP_OK;
}

void cert_manager_core_lock(void)
{
    LOCK();
}

void cert_manager_core_unlock(void)
{
    UNLOCK();
}

size_t cert_manager_core_get_set_count(void)
{
    return g_set_count;
}

const cert_set_entry_t* cert_manager_core_get_sets(void)
{
    return g_sets;
}