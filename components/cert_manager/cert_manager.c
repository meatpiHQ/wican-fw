#include "cert_manager.h"
#include <sys/stat.h>
#include <string.h>
#include <stdio.h>
#include <errno.h>
#include "esp_log.h"
#include "cJSON.h"
#include "esp_vfs.h"
#include "multipart_upload.h"
#include "filesystem.h"
#include "esp_heap_caps.h"

#define TAG "cert_manager"

#define HTTPS_CERT_DIR        FS_MOUNT_POINT"/certs"
#define HTTPS_CA_CERT_PATH    HTTPS_CERT_DIR"/ca.pem"
#define HTTPS_CLIENT_CERT_PATH HTTPS_CERT_DIR"/client.crt"
#define HTTPS_CLIENT_KEY_PATH  HTTPS_CERT_DIR"/client.key"

// Multi-set storage: /certs/<name>/ca.pem, client.crt, client.key plus manifest file
#define CERT_MANIFEST_PATH      HTTPS_CERT_DIR"/manifest.json"

typedef struct {
    char name[25];
    bool has_ca;
    bool has_client_cert;
    bool has_client_key;
} cert_set_entry_t;

static cert_set_entry_t g_sets[CERT_MANAGER_MAX_SETS];
static size_t g_set_count = 0;
static bool g_flat_mode = false; // fallback when FS doesn't support subdirectories (e.g., SPIFFS)

// Rate limiting for uploads
#define UPLOAD_RATE_LIMIT_MS 5000  // Minimum 5 seconds between uploads
static uint32_t last_upload_time = 0;

static bool valid_set_name(const char *name){
    size_t l = strlen(name);
    if (l == 0 || l > 24) {
        return false;
    }
    
    // Check for path traversal sequences
    if (strstr(name, "..") != NULL || strstr(name, "/") != NULL || strstr(name, "\\") != NULL) {
        return false;
    }
    
    // Check for reserved names
    if (strcmp(name, ".") == 0 || strcmp(name, "..") == 0 || 
        strcmp(name, "CON") == 0 || strcmp(name, "PRN") == 0 ||
        strcmp(name, "AUX") == 0 || strcmp(name, "NUL") == 0) {
        return false;
    }
    
    for(size_t i=0;i<l;i++){
        char c = name[i];
        if(!((c>='a'&&c<='z') || (c>='A'&&c<='Z') || (c>='0'&&c<='9') || c=='_' || c=='-')) {
            return false;
        }
    }
    return true;
}

static char * build_set_path(const char *set, const char *fname, char *out, size_t out_sz){
    if (g_flat_mode) {
        // Some filesystems (SPIFFS, FAT without LFN) are picky about directories or long names.
        // We create short 8.3 friendly names: <trunc(set)>_<tt>.<ext>
        // token & ext mapping based on original fname provided (ca.pem, client.crt, client.key)
        const char *token = "uk"; // unknown
        const char *ext = "dat";
        if(strcmp(fname, "ca.pem")==0){ token = "ca"; ext = "pem"; }
        else if(strcmp(fname, "client.crt")==0){ token = "cc"; ext = "crt"; }
        else if(strcmp(fname, "client.key")==0){ token = "ck"; ext = "key"; }
        char base[16];
        // base desired: <set>_<token>
        int set_len = (int)strlen(set);
        int token_len = (int)strlen(token);
        int max_base_len = 8; // 8.3 base limit safeguard
        // Need space for '_' + token
        int max_set_part = max_base_len - 1 - token_len;
        if(max_set_part < 1) max_set_part = 1;
        if(set_len > max_set_part) set_len = max_set_part;
        snprintf(base, sizeof(base), "%.*s_%s", set_len, set, token);
        snprintf(out, out_sz, "%s/%s.%s", HTTPS_CERT_DIR, base, ext);
        ESP_LOGD(TAG, "Flat path map: set=%s fname=%s -> %s", set, fname, out);
    } else {
        snprintf(out,out_sz, "%s/%s/%s", HTTPS_CERT_DIR, set, fname);
    }
    return out;
}

static void save_manifest(void){
    cJSON *root = cJSON_CreateArray();
    for(size_t i=0;i<g_set_count;i++){
        cJSON *o = cJSON_CreateObject();
        cJSON_AddStringToObject(o, "name", g_sets[i].name);
        cJSON_AddBoolToObject(o, "has_ca", g_sets[i].has_ca);
        cJSON_AddBoolToObject(o, "has_client_cert", g_sets[i].has_client_cert);
        cJSON_AddBoolToObject(o, "has_client_key", g_sets[i].has_client_key);
        cJSON_AddItemToArray(root, o);
    }
    char *json = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (json) {
        FILE *f = fopen(CERT_MANIFEST_PATH, "wb");
        if (f) {
            fwrite(json,1,strlen(json),f);
            fclose(f);
        }
        free(json);
    }
}

static void *cm_alloc_psram(size_t sz){
    void *p = heap_caps_malloc(sz, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if(!p){
        p = malloc(sz); // fallback to internal RAM
    }
    return p;
}

static void load_manifest(void)
{
    g_set_count = 0;
    struct stat st;
    if(stat(CERT_MANIFEST_PATH,&st)!=0){
        ESP_LOGI(TAG, "No manifest file yet (%s)", CERT_MANIFEST_PATH);
        return;
    }
    if(st.st_size <= 0){
        ESP_LOGW(TAG, "Manifest file empty");
        return;
    }
    size_t max_read = st.st_size;
    if(max_read > 16*1024){ // hard safety cap
        ESP_LOGW(TAG, "Manifest size %u >16KB, truncating", (unsigned)max_read);
        max_read = 16*1024;
    }
    FILE *f = fopen(CERT_MANIFEST_PATH, "rb");
    if(!f){
        ESP_LOGE(TAG, "Failed to open manifest (errno=%d)", errno);
        return;
    }
    char *buf = (char*)cm_alloc_psram(max_read + 1);
    if(!buf){
        ESP_LOGE(TAG, "OOM allocating %u bytes for manifest", (unsigned)max_read+1);
        fclose(f);
        return;
    }
    size_t r = fread(buf,1,max_read,f);
    fclose(f);
    buf[r] = '\0';
    ESP_LOGI(TAG, "Manifest read %u bytes (file=%u)", (unsigned)r, (unsigned)st.st_size);
    cJSON *root = cJSON_Parse(buf);
    if(!root){
        ESP_LOGE(TAG, "Failed to parse manifest JSON");
        free(buf);
        return;
    }
    if(!cJSON_IsArray(root)){
        ESP_LOGE(TAG, "Manifest root not array");
        cJSON_Delete(root);
        free(buf);
        return;
    }
    int arr_sz = cJSON_GetArraySize(root);
    ESP_LOGI(TAG, "Manifest contains %d entries", arr_sz);
    for(int i=0;i<arr_sz && g_set_count < CERT_MANAGER_MAX_SETS;i++){
        cJSON *it = cJSON_GetArrayItem(root,i);
        if(!it) continue;
        cJSON *n = cJSON_GetObjectItem(it,"name");
        if(!cJSON_IsString(n)) continue;
        cert_set_entry_t *e = &g_sets[g_set_count];
        memset(e,0,sizeof(*e));
        strncpy(e->name,n->valuestring,sizeof(e->name)-1);
        e->has_ca = cJSON_IsTrue(cJSON_GetObjectItem(it,"has_ca"));
        e->has_client_cert = cJSON_IsTrue(cJSON_GetObjectItem(it,"has_client_cert"));
        e->has_client_key = cJSON_IsTrue(cJSON_GetObjectItem(it,"has_client_key"));
        g_set_count++;
    }
    ESP_LOGI(TAG, "Loaded %u manifest entries", (unsigned)g_set_count);
    cJSON_Delete(root);
    free(buf);
}

static cert_set_entry_t * find_set(const char *name)
{
    size_t i;
    for(i = 0; i < g_set_count; i++)
    {
        if (strcmp(g_sets[i].name, name) == 0)
        {
            return &g_sets[i];
        }
    }
    return NULL;
}

static esp_err_t ensure_set_dir(const char *name)
{
    if (g_flat_mode) {
        return ESP_OK; // nothing to create
    }
    
    // First ensure base cert directory exists
    struct stat st;
    if (stat(HTTPS_CERT_DIR, &st) != 0) {
        if (mkdir(HTTPS_CERT_DIR, 0777) != 0) {
            ESP_LOGW(TAG, "mkdir base dir (%s) failed errno=%d; enabling flat mode", HTTPS_CERT_DIR, errno);
            g_flat_mode = true;
            return ESP_OK;
        }
    }
    
    char path[128];
    snprintf(path, sizeof(path), "%s/%s", HTTPS_CERT_DIR, name);
    if (stat(path, &st) == 0) {
        if (S_ISDIR(st.st_mode)) {
            return ESP_OK;
        }
        // Exists but not a dir; fall back to flat mode
        ESP_LOGW(TAG, "Path exists not dir (%s) switching to flat mode", path);
        g_flat_mode = true;
        return ESP_OK;
    }
    if (mkdir(path, 0777) == 0) {
        return ESP_OK;
    }
    // Any failure -> assume FS lacks dir support; fallback
    ESP_LOGW(TAG, "mkdir(%s) failed errno=%d; enabling flat mode", path, errno);
    g_flat_mode = true;
    return ESP_OK;
}

// List handler: GET /https_certs -> JSON array
static esp_err_t https_certs_list_handler(httpd_req_t *req){
    cJSON *root = cJSON_CreateArray();
    for(size_t i=0;i<g_set_count;i++){
        cJSON *o=cJSON_CreateObject();
        cJSON_AddStringToObject(o,"name", g_sets[i].name);
        cJSON_AddBoolToObject(o,"has_ca", g_sets[i].has_ca);
        cJSON_AddBoolToObject(o,"has_client_cert", g_sets[i].has_client_cert);
        cJSON_AddBoolToObject(o,"has_client_key", g_sets[i].has_client_key);
        cJSON_AddItemToArray(root,o);
    }
    char *json=cJSON_PrintUnformatted(root); cJSON_Delete(root);
    if(!json) {
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "OOM");
    }
    httpd_resp_set_type(req,"application/json");
    httpd_resp_send(req,json,HTTPD_RESP_USE_STRLEN); free(json); return ESP_OK;
}

// Upload new set: POST /https_certs?name=<name> multipart fields (ca_cert, client_cert, client_key)
typedef struct {
    cert_set_entry_t temp;
    cert_set_entry_t *existing;
    bool any_written;
    esp_err_t last_err;
    FILE *current_file;
    char current_field[16];
} multi_upload_ctx_t;

static bool multi_on_part_begin(const multipart_part_info_t *info, void *user_ctx){
    multi_upload_ctx_t *m=(multi_upload_ctx_t*)user_ctx; 
    if(m->last_err!=ESP_OK) return false;
    
    const char *name = m->temp.name; // already validated
    const char *fname=NULL;
    if(strcmp(info->name,"ca_cert")==0){ fname="ca.pem"; strncpy(m->current_field,"ca_cert",sizeof(m->current_field)-1);} 
    else if(strcmp(info->name,"client_cert")==0){ fname="client.crt"; strncpy(m->current_field,"client_cert",sizeof(m->current_field)-1);} 
    else if(strcmp(info->name,"client_key")==0){ fname="client.key"; strncpy(m->current_field,"client_key",sizeof(m->current_field)-1);} 
    else { ESP_LOGW(TAG, "Unknown multipart field '%s'", info->name); m->last_err = ESP_FAIL; return false; }
    
    char *path = (char*)cm_alloc_psram(192);
    if(!path){ 
        ESP_LOGE(TAG, "OOM path alloc"); 
        m->last_err=ESP_FAIL; 
        return false; 
    }
    
    build_set_path(name,fname,path,192);
    ESP_LOGI(TAG, "Opening file for upload: %s (field=%s)", path, info->name);
    
    // Set secure file permissions (owner read/write only)
    m->current_file = fopen(path,"wb");
    if(!m->current_file){
        ESP_LOGE(TAG, "fopen(%s) failed errno=%d", path, errno);
        m->last_err=ESP_FAIL;
        free(path);
        return false;
    }
    
    // Set file permissions after creation
    if(chmod(path, 0600) != 0) {
        ESP_LOGW(TAG, "chmod(%s, 0600) failed errno=%d", path, errno);
    }
    
    free(path);
    return true;
}

static esp_err_t multi_on_part_data(const char *data,size_t len, void *user_ctx){
    multi_upload_ctx_t *m=(multi_upload_ctx_t*)user_ctx; if(m->last_err!=ESP_OK) return ESP_FAIL;
    if(!m->current_file){
        m->last_err=ESP_FAIL;
        return ESP_FAIL;
    }
    size_t w=fwrite(data,1,len,m->current_file);
    if(w!=len){ ESP_LOGE(TAG, "Short write (%u/%u) field=%s", (unsigned)w, (unsigned)len, m->current_field); m->last_err=ESP_FAIL; return ESP_FAIL; }
    return ESP_OK;
}

static void multi_on_part_end(void *user_ctx){
    multi_upload_ctx_t *m=(multi_upload_ctx_t*)user_ctx; 
    if(m->current_file){ fclose(m->current_file); m->current_file=NULL; }
    if(strcmp(m->current_field,"ca_cert")==0) m->temp.has_ca=true;
    else if(strcmp(m->current_field,"client_cert")==0) m->temp.has_client_cert=true;
    else if(strcmp(m->current_field,"client_key")==0) m->temp.has_client_key=true;
    m->current_field[0]='\0';
    m->any_written=true; 
}

static void multi_on_finished(void *user_ctx){
    multi_upload_ctx_t *m=(multi_upload_ctx_t*)user_ctx;
    if(m->current_file){
        fclose(m->current_file);
        m->current_file=NULL;
    }
}

#define NAME_PARAM_SIZE 32

static esp_err_t https_certs_upload_handler(httpd_req_t *req){
    // Rate limiting check
    uint32_t current_time = esp_log_timestamp();
    if (last_upload_time != 0 && (current_time - last_upload_time) < UPLOAD_RATE_LIMIT_MS) {
        ESP_LOGW(TAG, "Upload rate limited - too frequent requests");
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "rate limited");
    }
    
    char *name_param = (char*)alloca(NAME_PARAM_SIZE); memset(name_param,0,NAME_PARAM_SIZE);
    char *q=strchr(req->uri,'?');
    if(q){ if(strncmp(q+1,"name=",5)==0){ strncpy(name_param,q+6,NAME_PARAM_SIZE-1); } }
    if(!valid_set_name(name_param)) {
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "bad name");
    }
    if(find_set(name_param)){
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "exists");
    }
    if(g_set_count>=CERT_MANAGER_MAX_SETS) {
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "full");
    }
    if(ensure_set_dir(name_param)!=ESP_OK) {
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "dir");
    }
    ESP_LOGI(TAG, "Begin upload for new cert set '%s' (flat_mode=%d)", name_param, g_flat_mode);
    // Double-check base directory exists (some flows might call upload before init?)
    struct stat _st_base; if(stat(HTTPS_CERT_DIR,&_st_base)!=0){
        if(mkdir(HTTPS_CERT_DIR,0777)!=0){
            ESP_LOGE(TAG, "Base cert dir missing and mkdir failed errno=%d", errno);
            return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "base");
        }
    }
    if(g_flat_mode){
        // Probe write access with a tiny temp file (non-dot name for FS that reject dotfiles)
        char probe[128];
        snprintf(probe,sizeof(probe),"%s/probe.tmp", HTTPS_CERT_DIR);
        FILE *pf = fopen(probe,"wb");
        if(!pf){
            ESP_LOGW(TAG, "flat mode probe fopen failed errno=%d (continuing)", errno);
        } else {
            fwrite("x",1,1,pf); fclose(pf); unlink(probe);
        }
    }
    multi_upload_ctx_t m={0}; strncpy(m.temp.name,name_param,sizeof(m.temp.name)-1);
    multipart_upload_handlers_t handlers={ .on_part_begin=multi_on_part_begin, .on_part_data=multi_on_part_data, .on_part_end=multi_on_part_end, .on_finished=multi_on_finished };
    multipart_upload_config_t cfg = multipart_upload_default_config(); cfg.rx_buf_size=2048;
    esp_err_t err = multipart_upload_handle(req,&handlers,&m,&cfg);
    if(err!=ESP_OK){
        ESP_LOGE(TAG, "multipart_upload_handle parse error err=%d", err);
        httpd_resp_send_err(req,HTTPD_500_INTERNAL_SERVER_ERROR,"upload parse");
        return ESP_FAIL;
    }
    if(m.last_err!=ESP_OK){
        ESP_LOGE(TAG, "multipart callbacks reported failure (last_err=%d)", m.last_err);
        httpd_resp_send_err(req,HTTPD_500_INTERNAL_SERVER_ERROR,"upload cb");
        return ESP_FAIL;
    }
    if(!m.any_written){
        ESP_LOGE(TAG, "No valid parts written for set '%s'", name_param);
        httpd_resp_send_err(req,HTTPD_400_BAD_REQUEST,"empty");
        return ESP_FAIL;
    }
    ESP_LOGI(TAG, "Upload success for set '%s' ca=%d client_cert=%d client_key=%d", name_param, m.temp.has_ca, m.temp.has_client_cert, m.temp.has_client_key);
    g_sets[g_set_count++] = m.temp; save_manifest();
    
    // Update rate limiting timestamp on successful upload
    last_upload_time = esp_log_timestamp();
    
    httpd_resp_set_type(req,"application/json"); httpd_resp_sendstr(req,"{\"ok\":true}"); return ESP_OK;
}

// Delete /https_certs/<name>
static esp_err_t https_certs_delete_handler(httpd_req_t *req){
    char name[32]={0};
    const char *uri=req->uri; // /https_certs or /https_certs?<q> or /https_certs/<name>
    const char *q=strchr(uri,'?');
    if(q && strncmp(q+1,"name=",5)==0){ strncpy(name,q+6,sizeof(name)-1); }
    if(name[0]=='\0'){
        if(strncmp(uri,"/https_certs/",13)==0){
            strncpy(name, uri+13, sizeof(name)-1);
        } else if(strncmp(uri, "/cert_manager/sets/", 19)==0){
            // prefix length is 19 ("/cert_manager/sets/")
            strncpy(name, uri+19, sizeof(name)-1);
        }
    }
    if(!valid_set_name(name)) {
        return httpd_resp_send_err(req,HTTPD_400_BAD_REQUEST,"bad name");
    }
    cert_set_entry_t *e=find_set(name);
    if(!e) {
        ESP_LOGW(TAG, "Delete request for non-existent cert set '%s' (uri=%s)", name, uri);
        return httpd_resp_send_err(req,HTTPD_404_NOT_FOUND,"nf");
    }
    char base[160];
    if (g_flat_mode) {
        build_set_path(name,"ca.pem",base,sizeof(base)); unlink(base);
        build_set_path(name,"client.crt",base,sizeof(base)); unlink(base);
        build_set_path(name,"client.key",base,sizeof(base)); unlink(base);
    } else {
        snprintf(base,sizeof(base),"%s/%s", HTTPS_CERT_DIR, name);
        build_set_path(name,"ca.pem",base,sizeof(base)); unlink(base);
        build_set_path(name,"client.crt",base,sizeof(base)); unlink(base);
        build_set_path(name,"client.key",base,sizeof(base)); unlink(base);
        // rebuild base then remove dir
        snprintf(base,sizeof(base),"%s/%s", HTTPS_CERT_DIR, name);
        rmdir(base);
    }
    size_t idx = e - g_sets;
    for(size_t i=idx+1;i<g_set_count;i++) g_sets[i-1]=g_sets[i];
    g_set_count--; save_manifest();
    httpd_resp_set_type(req,"application/json"); httpd_resp_sendstr(req,"{\"ok\":true}"); return ESP_OK;
}

static esp_err_t ensure_https_cert_dir(void)
{
    struct stat st;
    if (stat(HTTPS_CERT_DIR, &st) == 0)
    {
        if (S_ISDIR(st.st_mode)) return ESP_OK;
        ESP_LOGE(TAG, "Path exists but not dir: %s", HTTPS_CERT_DIR);
        return ESP_FAIL;
    }
    if (mkdir(HTTPS_CERT_DIR, 0777) == 0)
    {
        ESP_LOGI(TAG, "Created dir %s", HTTPS_CERT_DIR);
        return ESP_OK;
    }
    ESP_LOGE(TAG, "mkdir(%s) failed errno=%d", HTTPS_CERT_DIR, errno);
    return ESP_FAIL;
}

static size_t file_size_if_exists(const char *path)
{
    struct stat st;
    if (stat(path, &st) == 0 && S_ISREG(st.st_mode)) return (size_t)st.st_size;
    return 0;
}

static esp_err_t https_cert_status_handler(httpd_req_t *req)
{
    cJSON *root = cJSON_CreateObject();
    cJSON_AddNumberToObject(root, "ca_size", (double)file_size_if_exists(HTTPS_CA_CERT_PATH));
    cJSON_AddNumberToObject(root, "client_cert_size", (double)file_size_if_exists(HTTPS_CLIENT_CERT_PATH));
    cJSON_AddNumberToObject(root, "client_key_size", (double)file_size_if_exists(HTTPS_CLIENT_KEY_PATH));
    char *json = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (!json) return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "OOM");
    httpd_resp_set_type(req, "application/json");
    esp_err_t r = httpd_resp_send(req, json, HTTPD_RESP_USE_STRLEN);
    free(json);
    return r;
}

typedef struct
{
    FILE *current;
    const char *current_path;
    size_t bytes_written;
    int parts_stored;
    esp_err_t last_err;
} cert_upload_ctx_t;

static bool cert_upload_on_part_begin(const multipart_part_info_t *info, void *user_ctx)
{
    cert_upload_ctx_t *c = (cert_upload_ctx_t*)user_ctx;
    c->current = NULL;
    c->current_path = NULL;
    c->bytes_written = 0;
    c->last_err = ESP_OK;
    if (strcmp(info->name, "ca_cert") == 0)
    {
        c->current_path = HTTPS_CA_CERT_PATH;
    }
    else if (strcmp(info->name, "client_cert") == 0)
    {
        c->current_path = HTTPS_CLIENT_CERT_PATH;
    }
    else if (strcmp(info->name, "client_key") == 0)
    {
        c->current_path = HTTPS_CLIENT_KEY_PATH;
    }
    else
    {
        return false; // ignore unexpected fields
    }
    c->current = fopen(c->current_path, "wb");
    if (!c->current)
    {
        ESP_LOGE(TAG, "fopen %s failed errno=%d", c->current_path, errno);
        c->last_err = ESP_FAIL;
        return false;
    }
    
    // Set secure file permissions (owner read/write only)
    if(chmod(c->current_path, 0600) != 0) {
        ESP_LOGW(TAG, "chmod(%s, 0600) failed errno=%d", c->current_path, errno);
    }
    
    ESP_LOGI(TAG, "Receiving part '%s' -> %s", info->name, c->current_path);
    return true;
}

static esp_err_t cert_upload_on_part_data(const char *data, size_t len, void *user_ctx)
{
    cert_upload_ctx_t *c = (cert_upload_ctx_t*)user_ctx;
    if (!c->current) return ESP_OK; // not accepting
    if (fwrite(data, 1, len, c->current) != len)
    {
        ESP_LOGE(TAG, "write failed %s", c->current_path);
        c->last_err = ESP_FAIL;
        return ESP_FAIL;
    }
    c->bytes_written += len;
    if (c->bytes_written > 32 * 1024)
    { // safety limit per file
        ESP_LOGE(TAG, "file too large >32KB %s", c->current_path);
        c->last_err = ESP_FAIL;
        return ESP_FAIL;
    }
    return ESP_OK;
}

static void cert_upload_on_part_end(void *user_ctx)
{
    cert_upload_ctx_t *c = (cert_upload_ctx_t*)user_ctx;
    if (c->current)
    {
        fclose(c->current);
        c->current = NULL;
        c->parts_stored++;
    }
}

static void cert_upload_on_finished(void *user_ctx)
{
    cert_upload_ctx_t *c = (cert_upload_ctx_t*)user_ctx;
    if (c->current)
    {
        fclose(c->current);
        c->current = NULL;
    }
}

static esp_err_t https_cert_upload_handler(httpd_req_t *req)
{
    cert_upload_ctx_t uctx = {0};
    multipart_upload_handlers_t handlers =
    {
        .on_part_begin = cert_upload_on_part_begin,
        .on_part_data = cert_upload_on_part_data,
        .on_part_end = cert_upload_on_part_end,
        .on_finished = cert_upload_on_finished
    };
    multipart_upload_config_t cfg = multipart_upload_default_config();
    cfg.rx_buf_size = 2048; // streaming buffer
    esp_err_t err = multipart_upload_handle(req, &handlers, &uctx, &cfg);
    if (err != ESP_OK || uctx.last_err != ESP_OK)
    {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Upload failed");
        return ESP_FAIL;
    }
    httpd_resp_set_type(req, "application/json");
    httpd_resp_sendstr(req, "{\"ok\":true}");
    return ESP_OK;
}

esp_err_t cert_manager_init(void)
{
    // Check if filesystem mount point is accessible
    struct stat mount_st;
    if (stat(FS_MOUNT_POINT, &mount_st) != 0) {
        ESP_LOGE(TAG, "Filesystem mount point %s not accessible errno=%d", FS_MOUNT_POINT, errno);
        return ESP_FAIL;
    }
    
    esp_err_t r = ensure_https_cert_dir();
    if(r==ESP_OK){ load_manifest(); }
    return r;
}


// Router style consolidated paths under /cert_manager/*
//  GET    /cert_manager/status           -> single-cert status (old /https_cert_status)
//  POST   /cert_manager/upload           -> single-cert upload (old /https_cert_upload)
//  GET    /cert_manager/sets             -> list multi sets (old /https_certs GET)
//  POST   /cert_manager/sets?name=<name> -> upload new set (old /https_certs POST)
//  DELETE /cert_manager/sets/<name>      -> delete set (old /https_certs DELETE)
static esp_err_t cert_manager_router_handler(httpd_req_t *req)
{
    const char *uri = req->uri; // expected prefix /cert_manager
    // Advance past prefix
    const char *p = uri + strlen("/cert_manager");
    if (*p == '\0' || *p == '/') {
        if (*p == '/') p++; // skip '/'
        // Determine segment
        if (strncmp(p, "status", 6) == 0 && (p[6]=='\0' || p[6]=='/' || p[6]=='?')) {
            if (req->method == HTTP_GET) return https_cert_status_handler(req);
        } else if (strncmp(p, "upload", 6) == 0 && (p[6]=='\0' || p[6]=='?' || p[6]=='/')) {
            if (req->method == HTTP_POST) return https_cert_upload_handler(req);
        } else if (strncmp(p, "sets", 4) == 0) {
            const char *rest = p + 4; // may be \0, ?query, /<name>
            if (*rest == '\0' || *rest == '?' ) {
                if (req->method == HTTP_GET) return https_certs_list_handler(req);
                if (req->method == HTTP_POST) return https_certs_upload_handler(req);
            } else if (*rest == '/') {
                if (req->method == HTTP_DELETE) return https_certs_delete_handler(req);
            }
        }
    }
    return httpd_resp_send_err(req, HTTPD_404_NOT_FOUND, "cert_manager route");
}

esp_err_t cert_manager_register_handlers(httpd_handle_t server)
{
    // NOTE: ESP-IDF http server matches by method + pattern, so we still need one registration per HTTP verb even with wildcard URI.
    // We minimize to 3 handlers with shared router logic.
    static const httpd_uri_t cm_get = { .uri="/cert_manager*", .method=HTTP_GET, .handler=cert_manager_router_handler, .user_ctx=NULL };
    static const httpd_uri_t cm_post = { .uri="/cert_manager*", .method=HTTP_POST, .handler=cert_manager_router_handler, .user_ctx=NULL };
    static const httpd_uri_t cm_delete = { .uri="/cert_manager*", .method=HTTP_DELETE, .handler=cert_manager_router_handler, .user_ctx=NULL };
    const httpd_uri_t *all[] = { &cm_get, &cm_post, &cm_delete };
    for (size_t i=0;i<sizeof(all)/sizeof(all[0]);++i){
        esp_err_t r = httpd_register_uri_handler(server, all[i]);
        if (r != ESP_OK && r != ESP_ERR_HTTPD_HANDLER_EXISTS){
            ESP_LOGE(TAG, "Failed to register %s err=%s", all[i]->uri, esp_err_to_name(r));
            return r;
        }
    }
    ESP_LOGI(TAG, "Cert manager router registered at /cert_manager*");
    return ESP_OK;
}

static esp_err_t load_file_alloc(const char *path, char **buf_out, size_t *len_out)
{
    *buf_out = NULL;
    *len_out = 0;
    struct stat st;
    if (stat(path, &st) != 0) return ESP_ERR_NOT_FOUND;
    FILE *f = fopen(path, "rb");
    if (!f) return ESP_FAIL;
    char *buf = (char*)malloc(st.st_size + 1);
    if (!buf)
    {
        fclose(f);
        return ESP_ERR_NO_MEM;
    }
    size_t r = fread(buf, 1, st.st_size, f);
    fclose(f);
    if (r != (size_t)st.st_size)
    {
        free(buf);
        return ESP_FAIL;
    }
    buf[r] = '\0';
    *buf_out = buf;
    *len_out = r;
    return ESP_OK;
}

esp_err_t cert_manager_load_ca(char **buf_out, size_t *len_out)
{
    return load_file_alloc(HTTPS_CA_CERT_PATH, buf_out, len_out);
}
esp_err_t cert_manager_load_client_cert(char **buf_out, size_t *len_out)
{
    return load_file_alloc(HTTPS_CLIENT_CERT_PATH, buf_out, len_out);
}
esp_err_t cert_manager_load_client_key(char **buf_out, size_t *len_out)
{
    return load_file_alloc(HTTPS_CLIENT_KEY_PATH, buf_out, len_out);
}

const char * cert_manager_get_ca_path(void)
{
    return HTTPS_CA_CERT_PATH;
}
const char * cert_manager_get_client_cert_path(void)
{
    return HTTPS_CLIENT_CERT_PATH;
}
const char * cert_manager_get_client_key_path(void)
{
    return HTTPS_CLIENT_KEY_PATH;
}
