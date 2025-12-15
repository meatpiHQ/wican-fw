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

#include "multipart_upload.h"
#include <string.h>
#include <strings.h>
#include <stdlib.h>
#include <ctype.h>
#include "esp_log.h"
#include "multipart_parser.h"

#ifndef MIN
#define MIN(a,b) ((a)<(b)?(a):(b))
#endif

static const char *TAG_MP = "MP-UPLOAD";

typedef enum {
    DC_READ_SIZE = 0,
    DC_READ_DATA,
    DC_READ_DATA_CRLF,
    DC_READ_TRAILERS,
    DC_DONE,
    DC_ERROR,
} dechunk_state_t;

typedef struct {
    dechunk_state_t state;
    size_t chunk_remaining;
    bool in_ext;
    char size_buf[16];
    size_t size_len;
    size_t trailer_line_len;
} dechunker_t;

static void dechunker_init(dechunker_t *dc)
{
    if (!dc) return;
    memset(dc, 0, sizeof(*dc));
    dc->state = DC_READ_SIZE;
}

// Consume chunked-encoding bytes from `in` and write decoded body bytes to `out`.
// Advances *in_used by number of input bytes consumed. Returns number of output bytes written.
static size_t dechunker_consume(dechunker_t *dc,
                                const char *in,
                                size_t in_len,
                                size_t *in_used,
                                char *out,
                                size_t out_cap)
{
    if (!dc || !in || !in_used || !out || out_cap == 0) return 0;

    size_t produced = 0;
    size_t i = 0;
    while (i < in_len)
    {
        char c = in[i];

        if (dc->state == DC_READ_SIZE)
        {
            // Chunk-size line: 1A3[;ext...]\r\n
            if (c == '\r')
            {
                i++;
                continue;
            }
            if (c == '\n')
            {
                dc->size_buf[MIN(dc->size_len, sizeof(dc->size_buf) - 1)] = '\0';
                if (dc->size_len == 0)
                {
                    dc->state = DC_ERROR;
                    break;
                }
                char *endp = NULL;
                unsigned long sz = strtoul(dc->size_buf, &endp, 16);
                (void)endp;
                dc->chunk_remaining = (size_t)sz;
                dc->size_len = 0;
                dc->in_ext = false;

                if (dc->chunk_remaining == 0)
                {
                    dc->state = DC_READ_TRAILERS;
                    dc->trailer_line_len = 0;
                }
                else
                {
                    dc->state = DC_READ_DATA;
                }
                i++;
                continue;
            }
            if (c == ';')
            {
                dc->in_ext = true;
                i++;
                continue;
            }
            if (!dc->in_ext)
            {
                if (isxdigit((unsigned char)c))
                {
                    if (dc->size_len < sizeof(dc->size_buf) - 1)
                    {
                        dc->size_buf[dc->size_len++] = c;
                    }
                    else
                    {
                        dc->state = DC_ERROR;
                        break;
                    }
                }
                else if (c == ' ' || c == '\t')
                {
                    // ignore
                }
                else
                {
                    dc->state = DC_ERROR;
                    break;
                }
            }
            // ignore extension bytes until EOL
            i++;
            continue;
        }

        if (dc->state == DC_READ_DATA)
        {
            size_t avail = in_len - i;
            size_t want = dc->chunk_remaining;
            size_t can_take = avail < want ? avail : want;
            size_t space = out_cap - produced;
            if (can_take > space) can_take = space;
            if (can_take == 0)
            {
                // output buffer full; stop here
                break;
            }

            memcpy(out + produced, in + i, can_take);
            produced += can_take;
            dc->chunk_remaining -= can_take;
            i += can_take;

            if (dc->chunk_remaining == 0)
            {
                dc->state = DC_READ_DATA_CRLF;
            }
            continue;
        }

        if (dc->state == DC_READ_DATA_CRLF)
        {
            // Expect CRLF (or LF); be lenient and ignore CR
            if (c == '\r')
            {
                i++;
                continue;
            }
            if (c == '\n')
            {
                dc->state = DC_READ_SIZE;
                dc->size_len = 0;
                dc->in_ext = false;
                i++;
                continue;
            }
            // Unexpected
            dc->state = DC_ERROR;
            break;
        }

        if (dc->state == DC_READ_TRAILERS)
        {
            // Read optional trailer headers until blank line
            if (c == '\r')
            {
                i++;
                continue;
            }
            if (c == '\n')
            {
                if (dc->trailer_line_len == 0)
                {
                    dc->state = DC_DONE;
                    i++;
                    continue;
                }
                dc->trailer_line_len = 0;
                i++;
                continue;
            }
            dc->trailer_line_len++;
            i++;
            continue;
        }

        // DONE/ERROR: stop consuming
        break;
    }

    *in_used += i;
    return produced;
}

// simple case-insensitive substring search
static const char* ci_strstr(const char *hay, const char *needle)
{
    if (!hay || !needle || !*needle) return NULL;
    size_t nlen = strlen(needle);
    for (const char *p = hay; *p; ++p)
    {
        size_t i = 0;
        while (i < nlen)
        {
            char c1 = p[i];
            if (!c1) return NULL;
            char c2 = needle[i];
            if (c1 >= 'A' && c1 <= 'Z') c1 = (char)(c1 - 'A' + 'a');
            if (c2 >= 'A' && c2 <= 'Z') c2 = (char)(c2 - 'A' + 'a');
            if (c1 != c2) break;
            ++i;
        }
        if (i == nlen) return p;
    }
    return NULL;
}

static bool request_is_chunked(httpd_req_t *req)
{
    size_t te_len = httpd_req_get_hdr_value_len(req, "Transfer-Encoding");
    if (te_len == 0 || te_len > 128) return false;
    char *te = (char*)malloc(te_len + 1);
    if (!te) return false;
    bool is_chunked = false;
    if (httpd_req_get_hdr_value_str(req, "Transfer-Encoding", te, te_len + 1) == ESP_OK)
    {
        if (ci_strstr(te, "chunked")) is_chunked = true;
    }
    free(te);
    return is_chunked;
}

// naive subsequence search for binary data
static int find_bytes(const char *buf, size_t buf_len, const char *pat, size_t pat_len)
{
    if (!buf || !pat || pat_len == 0 || buf_len < pat_len) return -1;
    for (size_t i = 0; i + pat_len <= buf_len; ++i)
    {
        if (buf[i] == pat[0] && memcmp(buf + i, pat, pat_len) == 0) return (int)i;
    }
    return -1;
}

typedef enum { HF_NONE = 0, HF_FIELD, HF_VALUE } header_feed_state_t;

typedef struct mp_internal_ctx_t
{
    httpd_req_t *req;
    const multipart_upload_handlers_t *handlers;
    void *user_ctx;

    multipart_parser *parser;

    // current header accumulation
    header_feed_state_t feed_state;
    char cur_field[64];
    size_t cur_field_len;
    char cur_value[256];
    size_t cur_value_len;

    // current part info and state
    multipart_part_info_t part_info;
    bool headers_done_for_part;
    bool accept_current_part;

    bool had_error;
} mp_internal_ctx_t;

static void mp_commit_header(mp_internal_ctx_t *ictx)
{
    if (ictx->cur_field_len == 0) return;
    ictx->cur_field[MIN(ictx->cur_field_len, sizeof(ictx->cur_field)-1)] = '\0';
    ictx->cur_value[MIN(ictx->cur_value_len, sizeof(ictx->cur_value)-1)] = '\0';

    // Content-Disposition: form-data; name="..."; filename="..."
    if (strncasecmp(ictx->cur_field, "Content-Disposition", 19) == 0)
    {
        const char *v = ictx->cur_value;
        // extract name="..."
        const char *n = ci_strstr(v, "name=");
        if (n)
        {
            n += 5; // skip name=
            if (*n == '"') { n++; }
            size_t i = 0;
            while (*n && *n != ';' && *n != '"' && i < sizeof(ictx->part_info.name)-1)
            {
                ictx->part_info.name[i++] = *n++;
            }
            ictx->part_info.name[i] = '\0';
        }
        const char *f = ci_strstr(v, "filename=");
        if (f)
        {
            f += 9; // skip filename=
            if (*f == '"') { f++; }
            size_t i = 0;
            while (*f && *f != ';' && *f != '"' && i < sizeof(ictx->part_info.filename)-1)
            {
                ictx->part_info.filename[i++] = *f++;
            }
            ictx->part_info.filename[i] = '\0';
        }
    }
    else if (strncasecmp(ictx->cur_field, "Content-Type", 12) == 0)
    {
        strncpy(ictx->part_info.content_type, ictx->cur_value, sizeof(ictx->part_info.content_type)-1);
        ictx->part_info.content_type[sizeof(ictx->part_info.content_type)-1] = '\0';
    }

    // reset for next header
    ictx->cur_field_len = 0; ictx->cur_value_len = 0; ictx->feed_state = HF_NONE;
}

static int cb_on_header_field(multipart_parser* p, const char *at, size_t length)
{
    mp_internal_ctx_t *ictx = (mp_internal_ctx_t*)multipart_parser_get_data(p);
    if (ictx->feed_state == HF_VALUE)
    {
        mp_commit_header(ictx);
    }
    ictx->feed_state = HF_FIELD;
    size_t to_copy = MIN(length, sizeof(ictx->cur_field) - 1 - ictx->cur_field_len);
    if (to_copy > 0)
    {
        memcpy(ictx->cur_field + ictx->cur_field_len, at, to_copy);
        ictx->cur_field_len += to_copy;
    }
    return 0;
}

static int cb_on_header_value(multipart_parser* p, const char *at, size_t length)
{
    mp_internal_ctx_t *ictx = (mp_internal_ctx_t*)multipart_parser_get_data(p);
    ictx->feed_state = HF_VALUE;
    size_t to_copy = MIN(length, sizeof(ictx->cur_value) - 1 - ictx->cur_value_len);
    if (to_copy > 0)
    {
        memcpy(ictx->cur_value + ictx->cur_value_len, at, to_copy);
        ictx->cur_value_len += to_copy;
    }
    return 0;
}

static int cb_on_headers_complete(multipart_parser* p)
{
    mp_internal_ctx_t *ictx = (mp_internal_ctx_t*)multipart_parser_get_data(p);
    // commit any pending header pair
    mp_commit_header(ictx);

    ictx->accept_current_part = false;
    if (ictx->handlers && ictx->handlers->on_part_begin)
    {
        ictx->accept_current_part = ictx->handlers->on_part_begin(&ictx->part_info, ictx->user_ctx);
    }

    ictx->headers_done_for_part = true;
    return 0;
}

static int cb_on_part_data_begin(multipart_parser* p)
{
    // nothing specific; headers_complete already decided whether to accept this part
    return 0;
}

static int cb_on_part_data(multipart_parser* p, const char *at, size_t length)
{
    mp_internal_ctx_t *ictx = (mp_internal_ctx_t*)multipart_parser_get_data(p);
    if (ictx->accept_current_part && ictx->handlers && ictx->handlers->on_part_data)
    {
        esp_err_t err = ictx->handlers->on_part_data(at, length, ictx->user_ctx);
        if (err != ESP_OK)
        {
            ictx->had_error = true;
            return 1; // abort
        }
    }
    return 0;
}

static int cb_on_part_data_end(multipart_parser* p)
{
    mp_internal_ctx_t *ictx = (mp_internal_ctx_t*)multipart_parser_get_data(p);
    if (ictx->accept_current_part && ictx->handlers && ictx->handlers->on_part_end)
    {
        ictx->handlers->on_part_end(ictx->user_ctx);
    }
    // reset part info for next one
    memset(&ictx->part_info, 0, sizeof(ictx->part_info));
    ictx->headers_done_for_part = false;
    ictx->accept_current_part = false;
    ictx->cur_field_len = 0; ictx->cur_value_len = 0; ictx->feed_state = HF_NONE;
    return 0;
}

static int cb_on_body_end(multipart_parser* p)
{
    mp_internal_ctx_t *ictx = (mp_internal_ctx_t*)multipart_parser_get_data(p);
    if (ictx->handlers && ictx->handlers->on_finished)
    {
        ictx->handlers->on_finished(ictx->user_ctx);
    }
    return 0;
}

static bool parse_boundary_from_content_type(const char *ct, char *out, size_t out_len)
{
    if (!ct) return false;
    // find 'boundary=' in a case-insensitive way
    const char *p = ct;
    while (*p)
    {
        // skip spaces and semicolons
        while (*p == ' ' || *p == ';') p++;
        if (!*p) break;
        const char *start = p;
        // find end of token
        while (*p && *p != ';') p++;
        size_t len = (size_t)(p - start);
        // check if token starts with boundary=
        if (len >= 9 && strncasecmp(start, "boundary=", 9) == 0)
        {
            const char *b = start + 9;
            // strip optional quotes
            if (*b == '"')
            {
                b++;
                const char *e = strchr(b, '"');
                len = e ? (size_t)(e - b) : strlen(b);
            }
            else
            {
                // token until end or semicolon
                const char *e = start + len;
                // trim spaces at end
                while (e > b && (*(e-1) == ' ')) e--;
                len = (size_t)(e - b);
            }
            // The parser expects the initial boundary line exactly as in body: "--" + token
            if (len + 2 + 1 > out_len) return false; // +2 for leading dashes, +1 for NUL
            out[0] = '-'; out[1] = '-';
            memcpy(out + 2, b, len);
            out[len + 2] = '\0';
            return true;
        }
        if (*p == ';') p++;
    }
    return false;
}

static bool extract_boundary_from_first_line(const char *data, size_t len, char *boundary, size_t max_len)
{
    const char *line_end = memchr(data, '\r', len);
    if (!line_end) line_end = memchr(data, '\n', len);
    if (!line_end) return false;
    size_t blen = (size_t)(line_end - data);
    if (blen >= max_len) return false;
    memcpy(boundary, data, blen);
    boundary[blen] = '\0';
    return true;
}

esp_err_t multipart_upload_handle(httpd_req_t *req,
                                  const multipart_upload_handlers_t *handlers,
                                  void *user_ctx,
                                  const multipart_upload_config_t *cfg)
{
    if (!req || !handlers) return ESP_ERR_INVALID_ARG;

    multipart_upload_config_t local_cfg = cfg ? *cfg : multipart_upload_default_config();
    size_t rx_size = local_cfg.rx_buf_size ? local_cfg.rx_buf_size : MULTIPART_UPLOAD_DEFAULT_RX;

    char *rx = (char*)malloc(rx_size);
    if (!rx) return ESP_ERR_NO_MEM;

    bool is_chunked = request_is_chunked(req);
    char *raw_rx = NULL;
    dechunker_t dc;
    if (is_chunked)
    {
        raw_rx = (char*)malloc(rx_size);
        if (!raw_rx) { free(rx); return ESP_ERR_NO_MEM; }
        dechunker_init(&dc);
        ESP_LOGI(TAG_MP, "Request uses chunked transfer encoding");
    }

    esp_err_t result = ESP_FAIL;

    // build parser settings
    multipart_parser_settings s = {
        .on_header_field = cb_on_header_field,
        .on_header_value = cb_on_header_value,
        .on_headers_complete = cb_on_headers_complete,
        .on_part_data_begin = cb_on_part_data_begin,
        .on_part_data = cb_on_part_data,
        .on_part_data_end = cb_on_part_data_end,
        .on_body_end = cb_on_body_end,
    };

    char boundary[256];
    bool have_boundary = false;

    // Try Content-Type header
    size_t ct_len = httpd_req_get_hdr_value_len(req, "Content-Type");
    if (ct_len > 0 && ct_len < 256)
    {
        char *ct = (char*)malloc(ct_len + 1);
        if (ct)
        {
            if (httpd_req_get_hdr_value_str(req, "Content-Type", ct, ct_len + 1) == ESP_OK)
            {
                have_boundary = parse_boundary_from_content_type(ct, boundary, sizeof(boundary));
                if (have_boundary)
                {
                    ESP_LOGI(TAG_MP, "Boundary from header: %s", boundary);
                }
            }
            free(ct);
        }
    }

    mp_internal_ctx_t ictx = {0};
    ictx.req = req;
    ictx.handlers = handlers;
    ictx.user_ctx = user_ctx;

    // Determine boundary to use.
    // Best practice: use Content-Type boundary. If missing, fall back to reading first body line.
    const char *boundary_to_use = NULL;
    char body_boundary[256];
    size_t initial_len = 0;

    if (have_boundary)
    {
        boundary_to_use = boundary;
        ESP_LOGI(TAG_MP, "Boundary from header: %s", boundary_to_use);
    }
    else
    {
        // Pre-read some body bytes (decoded if chunked) to extract boundary from first line.
        // We'll feed these bytes into the parser after init.
        while (initial_len < rx_size && boundary_to_use == NULL)
        {
            int r = httpd_req_recv(req, is_chunked ? raw_rx : rx, (int)rx_size);
            if (r <= 0)
            {
                if (r == HTTPD_SOCK_ERR_TIMEOUT) continue;
                ESP_LOGE(TAG_MP, "Receive error while extracting boundary");
                goto cleanup;
            }

            if (!is_chunked)
            {
                // We read directly into rx; keep only what we got.
                // If boundary line is split across reads, this fallback may fail; prefer header boundary.
                initial_len = MIN((size_t)r, rx_size);
            }
            else
            {
                size_t in_used = 0;
                while (in_used < (size_t)r && initial_len < rx_size)
                {
                    size_t before = in_used;
                    size_t out = dechunker_consume(&dc, raw_rx + in_used, (size_t)r - in_used, &in_used, rx + initial_len, rx_size - initial_len);
                    if (dc.state == DC_ERROR)
                    {
                        ESP_LOGE(TAG_MP, "Chunked decode error while extracting boundary");
                        goto cleanup;
                    }
                    if (out > 0)
                    {
                        initial_len += out;
                        break;
                    }
                    if (in_used == before) break;
                }
            }

            if (initial_len > 0)
            {
                bool have_body_boundary = extract_boundary_from_first_line(rx, initial_len, body_boundary, sizeof(body_boundary));
                if (have_body_boundary)
                {
                    boundary_to_use = body_boundary;
                    ESP_LOGI(TAG_MP, "Boundary from body: %s", boundary_to_use);
                }
            }

            // If we consumed everything and still don't have boundary, keep looping a bit.
            // If rx is full and still no boundary, abort.
            if (initial_len >= rx_size && boundary_to_use == NULL)
            {
                ESP_LOGE(TAG_MP, "Failed to extract boundary from body (buffer too small)");
                goto cleanup;
            }
        }
    }

    if (!boundary_to_use)
    {
        ESP_LOGE(TAG_MP, "No boundary found in Content-Type or body");
        goto cleanup;
    }

    ictx.parser = multipart_parser_init(boundary_to_use, &s);
    if (!ictx.parser)
    {
        free(rx);
        return ESP_FAIL;
    }
    multipart_parser_set_data(ictx.parser, &ictx);

    // Feed any initial bytes we already read while extracting boundary.
    if (initial_len > 0)
    {
        size_t parsed_init = multipart_parser_execute(ictx.parser, rx, initial_len);
        if (parsed_init != initial_len)
        {
            int pos = find_bytes(rx, initial_len, boundary_to_use, strlen(boundary_to_use));
            if (pos > 0)
            {
                ESP_LOGW(TAG_MP, "Boundary not at start, resync at %d", pos);
                size_t parsed2 = multipart_parser_execute(ictx.parser, rx + pos, initial_len - (size_t)pos);
                if (parsed2 != initial_len - (size_t)pos)
                {
                    ESP_LOGE(TAG_MP, "Parser error in initial chunk after resync");
                    goto cleanup;
                }
            }
            else
            {
                ESP_LOGE(TAG_MP, "Parser error in initial chunk: parsed %u of %u", (unsigned)parsed_init, (unsigned)initial_len);
                goto cleanup;
            }
        }
    }

    // Continue reading and feeding data
    if (!is_chunked)
    {
        int remaining = req->content_len;
        if (remaining > 0)
        {
            while (remaining > 0)
            {
                int want = MIN(remaining, (int)rx_size);
                int r = httpd_req_recv(req, rx, want);
                if (r <= 0)
                {
                    if (r == HTTPD_SOCK_ERR_TIMEOUT) continue;
                    ESP_LOGE(TAG_MP, "Receive error while reading body");
                    goto cleanup;
                }
                remaining -= r;
                size_t parsed = multipart_parser_execute(ictx.parser, rx, (size_t)r);
                if (parsed != (size_t)r)
                {
                    ESP_LOGE(TAG_MP, "Parser error: parsed %u of %u", (unsigned)parsed, (unsigned)r);
                    goto cleanup;
                }
            }
        }
        else
        {
            // Unknown length (no Content-Length). Read until recv returns 0.
            for (;;)
            {
                int r = httpd_req_recv(req, rx, (int)rx_size);
                if (r <= 0)
                {
                    if (r == HTTPD_SOCK_ERR_TIMEOUT) continue;
                    break;
                }
                size_t parsed = multipart_parser_execute(ictx.parser, rx, (size_t)r);
                if (parsed != (size_t)r)
                {
                    ESP_LOGE(TAG_MP, "Parser error: parsed %u of %u", (unsigned)parsed, (unsigned)r);
                    goto cleanup;
                }
            }
        }
    }
    else
    {
        // Chunked: decode and feed until dechunker reaches DONE
        while (dc.state != DC_DONE)
        {
            int r = httpd_req_recv(req, raw_rx, (int)rx_size);
            if (r <= 0)
            {
                if (r == HTTPD_SOCK_ERR_TIMEOUT) continue;
                ESP_LOGE(TAG_MP, "Receive error while reading chunked body");
                goto cleanup;
            }

            size_t in_used = 0;
            while (in_used < (size_t)r)
            {
                size_t out_len = 0;
                size_t before = in_used;
                out_len = dechunker_consume(&dc, raw_rx + in_used, (size_t)r - in_used, &in_used, rx, rx_size);
                if (dc.state == DC_ERROR)
                {
                    ESP_LOGE(TAG_MP, "Chunked decode error");
                    goto cleanup;
                }
                if (out_len > 0)
                {
                    size_t parsed = multipart_parser_execute(ictx.parser, rx, out_len);
                    if (parsed != out_len)
                    {
                        ESP_LOGE(TAG_MP, "Parser error (chunked): parsed %u of %u", (unsigned)parsed, (unsigned)out_len);
                        goto cleanup;
                    }
                }
                if (in_used == before)
                {
                    // Avoid infinite loop
                    break;
                }
            }
        }
    }

    if (ictx.had_error)
    {
        ESP_LOGE(TAG_MP, "Handler reported error");
        goto cleanup;
    }

    result = ESP_OK;

cleanup:
    if (ictx.parser) multipart_parser_free(ictx.parser);
    free(rx);
    if (raw_rx) free(raw_rx);
    return result;
}
