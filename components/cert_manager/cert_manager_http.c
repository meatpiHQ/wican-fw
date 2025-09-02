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

#include "cert_manager_http.h"
#include "cert_manager_core.h"
#include <sys/stat.h>
#include <string.h>
#include <stdio.h>
#include <errno.h>
#include "esp_log.h"
#include "cJSON.h"
#include "multipart_upload.h"

#define TAG "cert_manager_http"

typedef struct
{
    char set_name[25];
    FILE *current;
    char current_field[16];
    esp_err_t last_err;
    bool any_written;
} set_upload_ctx_t;

// ---------- HTTP handlers ----------
static esp_err_t http_send_sets_list(httpd_req_t *req)
{
    cert_manager_core_lock();
    cJSON *arr = cJSON_CreateArray();
    size_t set_count = cert_manager_core_get_set_count();
    const cert_set_entry_t *sets = cert_manager_core_get_sets();
    for(size_t i=0;i<set_count;i++) 
    {
        const cert_set_entry_t *e = &sets[i];
        // If a set was deleted, cert_manager_core_delete_set() clears all file presence flags
        // but keeps cached buffers. We exclude such entries from the public list so that
        // /cert_manager/sets does not return deleted sets while active connections may still
        // reference their cached data until reboot.
        if(!e->has_ca && !e->has_client_cert && !e->has_client_key) 
            continue;
        cJSON *o = cJSON_CreateObject();
        cJSON_AddStringToObject(o, "name", e->name);
        cJSON_AddBoolToObject(o, "has_ca", e->has_ca);
        cJSON_AddBoolToObject(o, "has_client_cert", e->has_client_cert);
        cJSON_AddBoolToObject(o, "has_client_key", e->has_client_key);
        cJSON_AddItemToArray(arr,o);
    }
    cert_manager_core_unlock();
    char *json = cJSON_PrintUnformatted(arr);
    cJSON_Delete(arr);
    if(!json) 
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "oom");
    httpd_resp_set_type(req, "application/json");
    httpd_resp_send(req, json, HTTPD_RESP_USE_STRLEN);
    free(json);
    return ESP_OK;
}

static bool set_upload_on_part_begin(const multipart_part_info_t *info, void *user_ctx)
{
    set_upload_ctx_t *ctx = (set_upload_ctx_t*)user_ctx;
    ctx->current = NULL;
    ctx->current_field[0] = '\0';
    if(ctx->last_err != ESP_OK) 
        return false;
    const char *fname = NULL;
    if(strcmp(info->name,"ca_cert")==0) 
    { 
        fname = "ca.pem"; 
        strcpy(ctx->current_field, "ca_cert"); 
    }
    else if(strcmp(info->name,"client_cert")==0) 
    { 
        fname = "client.crt"; 
        strcpy(ctx->current_field, "client_cert"); 
    }
    else if(strcmp(info->name,"client_key")==0) 
    { 
        fname = "client.key"; 
        strcpy(ctx->current_field, "client_key"); 
    }
    else 
    { 
        ESP_LOGW(TAG, "Ignoring unknown field %s", info->name); 
        return false; 
    }
    char path[192];
    cert_manager_core_build_set_file_path(ctx->set_name, fname, path, sizeof(path));
    ctx->current = fopen(path, "wb");
    if(!ctx->current) 
    { 
        ESP_LOGE(TAG,"fopen %s failed errno=%d", path, errno); 
        ctx->last_err = ESP_FAIL; 
        return false; 
    }
    chmod(path,0600);
    return true;
}

static esp_err_t set_upload_on_part_data(const char *data, size_t len, void *user_ctx)
{
    set_upload_ctx_t *ctx = (set_upload_ctx_t*)user_ctx;
    if(!ctx->current) 
        return ESP_OK;
    if(fwrite(data,1,len,ctx->current)!=len) 
    { 
        ctx->last_err=ESP_FAIL; 
        return ESP_FAIL; 
    }
    return ESP_OK;
}

static void set_upload_on_part_end(void *user_ctx)
{
    set_upload_ctx_t *ctx = (set_upload_ctx_t*)user_ctx;
    if(ctx->current) 
    { 
        fclose(ctx->current); 
        ctx->current=NULL; 
        ctx->any_written=true; 
    }
}

static void set_upload_on_finished(void *user_ctx)
{
    set_upload_ctx_t *ctx = (set_upload_ctx_t*)user_ctx;
    if(ctx->current)
    { 
        fclose(ctx->current); 
        ctx->current=NULL; 
    }
}

// Safe path building helpers
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

static esp_err_t http_handle_set_upload(httpd_req_t *req)
{
    // parse ?name=
    char name[25]={0};
    const char *q = strchr(req->uri,'?');
    if(q) 
    {
        const char *p = strstr(q+1,"name=");
        if(p) 
        { 
            p += 5; 
            size_t l=0; 
            while(p[l] && p[l] != '&' && l < sizeof(name)-1) 
            { 
                name[l]=p[l]; 
                l++; 
            } 
            name[l]='\0'; 
        }
    }
    if(!cert_manager_core_valid_set_name(name)) 
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "bad name");
    // ensure dir
    char dir[160];
    if(!path_join2(dir,sizeof(dir),CERT_MANAGER_DIR,name)) 
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "path too long");
    struct stat st;
    if(stat(dir,&st)!=0) 
    {
        if(mkdir(dir,0777)!=0) 
        { 
            ESP_LOGE(TAG,"mkdir %s failed errno=%d", dir, errno); 
            return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "mkdir"); 
        }
    } 
    else if(!S_ISDIR(st.st_mode)) 
    {
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "path not dir");
    }
    set_upload_ctx_t ctx={0};
    strncpy(ctx.set_name, name, sizeof(ctx.set_name)-1);
    multipart_upload_handlers_t h = { .on_part_begin=set_upload_on_part_begin, .on_part_data=set_upload_on_part_data, .on_part_end=set_upload_on_part_end, .on_finished=set_upload_on_finished };
    multipart_upload_config_t cfg = multipart_upload_default_config();
    cfg.rx_buf_size = 2048;
    esp_err_t err = multipart_upload_handle(req, &h, &ctx, &cfg);
    if(err!=ESP_OK || ctx.last_err!=ESP_OK || !ctx.any_written) 
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "upload");
    // refresh in-memory list so new certs show up (but don't load into cache)
    cert_manager_core_lock();
    cert_manager_core_scan_sets_unlocked();
    // scan_sets_unlocked already updated the list with correct file presence flags
    cert_manager_core_unlock();
    httpd_resp_set_type(req,"application/json");
    httpd_resp_sendstr(req, "{\"ok\":true}");
    return ESP_OK;
}

static esp_err_t http_handle_set_delete(httpd_req_t *req)
{
    const char *prefix = "/cert_manager/sets/";
    size_t plen = strlen(prefix);
    ESP_LOGD(TAG, "Delete request URI=%s", req->uri);
    if(strncmp(req->uri, prefix, plen) != 0) 
    {
        ESP_LOGW(TAG, "Delete: bad URI %s", req->uri);
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "bad uri");
    }
    const char *name = req->uri + plen;
    if(!cert_manager_core_valid_set_name(name)) 
    {
        ESP_LOGW(TAG, "Delete: invalid set name '%s'", name);
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "bad name");
    }
    
    esp_err_t err = cert_manager_core_delete_set(name);
    if(err == ESP_ERR_NOT_FOUND)
    {
        return httpd_resp_send_err(req, HTTPD_404_NOT_FOUND, "nf");
    }
    else if(err != ESP_OK)
    {
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "delete failed");
    }
    
    cert_manager_core_lock();
    size_t remaining = cert_manager_core_get_set_count();
    cert_manager_core_unlock();
    ESP_LOGI(TAG, "Delete complete for '%s'. Remaining sets=%u", name, (unsigned)remaining);
    httpd_resp_set_type(req, "application/json");
    httpd_resp_sendstr(req, "{\"ok\":true}");
    return ESP_OK;
}

// Unified router
static esp_err_t router_handler(httpd_req_t *req)
{
    const char *uri = req->uri; // prefix /cert_manager
    const char *p = uri + strlen("/cert_manager");
    if(*p=='/') 
        p++;
    if(*p=='\0') 
        return httpd_resp_send_err(req, HTTPD_404_NOT_FOUND, "missing segment");
    if(strncmp(p,"sets",4)==0) 
    {
        const char *rest = p+4;
        if(*rest=='\0' || *rest=='?') 
        {
            if(req->method==HTTP_GET) 
                return http_send_sets_list(req);
            if(req->method==HTTP_POST) 
                return http_handle_set_upload(req);
        } 
        else if(*rest=='/') 
        {
            if(req->method==HTTP_DELETE) 
                return http_handle_set_delete(req);
        }
    }
    return httpd_resp_send_err(req, HTTPD_404_NOT_FOUND, "route");
}

esp_err_t cert_manager_http_register_handlers(httpd_handle_t server)
{
    static const httpd_uri_t get_u   = { .uri="/cert_manager*", .method=HTTP_GET,    .handler=router_handler };
    static const httpd_uri_t post_u  = { .uri="/cert_manager*", .method=HTTP_POST,   .handler=router_handler };
    static const httpd_uri_t del_u   = { .uri="/cert_manager*", .method=HTTP_DELETE, .handler=router_handler };
    const httpd_uri_t *arr[] = { &get_u, &post_u, &del_u };
    for(size_t i=0;i<3;i++) 
    { 
        esp_err_t r = httpd_register_uri_handler(server, arr[i]); 
        if(r!=ESP_OK && r!=ESP_ERR_HTTPD_HANDLER_EXISTS) 
            return r; 
    }
    ESP_LOGI(TAG,"Handlers registered at /cert_manager*");
    return ESP_OK;
}