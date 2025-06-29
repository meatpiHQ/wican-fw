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



#include <esp_wifi.h>
#include <esp_event.h>
#include <esp_log.h>
#include <esp_system.h>
#include <sys/param.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include <nvs_flash.h>
#include <sys/param.h>
#include "nvs_flash.h"
#include "esp_netif.h"
#include "esp_eth.h"
#include "esp_tls_crypto.h"
#include <esp_http_server.h>
#include "freertos/timers.h"
#include "esp_err.h"
#include <stdio.h>
#include <string.h>
#include <sys/unistd.h>
#include <sys/stat.h>
#include "esp_spiffs.h"

#include "config_server.h"
#include "cJSON.h"
#include<stdio.h>
#include <stdlib.h>
#include "ver.h"

#include <esp_wifi.h>
#include <esp_event.h>
#include <esp_log.h>
#include <esp_system.h>
#include <nvs_flash.h>
#include <sys/param.h>
#include "nvs_flash.h"
#include "esp_netif.h"
#include "esp_eth.h"
#include "lwip/sockets.h"

#include "esp_http_server.h"
#include "comm_server.h"
#include "types.h"
#include "driver/gpio.h"
#include "wifi_network.h"
#include "esp_vfs.h"
#include "esp_ota_ops.h"
#include "can.h"
#include "ble.h"
#include "sleep_mode.h"
#include "autopid.h"
#include "wc_mdns.h"
#include "hw_config.h"

#define WIFI_CONNECTED_BIT			BIT0
#define WS_CONNECTED_BIT			BIT1
TaskHandle_t xwebsocket_handle = NULL;
static EventGroupHandle_t xServerEventGroup = NULL;
static QueueHandle_t xip_Queue = NULL;

static QueueHandle_t *xTX_Queue, *xRX_Queue;

static uint8_t ws_led;
#define TAG __func__

httpd_handle_t server = NULL;
char *device_config_file = NULL;
static char *mqtt_canflt_file = NULL;

static char *device_id;
static const char logo[] = {"<svg xmlns=\"http://www.w3.org/2000/svg\" xmlns:xlink=\"http://www.w3.org/1999/xlink\" id=\"body_1\" width=\"136\" height=\"48\"><g transform=\"matrix(0.7010309 0 0 0.70588243 0 0)\"><g transform=\"matrix(0.40000004 0 0 0.40236688 0 0)\"><g><g> <path d=\"M84.73593 169.4794C 79.99207 169.4794 75.36686 168.99164 70.83204 168.24588L70.83204 168.24588L74.71903 158.56042C 78.00338 159.00487 81.331055 159.31183 84.73593 159.31183C 89.44589 159.31183 94.03909 158.82031 98.507996 157.98792L98.507996 157.98792L87.21239 145.75256L71.178566 145.75256L67.78876 145.75256L64.39895 145.75256L64.39895 135.58688L53.28978 135.58688C 52.11653 137.60194 49.95458 138.9767 47.45365 138.9767C 43.709793 138.9767 40.672142 135.94093 40.672142 132.19519C 40.672142 128.44945 43.707912 125.41556 47.45365 125.41556C 49.956463 125.41556 52.118412 126.786545 53.28978 128.80537L53.28978 128.80537L64.39895 128.80537L64.39895 125.415565L64.39895 118.634056L71.17858 118.634056L105.07672 118.634056L105.07672 120.084145L113.39306 129.09352C 113.98628 128.92403 114.60021 128.80539 115.24428 128.80539C 118.98814 128.80539 122.02391 131.84116 122.02391 135.5869C 122.02391 139.32887 118.99003 142.36276 115.24428 142.36276C 111.49853 142.36276 108.46654 139.33076 108.46654 135.5869C 108.46654 135.02946 108.5494 134.4965 108.6737 133.97862L108.6737 133.97862L100.769775 125.41557L71.180466 125.41557L71.180466 138.97484L91.51746 138.97484L91.51746 140.42116L106.065414 156.17625C 118.44953 152.48512 129.44383 145.62828 138.21404 136.60007L138.21404 136.60007L127.88828 125.415565L125.4156 125.415565L125.4156 93.35545L115.18025 82.27641C 112.34976 84.74156 109.340355 87.36491 106.42136 89.90914C 105.85827 90.404434 104.73964 91.37618 104.73964 91.37618L104.73964 91.37618L101.68692 91.42138L101.68692 91.51742L87.18793 91.51742C 86.01468 93.53624 83.85273 94.90912 81.349915 94.90912C 77.60606 94.90912 74.57028 91.87335 74.57028 88.12761C 74.57028 84.38187 77.60605 81.34987 81.349915 81.34987C 83.85272 81.34987 86.01467 82.720856 87.18793 84.73968L87.18793 84.73968L101.68692 84.73968L101.68692 85.47979L122.02392 67.35747L122.02392 40.673977L108.46843 40.673977L108.46843 55.174847C 110.48349 56.348095 111.85448 58.510048 111.85448 61.01098C 111.85448 64.75484 108.82248 67.79249 105.07674 67.79249C 101.334755 67.79249 98.297104 64.75672 98.297104 61.01098C 98.297104 58.508167 99.66809 56.34622 101.68691 55.17485L101.68691 55.17485L101.68691 40.67398L101.68691 37.284172L101.68691 33.894363L122.02391 33.894363L122.02391 20.367123C 119.33842 18.813463 116.58138 17.367132 113.69815 16.152452C 112.619064 16.649622 111.42697 16.947172 110.15956 16.947172C 105.48162 16.947172 101.68691 13.152462 101.68691 8.472642C 101.68691 3.794702 105.48162 -7.6293945E-06 110.15956 -7.6293945E-06C 114.383644 -7.6293945E-06 117.85632 3.0960224 118.498505 7.133672C 148.47575 20.184452 169.47754 49.9602 169.47754 84.73969C 169.47754 131.53984 131.53987 169.4794 84.73596 169.4794L84.73596 169.4794L84.73593 169.4794zM142.36089 88.12951C 138.61702 88.12951 135.58315 85.09751 135.58315 81.35177C 135.58315 78.84896 136.95601 76.68701 138.97107 75.51187L138.97107 75.51187L138.97107 47.455513L142.36089 47.455513L145.75258 47.455513L149.27422 47.455513C 144.08969 38.54595 137.08783 30.828472 128.80539 24.755062L128.80539 24.755062L128.80539 74.57214L124.01822 74.57214C 122.91841 75.528824 121.66418 76.62486 120.29319 77.816956L120.29319 77.816956L132.58127 91.1295L132.0709 91.51933L132.19144 91.51933L132.19144 101.688774L136.52287 101.688774C 137.69424 99.66995 139.85619 98.298965 142.35901 98.298965C 146.10287 98.298965 149.14052 101.33473 149.14052 105.080475C 149.14052 108.82246 146.10475 111.86011 142.35901 111.86011C 139.8562 111.86011 137.69424 110.487236 136.52287 108.4703L136.52287 108.4703L132.19144 108.4703L132.19144 120.08606L142.73376 131.50407C 153.07082 118.715065 159.30995 102.47408 159.30995 84.74159C 159.30995 73.850876 156.92578 63.542072 152.72241 54.235153L152.72241 54.235153L145.75069 54.235153L145.75069 75.51189C 147.7695 76.6889 149.1405 78.84897 149.1405 81.35178C 149.1405 85.09564 146.10474 88.129524 142.36087 88.129524zM84.73593 10.167557C 82.44969 10.167557 80.19358 10.310687 77.96007 10.512187L77.96007 10.512187L77.96007 31.954647L86.276405 40.964016C 86.86774 40.794525 87.481674 40.675884 88.12573 40.675884C 91.86959 40.675884 94.90724 43.711655 94.90724 47.457394C 94.90724 51.201252 91.871475 54.237022 88.12573 54.237022C 84.37999 54.237022 81.34799 51.201252 81.34799 47.457394C 81.34799 46.901844 81.430855 46.367004 81.55703 45.850994L81.55703 45.850994L76.78304 40.675873L73.171005 40.675873L61.212494 51.101433L70.36499 61.01287L71.17666 61.01287L71.17666 61.894222L71.210556 61.93188L71.17666 61.96201L71.17666 105.08233L106.018295 105.08233C 107.18967 103.061615 109.351616 101.69063 111.85442 101.69063C 115.59828 101.69063 118.63593 104.726395 118.63593 108.47214C 118.63593 112.21788 115.60017 115.24988 111.85442 115.24988C 109.351616 115.24988 107.18967 113.87701 106.018295 111.861946L106.018295 111.861946L71.18042 111.861946L67.79061 111.861946L64.4008 111.861946L64.4008 64.54391L33.310562 30.866108C 31.427332 32.660828 29.653332 34.56289 27.964073 36.54405L27.964073 36.54405L56.8095 67.79248L57.62117 67.79248L57.62117 68.67195L57.65507 68.70961L57.62117 68.73974L57.62117 108.47024L57.62117 115.24798L50.84343 115.24798L33.89436 115.24798L30.50831 115.24798L27.1185 115.24798L27.1185 98.30079L11.43684 98.30079C 16.60442 126.3515 37.46873 148.8787 64.59854 156.48506L64.59854 156.48506L60.785 165.99348C 25.657108 155.64702 -1.1444092E-05 123.214035 -1.1444092E-05 84.74152C -1.1444092E-05 37.939495 37.939537 0.0018234253 84.73781 0.0018234253C 89.34607 0.0018234253 93.835686 0.46886343 98.24434 1.1769634L98.24434 1.1769634L94.36112 10.860534C 91.20294 10.453753 88.0071 10.167503 84.73593 10.167503zM23.736233 41.960247C 15.197673 54.059998 10.165672 68.79816 10.165672 84.74159C 10.165672 87.02783 10.284312 89.28394 10.487702 91.51933L10.487702 91.51933L33.892483 91.51933L33.892483 94.91103L33.892483 98.30084L33.892483 108.47028L50.841553 108.47028L50.841553 71.32358L40.244614 59.845287L30.393433 73.48929C 30.451813 73.84334 30.506422 74.20115 30.506422 74.57215C 30.506422 78.31601 27.470652 81.35178 23.726791 81.35178C 19.982931 81.35178 16.94716 78.31602 16.94716 74.57215C 16.94716 70.828285 19.982931 67.79252 23.726791 67.79252C 24.481972 67.79252 25.19383 67.94506 25.871792 68.17293L25.871792 68.17293L35.555363 54.764328L23.736214 41.960247zM71.18044 11.459457C 58.94509 13.709917 47.794495 18.988607 38.432953 26.416067L38.432953 26.416067L56.617424 46.11654L71.18044 33.4198L71.18044 11.459459z\" stroke=\"none\" fill=\"#46D2EB\" fill-rule=\"nonzero\" /> <path d=\"M237.59679 43.7453L239.58821 43.7453C 249.39786 43.7453 254.30267 48.318214 254.30266 57.464043L254.30266 57.464043L254.30266 98.95216L235.4947 98.95216L235.4947 60.008644C 235.49472 58.386 234.57275 57.574677 232.72884 57.574673L232.72884 57.574673L230.84805 57.574673C 228.85664 57.574677 227.86093 58.681026 227.86092 60.893723L227.86092 60.893723L227.86092 98.95216L209.05296 98.95216L209.05296 60.008644C 209.05298 58.386 208.13101 57.574677 206.2871 57.574673L206.2871 57.574673L204.40631 57.574673C 202.41489 57.574677 201.41917 58.681026 201.41917 60.893723L201.41917 60.893723L201.41917 98.95216L182.61122 98.95216L182.61122 43.745304L201.08727 43.745304L201.08727 50.825943C 202.11987 46.10552 205.4758 43.745304 211.15504 43.745304L211.15504 43.745304L213.14647 43.745304C 220.00584 43.745304 224.505 46.068638 226.64394 50.715305C 227.23401 48.576366 228.52475 46.879963 230.51616 45.626095C 232.5076 44.372234 234.8678 43.745304 237.5968 43.745304L237.5968 43.745304L237.59679 43.7453zM277.7573 43.7453L291.25476 43.7453C 302.097 43.7453 307.5181 48.318214 307.5181 57.464043L307.5181 57.464043L307.5181 76.27199L279.30618 76.27199L279.30618 83.5739C 279.3062 85.19655 280.22815 86.007866 282.07205 86.007866L282.07205 86.007866L286.94 86.007866C 288.78394 86.007866 289.70587 85.12279 289.70587 83.35262L289.70587 83.35262L289.70587 80.254845L307.5181 80.254845L307.5181 85.23341C 307.5181 94.37925 302.097 98.95216 291.25476 98.952156L291.25476 98.952156L277.7573 98.952156C 266.9151 98.95216 261.49396 94.37925 261.49396 85.23341L261.49396 85.23341L261.49396 57.464035C 261.49396 48.318207 266.9151 43.745293 277.7573 43.745293zM279.30618 59.123558L279.30618 66.09356L289.70587 66.09356L289.70587 59.123558C 289.70587 57.500916 288.78394 56.68959 286.94 56.689587L286.94 56.689587L282.07205 56.689587C 280.22815 56.68959 279.3062 57.500916 279.30618 59.123558zM340.3767 50.82594L340.3767 43.7453L358.85275 43.7453L358.85275 98.95216L340.0448 98.95216L340.0448 92.977875C 338.64343 96.96074 335.39813 98.95216 330.30893 98.95216L330.30893 98.95216L328.3175 98.95216C 318.50787 98.95216 313.60306 94.37925 313.60306 85.23342L313.60306 85.23342L313.60306 57.464043C 313.60306 48.318214 318.50787 43.7453 328.3175 43.7453L328.3175 43.7453L330.30893 43.7453C 335.98822 43.7453 339.34412 46.105515 340.3767 50.82594zM335.17688 85.12279L337.05768 85.12279C 339.04913 85.12279 340.04486 84.01644 340.04483 81.803734L340.04483 81.803734L340.04483 60.893723C 340.04486 58.681026 339.04913 57.574677 337.05768 57.574673L337.05768 57.574673L335.17688 57.574673C 333.33298 57.574677 332.411 58.386 332.411 60.008644L332.411 60.008644L332.411 82.68881C 332.411 84.31146 333.33298 85.12278 335.17688 85.12278zM395.6942 43.745296L395.6942 57.574665L388.7242 57.574665L388.7242 82.688805C 388.7242 84.31145 389.64618 85.12277 391.49008 85.12277L391.49008 85.12277L395.58356 85.12277L395.58356 98.95214L386.1796 98.95214C 375.33737 98.95214 369.91626 94.37923 369.91626 85.2334L369.91626 85.2334L369.91626 57.574646L363.83133 57.574646L363.83133 43.745277L369.91626 43.745277L369.91626 32.903046L388.7242 32.903046L388.7242 43.745277L395.6942 43.745277z\" stroke=\"none\" fill=\"#24478F\" fill-rule=\"nonzero\" /> <path d=\"M405.7817 105.1253L405.7817 29.89318L440.58536 29.89318C 452.0694 29.89318 457.8114 34.736786 457.8114 44.424L457.8114 44.424L457.8114 65.985855C 457.8114 75.67307 452.0694 80.51668 440.58536 80.51668L440.58536 80.51668L426.2889 80.51668L426.2889 105.12532L405.7817 105.12532L405.7817 105.1253zM437.3042 63.290607L437.3042 47.119217C 437.3042 45.40052 436.32767 44.54117 434.3746 44.54117L434.3746 44.54117L426.2889 44.54117L426.2889 65.86866L434.3746 65.86866C 436.32767 65.86866 437.3042 65.00931 437.3042 63.29061zM485.11526 40.791275L464.49088 40.791275L464.49088 26.729195L485.11526 26.729195L485.11526 40.791275zM484.7637 46.650475L484.7637 105.12529L464.8424 105.12529L464.8424 46.65048L484.76367 46.65048z\" stroke=\"none\" fill=\"#24478F\" fill-rule=\"nonzero\" /> <path d=\"M189.8343 113.4776L195.82834 113.4776C 200.64325 113.4776 203.05069 115.50837 203.05067 119.56991L203.05067 119.56991L203.05067 127.92227L190.52214 127.92227L190.52214 131.16495C 190.52214 131.88554 190.93158 132.24585 191.75043 132.24583L191.75043 132.24583L193.91222 132.24583C 194.73108 132.24585 195.1405 131.85278 195.1405 131.06668L195.1405 131.06668L195.1405 129.691L203.05067 129.691L203.05067 131.90192C 203.05069 135.96347 200.64325 137.99423 195.82834 137.99423L195.82834 137.99423L189.8343 137.99423C 185.01942 137.99423 182.61197 135.96347 182.61197 131.90192L182.61197 131.90192L182.61197 119.56991C 182.61197 115.50837 185.01942 113.4776 189.8343 113.4776zM190.52214 120.30688L190.52214 123.40216L195.1405 123.40216L195.1405 120.30688C 195.1405 119.58629 194.73108 119.22599 193.91222 119.22599L193.91222 119.22599L191.75043 119.22599C 190.93158 119.22599 190.52214 119.58629 190.52214 120.30688zM214.59659 105.125244L214.59659 137.99422L206.24423 137.99422L206.24423 105.125244L214.59659 105.125244zM225.25813 113.4776L231.25217 113.4776C 236.06706 113.4776 238.4745 115.50837 238.4745 119.56991L238.4745 119.56991L238.4745 127.92227L225.94597 127.92227L225.94597 131.16495C 225.94597 131.88554 226.35541 132.24585 227.17426 132.24583L227.17426 132.24583L229.33604 132.24583C 230.1549 132.24585 230.56435 131.85278 230.56433 131.06668L230.56433 131.06668L230.56433 129.691L238.4745 129.691L238.4745 131.90192C 238.4745 135.96347 236.06706 137.99423 231.25217 137.99423L231.25217 137.99423L225.25813 137.99423C 220.44325 137.99423 218.03581 135.96347 218.0358 131.90192L218.0358 131.90192L218.0358 119.56991C 218.03581 115.50837 220.44325 113.4776 225.25813 113.4776zM225.94597 120.30688L225.94597 123.40216L230.56433 123.40216L230.56433 120.30688C 230.56435 119.58629 230.1549 119.22599 229.33604 119.22599L229.33604 119.22599L227.17426 119.22599C 226.35541 119.22599 225.94597 119.58629 225.94597 120.30688zM260.87848 119.56991L260.87848 122.95998L252.8209 122.95998L252.8209 120.69993C 252.8209 119.97934 252.41148 119.61905 251.59262 119.61904L251.59262 119.61904L250.75739 119.61904C 249.93854 119.61905 249.52911 119.97934 249.5291 120.69993L249.5291 120.69993L249.5291 130.77188C 249.52911 131.49248 249.93854 131.85277 250.75739 131.85277L250.75739 131.85277L251.59262 131.85277C 252.41148 131.85277 252.8209 131.49248 252.8209 130.77188L252.8209 130.77188L252.8209 128.51183L260.87848 128.51183L260.87848 131.9019C 260.87848 135.96344 258.47104 137.99422 253.65614 137.99422L253.65614 137.99422L248.39908 137.99422C 243.5842 137.99422 241.17676 135.96344 241.17674 131.9019L241.17674 131.9019L241.17674 119.56989C 241.17676 115.50836 243.5842 113.477585 248.39908 113.477585L248.39908 113.477585L253.65614 113.477585C 258.47104 113.477585 260.87848 115.50836 260.87848 119.56989zM276.25665 113.4776L276.25665 119.61904L273.16138 119.61904L273.16138 130.7719C 273.16138 131.4925 273.5708 131.85278 274.38968 131.85278L274.38968 131.85278L276.20755 131.85278L276.20755 137.99422L272.03137 137.99422C 267.2165 137.99422 264.80905 135.96344 264.80905 131.9019L264.80905 131.9019L264.80905 119.61902L262.1068 119.61902L262.1068 113.47758L264.80905 113.47758L264.80905 108.66269L273.1614 108.66269L273.1614 113.47758L276.25668 113.47758zM292.2244 113.23194L292.96136 113.23194L292.96136 120.5034L290.16086 120.5034C 288.09735 120.5034 287.06558 121.61705 287.06558 123.844345L287.06558 123.844345L287.06558 137.99422L278.71323 137.99422L278.71323 113.4776L286.91818 113.4776L286.91818 117.26073C 287.1802 116.01607 287.76978 115.03343 288.68692 114.312836C 289.60403 113.59225 290.7832 113.23195 292.2244 113.23195zM301.90332 113.4776L308.143 113.4776C 310.63232 113.4776 312.41745 114.00167 313.49832 115.04981C 314.57922 116.09795 315.11966 117.60466 315.11966 119.569916L315.11966 119.569916L315.11966 131.90193C 315.11966 133.8672 314.57922 135.3739 313.49832 136.42203C 312.41745 137.47017 310.63232 137.99423 308.143 137.99423L308.143 137.99423L301.90332 137.99423C 299.414 137.99423 297.6289 137.47017 296.548 136.42203C 295.4671 135.3739 294.92667 133.8672 294.92667 131.90193L294.92667 131.90193L294.92667 119.56992C 294.92667 117.60467 295.4671 116.09796 296.548 115.04982C 297.6289 114.00168 299.414 113.477615 301.90332 113.47761zM306.91473 131.11581L306.91473 120.35602C 306.91473 119.63543 306.5053 119.27513 305.68643 119.27513L305.68643 119.27513L304.3599 119.27513C 303.54105 119.27513 303.1316 119.63543 303.1316 120.35602L303.1316 120.35602L303.1316 131.11581C 303.1316 131.83641 303.54105 132.1967 304.3599 132.1967L304.3599 132.1967L305.68643 132.1967C 306.5053 132.1967 306.91473 131.83641 306.91473 131.11581zM331.48047 113.4776L332.36484 113.4776C 336.7212 113.4776 338.89935 115.50837 338.89932 119.56991L338.89932 119.56991L338.89932 137.99422L330.54697 137.99422L330.54697 120.69993C 330.547 119.97934 330.13754 119.61905 329.31866 119.61904L329.31866 119.61904L328.48343 119.61904C 327.59906 119.61905 327.1569 120.11037 327.1569 121.092995L327.1569 121.092995L327.1569 137.99423L318.80453 137.99423L318.80453 113.477615L327.0095 113.477615L327.0095 116.62203C 327.46805 114.52576 328.95837 113.47762 331.48047 113.477615zM351.08392 111.02102L342.4368 111.02102L342.4368 105.12524L351.08392 105.12524L351.08392 111.02102zM350.93652 113.4776L350.93652 137.99422L342.58417 137.99422L342.58417 113.4776L350.93652 113.4776zM374.07745 119.56991L374.07745 122.95998L366.0199 122.95998L366.0199 120.69993C 366.0199 119.97934 365.61047 119.61905 364.7916 119.61904L364.7916 119.61904L363.95636 119.61904C 363.1375 119.61905 362.72806 119.97934 362.72806 120.69993L362.72806 120.69993L362.72806 130.77188C 362.72806 131.49248 363.1375 131.85277 363.95636 131.85277L363.95636 131.85277L364.7916 131.85277C 365.61047 131.85277 366.0199 131.49248 366.0199 130.77188L366.0199 130.77188L366.0199 128.51183L374.07745 128.51183L374.07745 131.9019C 374.07745 135.96344 371.67004 137.99422 366.85513 137.99422L366.85513 137.99422L361.59805 137.99422C 356.78317 137.99422 354.37573 135.96344 354.37573 131.9019L354.37573 131.9019L354.37573 119.56989C 354.37573 115.50836 356.78317 113.477585 361.59805 113.477585L361.59805 113.477585L366.85513 113.477585C 371.67004 113.477585 374.07745 115.50836 374.07745 119.56989zM387.58862 131.50885L387.58862 129.5436C 387.58862 128.823 387.1792 128.4627 386.36032 128.4627L386.36032 128.4627L383.3633 128.4627C 378.6467 128.4627 376.2884 126.44832 376.28836 122.41954L376.28836 122.41954L376.28836 119.56991C 376.2884 115.50837 378.6958 113.4776 383.51068 113.4776L383.51068 113.4776L388.07993 113.4776C 392.89484 113.4776 395.30228 115.50837 395.30225 119.56991L395.30225 119.56991L395.30225 121.63343L387.34296 121.63343L387.34296 119.96296C 387.34296 119.24237 386.93353 118.88207 386.11465 118.88207L386.11465 118.88207L385.62332 118.88207C 384.80447 118.88207 384.39505 119.24237 384.39502 119.96296L384.39502 119.96296L384.39502 122.12475C 384.39505 122.845345 384.80447 123.20564 385.62332 123.205635L385.62332 123.205635L388.62033 123.205635C 391.14246 123.20564 392.95212 123.72152 394.04935 124.75327C 395.14664 125.785034 395.69525 127.267166 395.69525 129.19968L395.69525 129.19968L395.69525 131.9019C 395.69525 135.96344 393.28784 137.99422 388.47293 137.99422L388.47293 137.99422L383.65805 137.99422C 378.84317 137.99422 376.43573 135.96344 376.43573 131.9019L376.43573 131.9019L376.43573 129.88751L384.54242 129.88751L384.54242 131.50885C 384.54245 132.22945 384.95187 132.58974 385.77072 132.58974L385.77072 132.58974L386.3603 132.58974C 387.17917 132.58974 387.5886 132.22945 387.5886 131.50885z\" stroke=\"none\" fill=\"#24478F\" fill-rule=\"nonzero\" /></g></g></g></g></svg>"};

extern const unsigned char homepage_start[] asm("_binary_homepage_html_start");
extern const unsigned char homepage_end[]   asm("_binary_homepage_html_end");

static char can_datarate_str[11][7] = {
								"5k",
								"10K",
								"20K",
								"25K",
								"50K",
								"100K",
								"125K",
								"250K",
								"500K",
								"800K",
								"1000K",
};

const char device_config_default[] = "{\"wifi_mode\":\"AP\",\"ap_ch\":\"6\",\"sta_ssid\":\"MeatPi\",\"sta_pass\":\"TomatoSauce\",\"sta_security\":\"wpa3\",\"can_datarate\":\"500K\",\"can_mode\":\"normal\",\"port_type\":\"tcp\",\"port\":\"3333\",\"ap_pass\":\"@meatpi#\",\"protocol\":\"slcan\",\"ble_pass\":\"123456\",\"ble_status\":\"disable\",\"sleep_status\":\"disable\",\"sleep_volt\":\"13.1\",\"wakeup_volt\":\"13.5\",\"batt_alert\":\"disable\",\"batt_alert_ssid\":\"MeatPi\",\"batt_alert_pass\":\"TomatoSauce\",\"batt_alert_volt\":\"11.0\",\"batt_alert_protocol\":\"mqtt\",\"batt_alert_url\":\"mqtt://mqtt.eclipseprojects.io\",\"batt_alert_port\":\"1883\",\"batt_alert_topic\":\"CAR1/voltage\",\"batt_mqtt_user\":\"meatpi\",\"batt_mqtt_pass\":\"meatpi\",\"batt_alert_time\":\"1\",\"mqtt_en\":\"disable\",\"mqtt_elm327_log\":\"disable\",\"mqtt_url\":\"mqtt://127.0.0.1\",\"mqtt_port\":\"1883\",\"mqtt_user\":\"meatpi\",\"mqtt_pass\":\"meatpi\",\"keep_alive\":\"30\",\"mqtt_tx_topic\":\"wican/%s/can/tx\",\"mqtt_rx_topic\":\"wican/%s/can/rx\",\"mqtt_status_topic\":\"wican/%s/can/status\"}";
static device_config_t device_config;
TimerHandle_t xrestartTimer;

/* Max length a file path can have on storage */
#define FILE_PATH_MAX (ESP_VFS_PATH_MAX + CONFIG_SPIFFS_OBJ_NAME_LEN)
/* Scratch buffer size */
#define SCRATCH_BUFSIZE  4096

#define MAX_FILE_SIZE   (2000*1024) // 200 KB
#define MAX_FILE_SIZE_STR "200KB"

struct file_server_data {
    /* Base path of file storage */
    char base_path[ESP_VFS_PATH_MAX + 1];

    /* Scratch buffer for temporary storage during file transfer */
    char scratch[SCRATCH_BUFSIZE];
};


/* Copies the full path into destination buffer and returns
 * pointer to path (skipping the preceding base path) */
static const char* get_path_from_uri(char *dest, const char *base_path, const char *uri, size_t destsize)
{
    const size_t base_pathlen = strlen(base_path);
    size_t pathlen = strlen(uri);

    const char *quest = strchr(uri, '?');
    if (quest) {
        pathlen = MIN(pathlen, quest - uri);
    }
    const char *hash = strchr(uri, '#');
    if (hash) {
        pathlen = MIN(pathlen, hash - uri);
    }

    if (base_pathlen + pathlen + 1 > destsize) {
        /* Full path string won't fit into destination buffer */
        return NULL;
    }

    /* Construct full path (base + path) */
    strcpy(dest, base_path);
    strlcpy(dest + base_pathlen, uri, pathlen + 1);

    /* Return pointer to path, skipping the base */
    return dest + base_pathlen;
}

int8_t config_server_get_wifi_mode(void)
{
	if(strcmp(device_config.wifi_mode, "AP") == 0)
	{
		return AP_MODE;
	}
	else if(strcmp(device_config.wifi_mode, "APStation") == 0)
	{
		return APSTA_MODE;
	}
	return -1;
}

int8_t config_server_get_ap_ch(void)
{
	int ch_val = atoi(device_config.ap_ch);

	if(ch_val > 0 && ch_val < 15)
	{
		return ch_val;
	}
	return -1;
}

char *config_server_get_sta_ssid(void)
{
	return device_config.sta_ssid;
}

char *config_server_get_ap_pass(void)
{
	return device_config.ap_pass;
}

int config_server_ble_pass(void)
{
	int ble_pass = atoi(device_config.ble_pass);

	if(ble_pass > 0 && ble_pass <= 999999)
	{
		return ble_pass;
	}
	return -1;
}

char *config_server_get_sta_pass(void)
{
	return device_config.sta_pass;
}
int8_t config_server_protocol(void)
{
	if(strcmp(device_config.protocol, "slcan") == 0)
	{
		return SLCAN;
	}
	else if(strcmp(device_config.protocol, "realdash66") == 0)
	{
		return REALDASH;
	}
	else if(strcmp(device_config.protocol, "savvycan") == 0)
	{
		return SAVVYCAN;
	}
	else if(strcmp(device_config.protocol, "elm327") == 0)
	{
		return OBD_ELM327;
	}
	else if(strcmp(device_config.protocol, "auto_pid") == 0)
	{
		return AUTO_PID;
	}
	return SLCAN;
}

int8_t config_server_get_can_rate(void)
{
	ESP_LOGI(TAG, "device_config.can_datarate:%s", device_config.can_datarate);
	if(strcmp(device_config.can_datarate, "5K") == 0)
	{
		return CAN_5K;
	}
	if(strcmp(device_config.can_datarate, "10K") == 0)
	{
		return CAN_10K;
	}
	if(strcmp(device_config.can_datarate, "20K") == 0)
	{
		return CAN_20K;
	}
	if(strcmp(device_config.can_datarate, "25K") == 0)
	{
		return CAN_25K;
	}
	else if(strcmp(device_config.can_datarate, "50K") == 0)
	{
		return CAN_50K;
	}
	else if(strcmp(device_config.can_datarate, "100K") == 0)
	{
		return CAN_100K;
	}
	else if(strcmp(device_config.can_datarate, "125K") == 0)
	{
		return CAN_125K;
	}
	else if(strcmp(device_config.can_datarate, "250K") == 0)
	{
		return CAN_250K;
	}
	else if(strcmp(device_config.can_datarate, "500K") == 0)
	{
		return CAN_500K;
	}
	else if(strcmp(device_config.can_datarate, "800K") == 0)
	{
		return CAN_800K;
	}
	else if(strcmp(device_config.can_datarate, "1000K") == 0)
	{
		return CAN_1000K;
	}
	else if(strcmp(device_config.can_datarate, "auto") == 0)
	{
		return CAN_AUTO;
	}

	return -1;
}


int8_t config_server_get_can_mode(void)
{
	if(strcmp(device_config.can_mode, "normal") == 0)
	{
		return CAN_NORMAL;
	}
	else if(strcmp(device_config.can_mode, "silent") == 0)
	{
		return CAN_SILENT;
	}
	return -1;
}

int8_t config_server_get_port_type(void)
{
	if(strcmp(device_config.port_type, "tcp") == 0)
	{
		return TCP_PORT;
	}
	else if(strcmp(device_config.port_type, "udp") == 0)
	{
		return UDP_PORT;
	}
	return -1;
}

int32_t config_server_get_port(void)
{
	int port_val = atoi(device_config.port);

	if(port_val > 0 && port_val <= 65535)
	{
		return port_val;
	}
	return -1;
}

static esp_err_t index_handler(httpd_req_t *req)
{
    httpd_resp_set_type(req, "text/html");
    
    const size_t homepage_size = homepage_end - homepage_start;
    
    esp_err_t ret = httpd_resp_send(req, (const char*)homepage_start, homepage_size);
    
    return (ret == ESP_OK) ? ESP_OK : ESP_FAIL;
}


static esp_err_t store_config_handler(httpd_req_t *req)
{
    char *buf = NULL;
    size_t buf_size = req->content_len;

    if (buf_size <= 0)
    {
        return ESP_FAIL; // Invalid content length
    }

    buf = (char *)malloc(buf_size);
    if (!buf)
    {
        return ESP_ERR_NO_MEM; // Memory allocation failure
    }

    int ret = httpd_req_recv(req, buf, buf_size);

    if (ret <= 0)
    {
        if (ret == HTTPD_SOCK_ERR_TIMEOUT)
        {
            // Retry receiving if timeout occurred
            free(buf);
            return ESP_FAIL;
        }
        // Handle read error
        free(buf);
        return ESP_FAIL;
    }

    FILE *f = fopen(FS_MOUNT_POINT"/config.json", "w");
    if (f)
    {
        // Write the received data into the file
        fwrite(buf, 1, buf_size, f);
        fclose(f);
    }
    else
    {
        // Handle file open error
        free(buf);
        return ESP_FAIL;
    }

    // Free dynamically allocated memory
    free(buf);

    const char *resp_str = "Configuration saved! Rebooting...";
    httpd_resp_send(req, resp_str, HTTPD_RESP_USE_STRLEN);

    xTimerStart( xrestartTimer, 0 );
    return ESP_OK;
}

static esp_err_t store_canflt_handler(httpd_req_t *req)
{
    char *buf = NULL;
    size_t buf_size = req->content_len;

    if (buf_size <= 0)
    {
        return ESP_FAIL; // Invalid content length
    }

    buf = (char *)malloc(buf_size);
    if (!buf)
    {
        return ESP_ERR_NO_MEM; // Memory allocation failure
    }

    int ret = httpd_req_recv(req, buf, buf_size);

    if (ret <= 0)
    {
        if (ret == HTTPD_SOCK_ERR_TIMEOUT)
        {
            // Retry receiving if timeout occurred
            free(buf);
            return ESP_FAIL;
        }
        // Handle read error
        free(buf);
        return ESP_FAIL;
    }

    FILE *f = fopen(FS_MOUNT_POINT"/mqtt_canfilt.json", "w");
    if (f)
    {
        // Write the received data into the file
        fwrite(buf, 1, buf_size, f);
        fclose(f);
    }
    else
    {
        // Handle file open error
        free(buf);
        return ESP_FAIL;
    }

    // Free dynamically allocated memory
    free(buf);

	free(mqtt_canflt_file);
	mqtt_canflt_file = NULL;
	FILE* f1 = fopen(FS_MOUNT_POINT"/mqtt_canfilt.json", "r");
	if (f1 != NULL)
	{
		fseek(f1, 0, SEEK_END);
		int32_t filesize = ftell(f1);
		fseek(f1, 0, SEEK_SET);
		mqtt_canflt_file = malloc(filesize+1);
		ESP_LOGI(__func__, "mqtt_canflt_file File size: %ld", filesize);
		fseek(f1, 0, SEEK_SET);
		fread(mqtt_canflt_file, sizeof(char), filesize, f1);
		mqtt_canflt_file[filesize] = 0;
		fseek(f1, 0, SEEK_SET);
		ESP_LOGI(TAG, "mqtt_canfilt.json: %s", mqtt_canflt_file);
	}
    const char *resp_str = "CAN filter saved! Filter will take effect after submit.";
    httpd_resp_send(req, resp_str, HTTPD_RESP_USE_STRLEN);

    return ESP_OK;
}

static esp_err_t load_canflt_handler(httpd_req_t *req)
{
	if(mqtt_canflt_file != NULL)
	{
		const char* resp_str = (const char*)mqtt_canflt_file;
		httpd_resp_set_type(req, "application/json");
		httpd_resp_send(req, (const char*)resp_str, HTTPD_RESP_USE_STRLEN);
		ESP_LOGI(TAG, "mqtt_canflt_file: %s", mqtt_canflt_file);
	}
	else
	{
		const char* resp_str = (const char*) "NONE";
		httpd_resp_send(req, (const char*)resp_str, HTTPD_RESP_USE_STRLEN);
		ESP_LOGI(TAG, "mqtt_canflt_file: NONE");
	}

    return ESP_OK;
}

static esp_err_t load_pid_auto_handler(httpd_req_t *req)
{
    FILE *f = fopen(FS_MOUNT_POINT"/auto_pid.json", "r");
    if (f == NULL) 
	{
        const char* resp_str = "NONE";
        httpd_resp_send(req, resp_str, HTTPD_RESP_USE_STRLEN);
        ESP_LOGI(TAG, "auto_pid.json: NONE");
        return ESP_OK;
    }

    fseek(f, 0, SEEK_END);
    long filesize = ftell(f);
    fseek(f, 0, SEEK_SET);

    char *buf = malloc(filesize + 1);
    if (!buf)
	{
        fclose(f);
        httpd_resp_send_500(req);
        return ESP_FAIL;
    }

    size_t read = fread(buf, 1, filesize, f);
    fclose(f);
    
    if (read != filesize)
	{
        free(buf);
        httpd_resp_send_500(req);
        return ESP_FAIL;
    }

    buf[filesize] = 0;
    ESP_LOGI(TAG, "auto_pid.json: %s", buf);
    
	httpd_resp_set_type(req, "application/json");
    httpd_resp_send(req, buf, HTTPD_RESP_USE_STRLEN);
    free(buf);

    return ESP_OK;
}


static esp_err_t load_pid_auto_config_handler(httpd_req_t *req)
{
    const char *filepath = FS_MOUNT_POINT"/car_data.json";
    ESP_LOGI(TAG, "Opening file: %s", filepath);
    FILE *fd = fopen(filepath, "r");

    if (fd == NULL)
    {
        ESP_LOGE(TAG, "File does not exist: %s", filepath);
        httpd_resp_send(req, "NONE", HTTPD_RESP_USE_STRLEN);
        return ESP_OK;
    }

    // Seek to the end of the file to determine its size
    fseek(fd, 0, SEEK_END);
    long file_size = ftell(fd);
    rewind(fd);

    if (file_size <= 0)
    {
        ESP_LOGE(TAG, "File is empty or invalid: %s", filepath);
        fclose(fd);
        httpd_resp_send(req, "NONE", HTTPD_RESP_USE_STRLEN);
        return ESP_OK;
    }

    ESP_LOGI(TAG, "File size: %ld bytes", file_size);

    // Allocate memory on the heap to hold the file content
    char *buf = (char *)malloc(file_size + 1);
    if (buf == NULL)
    {
        ESP_LOGE(TAG, "Failed to allocate memory for file content");
        fclose(fd);
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Memory allocation failed");
        return ESP_FAIL;
    }

    // Read the file into the buffer
    size_t read_len = fread(buf, 1, file_size, fd);
    fclose(fd);

    if (read_len != file_size)
    {
        ESP_LOGE(TAG, "Failed to read the entire file. Read %zu bytes out of %ld", read_len, file_size);
        free(buf);
        httpd_resp_send(req, "NONE", HTTPD_RESP_USE_STRLEN);
        return ESP_OK;
    }

    // Find the last closing brace and terminate the string there
    char *last_brace = strrchr(buf, '}');
    if (last_brace != NULL) {
        *(last_brace + 1) = '\0';  // Terminate string right after the last }
        read_len = last_brace - buf + 1;  // Update length to new size
    }

    ESP_LOGI(TAG, "Sending response: %s", buf);
    httpd_resp_set_type(req, "application/json");
    httpd_resp_send(req, buf, read_len);  // Use actual content length instead of HTTPD_RESP_USE_STRLEN
    
    free(buf);
    return ESP_OK;
}

static esp_err_t load_config_handler(httpd_req_t *req)
{
    const char* resp_str = (const char*)device_config_file;
	httpd_resp_set_type(req, "application/json");
    httpd_resp_send(req, (const char*)resp_str, HTTPD_RESP_USE_STRLEN);
    ESP_LOGI(TAG, "device_config_file: %s", device_config_file);
	UBaseType_t stack_high_watermark = uxTaskGetStackHighWaterMark(NULL);
	ESP_LOGI(TAG, "Task stack high watermark: %u words", stack_high_watermark);
    return ESP_OK;
}

static esp_err_t load_car_config_handler(httpd_req_t *req)
{
    char *response_str = autopid_get_config();
    
    if (response_str) {
        ESP_LOGI(TAG, "Sending response: %s", response_str);
        httpd_resp_set_type(req, "application/json");
        httpd_resp_send(req, response_str, HTTPD_RESP_USE_STRLEN);
        free(response_str);  // Free the allocated string
    } else {
        ESP_LOGE(TAG, "Failed to generate JSON response");
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to generate JSON");
    }
    
    return ESP_OK;
}


static esp_err_t system_reboot_handler(httpd_req_t *req)
{
	const char *resp_str = "Configuration saved! Rebooting...";
    httpd_resp_send(req, resp_str, HTTPD_RESP_USE_STRLEN);

	ESP_LOGI(TAG, "reboot");
	xTimerStart( xrestartTimer, 0 );
    // esp_restart();
    return ESP_OK;
}

static esp_err_t logo_handler(httpd_req_t *req)
{
    const char* resp_str = (const char*)logo;
	httpd_resp_set_type(req, "image/svg+xml");
    httpd_resp_send(req, (const char*)resp_str, HTTPD_RESP_USE_STRLEN);
    return ESP_OK;
}

static esp_err_t store_auto_data_handler(httpd_req_t *req)
{
    if (!req)
	{
        return ESP_ERR_INVALID_ARG;
    }

    char *buf = NULL;
    size_t buf_size = req->content_len;
    esp_err_t ret = ESP_OK;

    // Validate content length
    if (buf_size <= 0 || buf_size > MAX_FILE_SIZE)
	{
        ESP_LOGE(TAG, "Invalid content length: %d", buf_size);
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Invalid content length");
        return ESP_FAIL;
    }

    // Allocate buffer with extra byte for null termination
    buf = (char *)calloc(1, buf_size + 1);
    if (!buf)
	{
        ESP_LOGE(TAG, "Memory allocation failed for size %d", buf_size + 1);
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Memory allocation failed");
        return ESP_ERR_NO_MEM;
    }

    // Receive data with timeout handling
    int received = httpd_req_recv(req, buf, buf_size);
    if (received <= 0)
	{
        ESP_LOGE(TAG, "Failed to receive data: %d", received);
        ret = ESP_FAIL;
        goto cleanup;
    }

    // Validate received data length
    if (received != buf_size)
	{
        ESP_LOGE(TAG, "Incomplete data received: %d/%d", received, buf_size);
        ret = ESP_FAIL;
        goto cleanup;
    }

    buf[received] = '\0';
    
    // Validate JSON format
    cJSON *json = cJSON_Parse(buf);
    if (!json)
	{
        ESP_LOGE(TAG, "Invalid JSON format");
        ret = ESP_FAIL;
        goto cleanup;
    }
    cJSON_Delete(json);

    ESP_LOGI(TAG, "Auto Table json: %s", buf);

    // Open file with error handling
    FILE *f = fopen(FS_MOUNT_POINT"/auto_pid.json", "w");
    if (!f)
	{
        ESP_LOGE(TAG, "Failed to open file for writing");
        ret = ESP_FAIL;
        goto cleanup;
    }

    // Write data with size verification
    size_t written = fwrite(buf, 1, received, f);
    if (written != received) 
	{
        ESP_LOGE(TAG, "File write failed: %d/%d bytes", written, received);
        fclose(f);
        ret = ESP_FAIL;
        goto cleanup;
    }

    fclose(f);

    // Send success response
    const char *resp_str = "Auto PID table will take effect after submit.";
    httpd_resp_send(req, resp_str, HTTPD_RESP_USE_STRLEN);

cleanup:
    if (buf)
	{
        free(buf);
    }
    
    if (ret != ESP_OK)
	{
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to store data");
    }

    return ret;
}


static esp_err_t store_car_data_handler(httpd_req_t *req)
{
    int total_len = req->content_len;
    int received = 0;
	const char *filepath = FS_MOUNT_POINT"/car_data.json";

    FILE *file = fopen(filepath, "w");
    if (!file)
    {
        ESP_LOGE(TAG, "Failed to open file for writing");
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to open file for writing");
        return ESP_FAIL;
    }

    static char buffer[16];
    while (received < total_len)
    {
        int ret = httpd_req_recv(req, buffer, sizeof(buffer));
        if (ret <= 0)
        {
            ESP_LOGE(TAG, "Failed to receive JSON data");
            fclose(file);
            httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to receive JSON data");
            return ESP_FAIL;
        }
        if (fwrite(buffer, 1, ret, file) != ret)
        {
            ESP_LOGE(TAG, "Failed to write data to file");
            fclose(file);
            httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to write data to file");
            return ESP_FAIL;
        }
        received += ret;
    }

    fclose(file);
    ESP_LOGI(TAG, "JSON data successfully stored");

    httpd_resp_send(req, "Data stored successfully", HTTPD_RESP_USE_STRLEN);
    return ESP_OK;
}

static esp_err_t check_status_handler(httpd_req_t *req)
{

	char ip_str[20] = {0};
	config_server_get_sta_ip(ip_str);
	cJSON *root = cJSON_CreateObject();
	static char fver[16];
	static char hver[32];
    const esp_partition_t *running = esp_ota_get_running_partition();
    static esp_app_desc_t running_app_info;
	uint32_t firmware_ver_minor = 0, firmware_ver_major = 0;

    if (esp_ota_get_partition_description(running, &running_app_info) != ESP_OK)
    {
    	running_app_info.version[0] = '1';
        ESP_LOGE(TAG, "Error getting partition_description");

    }


	if (sscanf(running_app_info.version, "v%ld.%ld", &firmware_ver_major, &firmware_ver_minor) == 2) 
	{
		ESP_LOGI(TAG, "Firmware version: %ld.%ld", firmware_ver_major, firmware_ver_minor);
	} 

    sprintf(fver, "%ld.%02ld", firmware_ver_major, firmware_ver_minor);
    sprintf(hver, "WiCAN-%s", HARDWARE_VERSION);

	cJSON_AddStringToObject(root, "wifi_mode", device_config.wifi_mode);
	cJSON_AddStringToObject(root, "ap_ch", device_config.ap_ch);
	cJSON_AddStringToObject(root, "sta_status", (wifi_network_is_connected()?"Connected":"Not Connected"));
	cJSON_AddStringToObject(root, "sta_ip", ip_str);
	cJSON_AddStringToObject(root, "mdns", wc_mdns_get_hostname());
	cJSON_AddStringToObject(root, "ble_status", device_config.ble_status);
//	cJSON_AddStringToObject(root, "can_datarate", device_config.can_datarate);
	cJSON_AddStringToObject(root, "can_datarate", can_datarate_str[can_get_bitrate()]);
	cJSON_AddStringToObject(root, "can_mode", device_config.can_mode);
	cJSON_AddStringToObject(root, "port_type", device_config.port_type);
	cJSON_AddStringToObject(root, "port", device_config.port);
	cJSON_AddStringToObject(root, "fw_version", fver);
	cJSON_AddStringToObject(root, "hw_version", hver);
	cJSON_AddStringToObject(root, "git_version", GIT_SHA);
	cJSON_AddStringToObject(root, "protocol", device_config.protocol);
	cJSON_AddStringToObject(root, "sleep_status", device_config.sleep_status);
	cJSON_AddStringToObject(root, "sleep_volt", device_config.sleep_volt);
	cJSON_AddStringToObject(root, "wakeup_volt", device_config.wakeup_volt);

	cJSON_AddStringToObject(root, "batt_alert", device_config.batt_alert);
	cJSON_AddStringToObject(root, "batt_alert_ssid", device_config.batt_alert_ssid);
	cJSON_AddStringToObject(root, "batt_alert_pass", device_config.batt_alert_pass);
	cJSON_AddStringToObject(root, "batt_alert_volt", device_config.batt_alert_volt);
	cJSON_AddStringToObject(root, "batt_alert_protocol", device_config.batt_alert_protocol);
	cJSON_AddStringToObject(root, "batt_alert_url", device_config.batt_alert_url);
	cJSON_AddStringToObject(root, "batt_alert_port", device_config.batt_alert_port);
	cJSON_AddStringToObject(root, "batt_alert_topic", device_config.batt_alert_topic);
	cJSON_AddStringToObject(root, "batt_alert_time", device_config.batt_alert_time);
	cJSON_AddStringToObject(root, "batt_mqtt_user", device_config.batt_mqtt_user);
	cJSON_AddStringToObject(root, "batt_mqtt_pass", device_config.batt_mqtt_pass);

	char volt[8]= {0};
	float tmp = 0;
	sleep_mode_get_voltage(&tmp);
	sprintf(volt, "%.1fV", tmp);
	cJSON_AddStringToObject(root, "batt_voltage", volt);

	cJSON_AddStringToObject(root, "mqtt_en", device_config.mqtt_en);
	cJSON_AddStringToObject(root, "mqtt_url", device_config.mqtt_url);
	cJSON_AddStringToObject(root, "mqtt_port", device_config.mqtt_port);
	cJSON_AddStringToObject(root, "mqtt_user", device_config.mqtt_user);
	cJSON_AddStringToObject(root, "mqtt_pass", device_config.mqtt_pass);
	cJSON_AddStringToObject(root, "mqtt_tx_topic", device_config.mqtt_tx_topic);
	cJSON_AddStringToObject(root, "mqtt_rx_topic", device_config.mqtt_rx_topic);
	cJSON_AddStringToObject(root, "mqtt_status_topic", device_config.mqtt_status_topic);
	cJSON_AddStringToObject(root, "device_id", device_id);
	cJSON_AddStringToObject(root, "sta_security", device_config.sta_security);
	
	if(autopid_get_ecu_status())
	{
		cJSON_AddStringToObject(root, "ecu_status", "online");
	}
	else
	{
		cJSON_AddStringToObject(root, "ecu_status", "offline");
	}
    const char *resp_str = cJSON_PrintUnformatted(root);

	httpd_resp_set_type(req, "application/json");
	httpd_resp_send(req, resp_str, HTTPD_RESP_USE_STRLEN);

    free((void *)resp_str);
    cJSON_Delete(root);
    return ESP_OK;
}

/*
 * Structure holding server handle
 * and internal socket fd in order
 * to use out of request send
 */
typedef  struct _async_resp_arg {
    httpd_handle_t hd;
    int fd;
}async_resp_arg_t;
static async_resp_arg_t rsp_arg;
/*
 * async send function, which we put into the httpd work queue
 */
//static void ws_async_send(void *arg)
//{
//    static const char * data = "Async data";
//    struct async_resp_arg *resp_arg = arg;
//    httpd_handle_t hd = resp_arg->hd;
//    int fd = resp_arg->fd;
//    httpd_ws_frame_t ws_pkt;
//    memset(&ws_pkt, 0, sizeof(httpd_ws_frame_t));
//    ws_pkt.payload = (uint8_t*)data;
//    ws_pkt.len = strlen(data);
//    ws_pkt.type = HTTPD_WS_TYPE_TEXT;
//
//    httpd_ws_send_frame_async(hd, fd, &ws_pkt);
//    free(resp_arg);
//}
//
//static esp_err_t trigger_async_send(httpd_handle_t handle, httpd_req_t *req)
//{
//    struct async_resp_arg *resp_arg = malloc(sizeof(struct async_resp_arg));
//    resp_arg->hd = req->handle;
//    resp_arg->fd = httpd_req_to_sockfd(req);
//    return httpd_queue_work(handle, ws_async_send, resp_arg);
//}

//static void ws_send(async_resp_arg_t resp, httpd_ws_frame_t *ws_pkt)
//{
//	httpd_ws_send_frame_async(resp.hd, resp.fd, ws_pkt);
//}

/*
 * This handler echos back the received ws data
 * and triggers an async send if certain message received
 */
static esp_err_t ws_handler(httpd_req_t *req)
{
    if (req->method == HTTP_GET)
    {
//        ESP_LOGI(TAG, "Handshake done, the new connection was opened");
        rsp_arg.hd = req->handle;
        rsp_arg.fd = httpd_req_to_sockfd(req);
//        tcp_server_suspend();
//        vTaskResume(xwebsocket_handle);
        gpio_set_level(ws_led, 0);
        xEventGroupSetBits( xServerEventGroup, WS_CONNECTED_BIT );
        return ESP_OK;
    }

    httpd_ws_frame_t ws_pkt;

    uint8_t *buf = NULL;
    memset(&ws_pkt, 0, sizeof(httpd_ws_frame_t));
    ws_pkt.type = HTTPD_WS_TYPE_TEXT;

    /* Set max_len = 0 to get the frame len */
    esp_err_t ret = httpd_ws_recv_frame(req, &ws_pkt, 0);
    if (ret != ESP_OK)
    {
        ESP_LOGE(TAG, "httpd_ws_recv_frame failed to get frame len with %d", ret);
        return ret;
    }
//    ESP_LOGI(TAG, "frame len is %d", ws_pkt.len);

    if (ws_pkt.len)
    {
        /* ws_pkt.len + 1 is for NULL termination as we are expecting a string */
        buf = calloc(1, ws_pkt.len + 1);
        if (buf == NULL)
        {
            ESP_LOGE(TAG, "Failed to calloc memory for buf");
            return ESP_ERR_NO_MEM;
        }
        ws_pkt.payload = buf;
        /* Set max_len = ws_pkt.len to get the frame payload */
        ret = httpd_ws_recv_frame(req, &ws_pkt, ws_pkt.len);
        if (ret != ESP_OK)
        {
            ESP_LOGE(TAG, "httpd_ws_recv_frame failed with %d", ret);
            free(buf);
            return ret;
        }
//        ESP_LOGI(TAG, "Got packet with message: %s", ws_pkt.payload);
    }
//    ESP_LOGI(TAG, "Packet type: %d", ws_pkt.type);

    static xdev_buffer rx_buffer;
    memcpy(rx_buffer.ucElement, ws_pkt.payload, ws_pkt.len);
    rx_buffer.dev_channel = DEV_WIFI_WS;
    rx_buffer.usLen = ws_pkt.len;

    xQueueSend( *xRX_Queue, ( void * ) &rx_buffer, portMAX_DELAY );
//    ws_send(rsp_arg, &ws_pkt);
//    if (ws_pkt.type == HTTPD_WS_TYPE_TEXT &&
//        strcmp((char*)ws_pkt.payload,"Trigger async") == 0)
//    {
//        free(buf);
//        return trigger_async_send(req->handle, req);
//    }
//
//    ret = httpd_ws_send_frame(req, &ws_pkt);
//    if (ret != ESP_OK)
//    {
//        ESP_LOGE(TAG, "httpd_ws_send_frame failed with %d", ret);
//    }
    free(buf);
    return ret;
}

/* Handler to upload a file onto the server */
static esp_err_t upload_post_handler(httpd_req_t *req)
{
    char filepath[FILE_PATH_MAX];
    uint32_t total_size = 0;

    if(config_server_get_ble_config())
    {
    	ble_disable();
    }
    can_disable();
    /* Skip leading "/upload" from URI to get filename */
    /* Note sizeof() counts NULL termination hence the -1 */
    const char *filename = get_path_from_uri(filepath, ((struct file_server_data *)req->user_ctx)->base_path,
                                             req->uri + sizeof("/upload") - 1, sizeof(filepath));
    if (!filename) {
        /* Respond with 500 Internal Server Error */
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Filename too long");
        return ESP_FAIL;
    }

    /* Filename cannot have a trailing '/' */
    if (filename[strlen(filename) - 1] == '/') {
        ESP_LOGE(TAG, "Invalid filename : %s", filename);
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Invalid filename");
        return ESP_FAIL;
    }

    /* File cannot be larger than a limit */
    if (req->content_len > MAX_FILE_SIZE)
    {
        ESP_LOGE(TAG, "File too large : %d bytes", req->content_len);
        /* Respond with 400 Bad Request */
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST,
                            "File size must be less than "
                            MAX_FILE_SIZE_STR "!");
        /* Return failure to close underlying connection else the
         * incoming file content will keep the socket busy */
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "Receiving file : %s...", filename);

    /* Retrieve the pointer to scratch buffer for temporary storage */
    char *buf = ((struct file_server_data *)req->user_ctx)->scratch;
    int received = 0;

    /* Content length of the request gives
     * the size of the file being uploaded */
    int remaining = req->content_len;

    ///
    esp_err_t err;
    /* update handle : set by esp_ota_begin(), must be freed via esp_ota_end() */
    esp_ota_handle_t update_handle = 0 ;
    const esp_partition_t *update_partition = NULL;

    ESP_LOGI(TAG, "Starting OTA");

    const esp_partition_t *configured = esp_ota_get_boot_partition();
    const esp_partition_t *running = esp_ota_get_running_partition();

    if (configured != running) {
        ESP_LOGW(TAG, "Configured OTA boot partition at offset 0x%08lx, but running from offset 0x%08lx",
                 configured->address, running->address);
        ESP_LOGW(TAG, "(This can happen if either the OTA boot data or preferred boot image become corrupted somehow.)");
    }
    ESP_LOGI(TAG, "Running partition type %d subtype %d (offset 0x%08lx)",
             running->type, running->subtype, running->address);

    update_partition = esp_ota_get_next_update_partition(NULL);
    assert(update_partition != NULL);
    ESP_LOGI(TAG, "Writing to partition subtype %d at offset 0x%lx",
             update_partition->subtype, update_partition->address);
    err = esp_ota_begin(update_partition, OTA_SIZE_UNKNOWN, &update_handle);
    if (err != ESP_OK)
    {
        ESP_LOGE(TAG, "esp_ota_begin failed (%s)", esp_err_to_name(err));
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "esp_ota_begin failed");
        return ESP_FAIL;
    }

	char *ret = 0;
	char *boundary_start = 0;
	char *boundary_end = 0;
	uint8_t count = 0;

    while (remaining > 0)
    {
//        ESP_LOGI(TAG, "Remaining size : %d", remaining);
        /* Receive the file part by part into a buffer */
        if ((received = httpd_req_recv(req, buf, MIN(remaining, SCRATCH_BUFSIZE))) <= 0)
        {
            if (received == HTTPD_SOCK_ERR_TIMEOUT)
            {
                /* Retry if timeout occurred */
                continue;
            }


            ESP_LOGE(TAG, "File reception failed!");
            /* Respond with 500 Internal Server Error */
            httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to receive file");
            return ESP_FAIL;
        }

        if(boundary_start == 0)
        {
        	boundary_start = buf;
        	ret = memchr(boundary_start, '\n', 200);
        	boundary_end = ret + 1;
        	remaining -= ((boundary_end - boundary_start) + 1 + 2 + 1);//ignore boundary at end of file
        	//TODO: Better way to do this ??
        	while(1)
        	{
        		if(((ret[0] == 'T') && (ret[1] == 'y') && (ret[2] == 'p') && (ret[3] == 'e') &&
        		        			(ret[4] == ':')))
        		{
        			break;
        		}
        		ret++;
        	}
    		ret = memchr(ret, '\n', 200);
    		buf = ret + 3;
    		remaining -= (buf - boundary_start);
    		ESP_LOGI(TAG, "Real Remaining size : %d", remaining);
    		
    		received -= (buf - boundary_start);
        }
        total_size += received;
        /* Write buffer content to file on storage */
        if (received && (ESP_OK != esp_ota_write( update_handle, (const void *)buf, received)))
        {
            ESP_LOGE(TAG, "File write failed!");
            esp_ota_abort(update_handle);
            /* Respond with 500 Internal Server Error */
            httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to write file to storage");
            return ESP_FAIL;
        }

        if(count < 3)
        {
        	count++;
        }
        /* Keep track of remaining size of
         * the file left to be uploaded */
        remaining -= received;
        vTaskDelay(5 / portTICK_PERIOD_MS);
    }

    /* Close file upon upload completion */
    ESP_LOGI(TAG, "File reception complete: %lu", total_size);

    if ((received = httpd_req_recv(req, buf, SCRATCH_BUFSIZE)) <= 0)
    {
        ESP_LOGE(TAG, "File reception failed!");
        esp_ota_abort(update_handle);
        /* Respond with 500 Internal Server Error */
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to receive file");
        return ESP_FAIL;
     }

    err = esp_ota_end(update_handle);
    if (err != ESP_OK)
    {
        if (err == ESP_ERR_OTA_VALIDATE_FAILED)
        {
            ESP_LOGE(TAG, "Image validation failed, image is corrupted");
        }
        ESP_LOGE(TAG, "esp_ota_end failed (%s)!", esp_err_to_name(err));
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "esp_ota_end failed");
        return ESP_FAIL;
    }

    err = esp_ota_set_boot_partition(update_partition);
    if (err != ESP_OK)
    {
        ESP_LOGE(TAG, "esp_ota_set_boot_partition failed (%s)!", esp_err_to_name(err));
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "esp_ota_set_boot_partition failed");
        return ESP_FAIL;
    }

    xTimerStart( xrestartTimer, 0 );

    /* Redirect onto root to see the updated file list */
    httpd_resp_set_status(req, "303 See Other");
    httpd_resp_set_hdr(req, "Location", "/");
#ifdef CONFIG_EXAMPLE_HTTPD_CONN_CLOSE_HEADER
    httpd_resp_set_hdr(req, "Connection", "close");
#endif
    httpd_resp_sendstr(req, "File uploaded successfully");
    return ESP_OK;
}

static esp_err_t upload_car_data_handler(httpd_req_t *req)
{
    char filepath[FILE_PATH_MAX];
    uint32_t total_size = 0;

    /* Skip leading "/upload" from URI to get filename */
    /* Note sizeof() counts NULL termination hence the -1 */
    const char *filename = get_path_from_uri(filepath, ((struct file_server_data *)req->user_ctx)->base_path,
                                             req->uri + sizeof("/upload") - 1, sizeof(filepath));
    if (!filename) {
        /* Respond with 500 Internal Server Error */
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Filename too long");
        return ESP_FAIL;
    }

    /* Filename cannot have a trailing '/' */
    if (filename[strlen(filename) - 1] == '/') {
        ESP_LOGE(TAG, "Invalid filename : %s", filename);
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Invalid filename");
        return ESP_FAIL;
    }

    /* File cannot be larger than a limit */
    if (req->content_len > MAX_FILE_SIZE)
    {
        ESP_LOGE(TAG, "File too large : %d bytes", req->content_len);
        /* Respond with 400 Bad Request */
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST,
                            "File size must be less than "
                            MAX_FILE_SIZE_STR "!");
        /* Return failure to close underlying connection else the
         * incoming file content will keep the socket busy */
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "Receiving file : %s...", filename);

    /* Retrieve the pointer to scratch buffer for temporary storage */
    char *buf = ((struct file_server_data *)req->user_ctx)->scratch;
    int received = 0;

    /* Content length of the request gives
     * the size of the file being uploaded */
    int remaining = req->content_len;
	char *ret = 0;
	char *boundary_start = 0;
	char *boundary_end = 0;
	uint8_t count = 0;


	FILE *fd = fopen(filepath, "w");
    if (fd == NULL)
    {
        ESP_LOGE(TAG, "Failed to create file : %s", filepath);
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to create file");
        return ESP_FAIL;
    }
	// remaining
    while (remaining > 0)
    {
//        ESP_LOGI(TAG, "Remaining size : %d", remaining);
        /* Receive the file part by part into a buffer */
        if ((received = httpd_req_recv(req, buf, MIN(remaining, SCRATCH_BUFSIZE))) <= 0)
        {
            if (received == HTTPD_SOCK_ERR_TIMEOUT)
            {
                /* Retry if timeout occurred */
                continue;
            }


            ESP_LOGE(TAG, "File reception failed!");
            fclose(fd);
            unlink(filepath);
            httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to receive file");
            return ESP_FAIL;
        }

        if(boundary_start == 0)
        {
        	boundary_start = buf;
        	ret = memchr(boundary_start, '\n', 200);
        	boundary_end = ret + 1;
        	remaining -= ((boundary_end - boundary_start) + 1 + 2 + 1);//ignore boundary at end of file
        	//TODO: Better way to do this ??
        	while(1)
        	{
        		if(((ret[0] == 'T') && (ret[1] == 'y') && (ret[2] == 'p') && (ret[3] == 'e') &&
        		        			(ret[4] == ':')))
        		{
        			break;
        		}
        		ret++;
        	}
    		ret = memchr(ret, '\n', 200);
    		buf = ret + 3;
    		remaining -= (buf - boundary_start);
    		ESP_LOGI(TAG, "Real Remaining size : %d", remaining);
    		
    		received -= (buf - boundary_start);
        }
        total_size += received;
        /* Write buffer content to file on storage */
        if (received > 0)
        {
            if (fwrite(buf, 1, received, fd) != received)
            {
                ESP_LOGE(TAG, "File write failed!");
                fclose(fd);
                unlink(filepath);
                httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to write file to storage");
                return ESP_FAIL;
            }
        }

        if(count < 3)
        {
        	count++;
        }
        /* Keep track of remaining size of
         * the file left to be uploaded */
        remaining -= received;
        vTaskDelay(5 / portTICK_PERIOD_MS);
    }


    fclose(fd);

    ESP_LOGI(TAG, "File reception complete: %lu", total_size);
    httpd_resp_sendstr(req, "File uploaded successfully");
    return ESP_OK;
}

esp_err_t autopid_data_handler(httpd_req_t *req)
{
    char *data = autopid_data_read();
    
    if (data == NULL)
    {
        ESP_LOGE(TAG, "No data available");
        const char *response = "{\"error\":\"No data available\"}";
        httpd_resp_set_type(req, "application/json");
        httpd_resp_send(req, response, strlen(response));
        return ESP_OK;
    }

    httpd_resp_set_type(req, "application/json");

    httpd_resp_send(req, data, strlen(data));

    free(data);

    return ESP_OK;
}

static esp_err_t scan_available_pids_handler(httpd_req_t *req)
{
    char protocol[8];
    char param[32];
    uint8_t protocol_num = 6; // Default protocol

    if(config_server_protocol() != AUTO_PID)
    {
        httpd_resp_set_type(req, "application/json");
        const char *resp_str = "{\"text\":\"Set protocol to AutoPid and reboot to be able to scan\"}";
        httpd_resp_send(req, resp_str, strlen(resp_str));
        return ESP_OK;
    }
	
    if (httpd_req_get_url_query_str(req, param, sizeof(param)) == ESP_OK) {
        if (httpd_query_key_value(param, "protocol", protocol, sizeof(protocol)) == ESP_OK) {
            protocol_num = atoi(protocol);
            ESP_LOGI(TAG, "Scanning PIDs with protocol: %d", protocol_num);
        }
    }

    char *available_pids = malloc(5120);
    if (available_pids == NULL) {
        httpd_resp_send_500(req);
        return ESP_FAIL;
    }
    memset(available_pids, 0, 5120);
    
    if (autopid_find_standard_pid(protocol_num, available_pids, 5120) == ESP_OK) {
        httpd_resp_set_type(req, "application/json");
        httpd_resp_send(req, available_pids, strlen(available_pids));
    }

    free(available_pids);
    return ESP_OK;
}

static const httpd_uri_t index_uri = {
    .uri       = "/",
    .method    = HTTP_GET,
    .handler   = index_handler,
    /* Let's pass response string in user
     * context to demonstrate it's usage */
    .user_ctx  = NULL
};
static const httpd_uri_t store_config_uri = {
    .uri       = "/store_config",
    .method    = HTTP_POST,
    .handler   = store_config_handler,
    /* Let's pass response string in user
     * context to demonstrate it's usage */
    .user_ctx  = NULL
};
static const httpd_uri_t store_canflt_uri = {
    .uri       = "/store_canflt",
    .method    = HTTP_POST,
    .handler   = store_canflt_handler,
    /* Let's pass response string in user
     * context to demonstrate it's usage */
    .user_ctx  = NULL
};
static const httpd_uri_t load_canflt_uri = {
    .uri       = "/load_canflt",
    .method    = HTTP_GET,
    .handler   = load_canflt_handler,
    /* Let's pass response string in user
     * context to demonstrate it's usage */
    .user_ctx  = NULL
};
static const httpd_uri_t load_pid_auto_uri = {
    .uri       = "/load_auto_pid",
    .method    = HTTP_GET,
    .handler   = load_pid_auto_handler,
    /* Let's pass response string in user
     * context to demonstrate it's usage */
    .user_ctx  = NULL
};
static const httpd_uri_t load_pid_auto_conf_uri = {
    .uri       = "/load_auto_pid_car_data",
    .method    = HTTP_GET,
    .handler   = load_pid_auto_config_handler,
    /* Let's pass response string in user
     * context to demonstrate it's usage */
    .user_ctx  = NULL
};
static const httpd_uri_t load_car_config_uri = {
    .uri       = "/load_car_config",
    .method    = HTTP_GET,
    .handler   = load_car_config_handler,
    /* Let's pass response string in user
     * context to demonstrate it's usage */
    .user_ctx  = NULL
};
static const httpd_uri_t check_status_uri = {
    .uri       = "/check_status",
    .method    = HTTP_GET,
    .handler   = check_status_handler,
    /* Let's pass response string in user
     * context to demonstrate it's usage */
    .user_ctx  = NULL
};
static const httpd_uri_t load_config_uri = {
    .uri       = "/load_config",
    .method    = HTTP_GET,
    .handler   = load_config_handler,
    /* Let's pass response string in user
     * context to demonstrate it's usage */
    .user_ctx  = NULL
};
static const httpd_uri_t logo_uri = {
    .uri       = "/logo.svg",
    .method    = HTTP_GET,
    .handler   = logo_handler,
    /* Let's pass response string in user
     * context to demonstrate it's usage */
    .user_ctx  = NULL
};
static const httpd_uri_t ws = {
        .uri        = "/ws",
        .method     = HTTP_GET,
        .handler    = ws_handler,
        .user_ctx   = NULL,
        .is_websocket = true
};
static struct file_server_data server_data = {.base_path = FS_MOUNT_POINT""};
//static struct file_server_data *server_data = NULL;
/* URI handler for uploading files to server */
static const httpd_uri_t file_upload = {
    .uri       = "/upload/ota.bin",   // Match all URIs of type /upload/path/to/file
    .method    = HTTP_POST,
    .handler   = upload_post_handler,
    .user_ctx  = &server_data    // Pass server data as context
};
static const httpd_uri_t system_reboot = {
    .uri       = "/system_reboot",   // Match all URIs of type /upload/path/to/file
    .method    = HTTP_POST,
    .handler   = system_reboot_handler,
    .user_ctx  = NULL    // Pass server data as context
};
static const httpd_uri_t store_auto_data_uri = {
    .uri       = "/store_auto_data",
    .method    = HTTP_POST,
    .handler   = store_auto_data_handler,
    /* Let's pass response string in user
     * context to demonstrate it's usage */
    .user_ctx  = NULL
};
static const httpd_uri_t upload_car_data = {
    .uri       = "/upload/car_data.json",   // Match all URIs of type /upload/path/to/file
    .method    = HTTP_POST,
    .handler   = upload_car_data_handler,
    .user_ctx  = &server_data    // Pass server data as context
};
static const httpd_uri_t autopid_data = {
    .uri       = "/autopid_data",   // Match all URIs of type /upload/path/to/file
    .method    = HTTP_GET,
    .handler   = autopid_data_handler,
    .user_ctx  = &server_data    // Pass server data as context
};
static const httpd_uri_t store_car_data_uri = {
    .uri       = "/store_car_data",
    .method    = HTTP_POST,
    .handler   = store_car_data_handler,
    .user_ctx  = NULL
};
static const httpd_uri_t scan_available_pids_uri = {
    .uri       = "/scan_available_pids",
    .method    = HTTP_GET,
    .handler   = scan_available_pids_handler,
    .user_ctx  = NULL
};
static void config_server_load_cfg(char *cfg)
{
	cJSON * root, *key = 0;
	root   = cJSON_Parse(cfg);
    struct stat st;

	key = cJSON_GetObjectItem(root,"wifi_mode");
	if(key == 0)
	{
		goto config_error;
	}
	strcpy(device_config.wifi_mode, key->valuestring);
	ESP_LOGI(TAG, "device_config.wifi_mode: %s", device_config.wifi_mode);

	key = cJSON_GetObjectItem(root,"ap_ch");
	if(key == 0)
	{
		goto config_error;
	}
	strcpy(device_config.ap_ch, key->valuestring);
	ESP_LOGI(TAG, "device_config.ap_ch: %s", device_config.ap_ch);

	key = cJSON_GetObjectItem(root,"sta_ssid");
	if(key == 0)
	{
		goto config_error;
	}
	if(strlen(key->valuestring) == 0 || strlen(key->valuestring) > 32)
	{
		goto config_error;
	}
	strcpy(device_config.sta_ssid, key->valuestring);
	ESP_LOGI(TAG, "device_config.sta_ssid: %s", device_config.sta_ssid);

	key = cJSON_GetObjectItem(root,"sta_pass");
	if(key == 0)
	{
		goto config_error;
	}
	if(strlen(key->valuestring) < 8 || strlen(key->valuestring) > 64)
	{
		goto config_error;
	}
	strcpy(device_config.sta_pass, key->valuestring);
	ESP_LOGI(TAG, "device_config.sta_pass: %s", device_config.sta_pass);

	key = cJSON_GetObjectItem(root,"can_datarate");
	if(key == 0)
	{
		goto config_error;
	}
	strcpy(device_config.can_datarate, key->valuestring);
	ESP_LOGI(TAG, "device_config.can_datarate: %s", device_config.can_datarate);

	key = cJSON_GetObjectItem(root,"can_mode");
	if(key == 0)
	{
		goto config_error;
	}
	strcpy(device_config.can_mode, key->valuestring);
	ESP_LOGI(TAG, "device_config.can_mode: %s", device_config.can_mode);

	key = cJSON_GetObjectItem(root,"port_type");
	if(key == 0)
	{
		goto config_error;
	}
	strcpy(device_config.port_type, key->valuestring);
	ESP_LOGI(TAG, "device_config.port_type: %s", device_config.port_type);

	key = cJSON_GetObjectItem(root,"port");
	if(key == 0)
	{
		goto config_error;
	}
	strcpy(device_config.port, key->valuestring);
	ESP_LOGI(TAG, "device_config.port: %s", device_config.port);


	key = cJSON_GetObjectItem(root,"ap_pass");
	if(key == 0)
	{
		goto config_error;
	}
	if(strlen(key->valuestring) < 8 || strlen(key->valuestring) > 64)
	{
		goto config_error;
	}
	strcpy(device_config.ap_pass, key->valuestring);
	ESP_LOGE(TAG, "device_config.ap_pass: %s", device_config.ap_pass);

	key = cJSON_GetObjectItem(root,"protocol");
	if(key == 0)
	{
		goto config_error;
	}
	if(strlen(key->valuestring) < 2 || strlen(key->valuestring) > 64)
	{
		goto config_error;
	}
	strcpy(device_config.protocol, key->valuestring);
	ESP_LOGE(TAG, "device_config.protocol: %s", device_config.protocol);

	key = cJSON_GetObjectItem(root,"ble_pass");
	if(key == 0)
	{
		goto config_error;
	}
	if(strlen(key->valuestring) < 4 || strlen(key->valuestring) > 16)
	{
		goto config_error;
	}
	strcpy(device_config.ble_pass, key->valuestring);
	ESP_LOGE(TAG, "device_config.ble_pass: %s", device_config.ble_pass);

	key = cJSON_GetObjectItem(root,"sleep_status");
	if(key == 0)
	{
		goto config_error;
	}

	strcpy(device_config.sleep_status, key->valuestring);
	ESP_LOGE(TAG, "device_config.sleep_status: %s", device_config.sleep_status);

	key = cJSON_GetObjectItem(root,"ble_status");
	if(key == 0)
	{
		goto config_error;
	}

	strcpy(device_config.ble_status, key->valuestring);
	ESP_LOGE(TAG, "device_config.ble_status: %s", device_config.ble_status);

	key = cJSON_GetObjectItem(root,"sleep_volt");
	if(key == 0)
	{
		goto config_error;
	}

	strcpy(device_config.sleep_volt, key->valuestring);
	ESP_LOGE(TAG, "device_config.sleep_volt: %s", device_config.sleep_volt);

	//*****
	key = cJSON_GetObjectItem(root,"batt_alert");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.batt_alert)))
	{
		goto config_error;
	}

	strcpy(device_config.batt_alert, key->valuestring);
	ESP_LOGE(TAG, "device_config.batt_alert: %s", device_config.batt_alert);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"batt_alert_ssid");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.batt_alert_ssid)))
	{
		goto config_error;
	}

	strcpy(device_config.batt_alert_ssid, key->valuestring);
	ESP_LOGE(TAG, "device_config.batt_alert_ssid: %s", device_config.batt_alert_ssid);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"batt_alert_pass");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.batt_alert_pass)))
	{
		goto config_error;
	}

	strcpy(device_config.batt_alert_pass, key->valuestring);
	ESP_LOGE(TAG, "device_config.batt_alert_pass: %s", device_config.batt_alert_pass);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"batt_alert_volt");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.batt_alert_volt)))
	{
		goto config_error;
	}

	strcpy(device_config.batt_alert_volt, key->valuestring);
	ESP_LOGE(TAG, "device_config.batt_alert_volt: %s", device_config.batt_alert_volt);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"batt_alert_protocol");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.batt_alert_protocol)))
	{
		goto config_error;
	}

	strcpy(device_config.batt_alert_protocol, key->valuestring);
	ESP_LOGE(TAG, "device_config.batt_alert_protocol: %s", device_config.batt_alert_protocol);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"batt_alert_url");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.batt_alert_url)))
	{
		goto config_error;
	}

	strcpy(device_config.batt_alert_url, key->valuestring);
	ESP_LOGE(TAG, "device_config.batt_alert_url: %s", device_config.batt_alert_url);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"batt_alert_port");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.batt_alert_port)))
	{
		goto config_error;
	}

	strcpy(device_config.batt_alert_port, key->valuestring);
	ESP_LOGE(TAG, "device_config.batt_alert_port: %s", device_config.batt_alert_port);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"batt_alert_topic");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.batt_alert_topic)))
	{
		goto config_error;
	}

	strcpy(device_config.batt_alert_topic, key->valuestring);
	ESP_LOGE(TAG, "device_config.batt_alert_topic: %s", device_config.batt_alert_topic);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"batt_mqtt_user");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.batt_mqtt_user)))
	{
		goto config_error;
	}

	strcpy(device_config.batt_mqtt_user, key->valuestring);
	ESP_LOGE(TAG, "device_config.batt_mqtt_user: %s", device_config.batt_mqtt_user);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"batt_mqtt_pass");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.batt_mqtt_pass)))
	{
		goto config_error;
	}

	strcpy(device_config.batt_mqtt_pass, key->valuestring);
	ESP_LOGE(TAG, "device_config.batt_mqtt_pass: %s", device_config.batt_mqtt_pass);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"batt_alert_time");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.batt_alert_time)))
	{
		goto config_error;
	}

	strcpy(device_config.batt_alert_time, key->valuestring);
	ESP_LOGE(TAG, "device_config.batt_alert_time: %s", device_config.batt_alert_time);
	//*****



	//*****
	key = cJSON_GetObjectItem(root,"mqtt_en");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.mqtt_en)))
	{
		goto config_error;
	}

	strcpy(device_config.mqtt_en, key->valuestring);
	ESP_LOGE(TAG, "device_config.mqtt_en: %s", device_config.mqtt_en);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"mqtt_url");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.mqtt_url)))
	{
		goto config_error;
	}

	strcpy(device_config.mqtt_url, key->valuestring);
	ESP_LOGE(TAG, "device_config.mqtt_url: %s", device_config.mqtt_url);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"mqtt_port");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.mqtt_port)))
	{
		goto config_error;
	}

	strcpy(device_config.mqtt_port, key->valuestring);
	ESP_LOGE(TAG, "device_config.mqtt_port: %s", device_config.mqtt_port);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"mqtt_user");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.mqtt_user)))
	{
		goto config_error;
	}

	strcpy(device_config.mqtt_user, key->valuestring);
	ESP_LOGE(TAG, "device_config.mqtt_user: %s", device_config.mqtt_user);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"mqtt_pass");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.mqtt_pass)))
	{
		goto config_error;
	}

	strcpy(device_config.mqtt_pass, key->valuestring);
	ESP_LOGE(TAG, "device_config.mqtt_pass: %s", device_config.mqtt_pass);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"mqtt_elm327_log");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.mqtt_elm327_log)))
	{
		goto config_error;
	}

	strcpy(device_config.mqtt_elm327_log, key->valuestring);
	ESP_LOGE(TAG, "device_config.mqtt_elm327_log: %s", device_config.mqtt_elm327_log);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"mqtt_tx_topic");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.mqtt_tx_topic)))
	{
		goto config_error;
	}
	if(strlen(key->valuestring) == 0)
	{
		sprintf(device_config.mqtt_tx_topic, "wican/%s/can/tx", device_id);
	}
	else
	{
		strcpy(device_config.mqtt_tx_topic, key->valuestring);
	}
	
	ESP_LOGE(TAG, "device_config.mqtt_tx_topic: %s", device_config.mqtt_tx_topic);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"mqtt_tx_en");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.mqtt_tx_en)))
	{
		strcpy(device_config.mqtt_tx_en,"disable");
	}
	else
	{
		strcpy(device_config.mqtt_tx_en, key->valuestring);
	}
	
	ESP_LOGE(TAG, "device_config.mqtt_tx_en: %s", device_config.mqtt_tx_en);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"mqtt_rx_en");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.mqtt_rx_en)))
	{
		strcpy(device_config.mqtt_rx_en,"disable");
	}
	else
	{
		strcpy(device_config.mqtt_rx_en, key->valuestring);
	}

	ESP_LOGE(TAG, "device_config.mqtt_rx_en: %s", device_config.mqtt_rx_en);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"mqtt_rx_topic");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.mqtt_rx_topic)) || strlen(key->valuestring) == 0)
	{
		goto config_error;
	}
	strcpy(device_config.mqtt_rx_topic, key->valuestring);

	
	ESP_LOGE(TAG, "device_config.mqtt_rx_topic: %s", device_config.mqtt_rx_topic);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"mqtt_status_topic");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.mqtt_status_topic)) || strlen(key->valuestring) == 0)
	{
		goto config_error;
	}
	strcpy(device_config.mqtt_status_topic, key->valuestring);

	
	ESP_LOGE(TAG, "device_config.mqtt_status_topic: %s", device_config.mqtt_status_topic);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"wakeup_volt");
	if(key == 0)
	{
		strcpy(device_config.wakeup_volt, "13.5");
	}
	else
	{
		strcpy(device_config.wakeup_volt, key->valuestring);
	}

	ESP_LOGE(TAG, "device_config.wakeup_volt: %s", device_config.wakeup_volt);
	//*****
	
	//*****
	key = cJSON_GetObjectItem(root,"sleep_time");
	if(key == 0)
	{
		strcpy(device_config.sleep_time, "2");
	}
	else
	{
		uint32_t sleep_time = atoi(device_config.sleep_time);

		if(sleep_time > 30 && sleep_time < 1)
		{
			strcpy(device_config.sleep_time, "2");
		}

		strcpy(device_config.sleep_time, key->valuestring);
	}

	ESP_LOGE(TAG, "device_config.sleep_time: %s", device_config.sleep_time);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"sta_security");
	if(key == 0)
	{
		strcpy(device_config.sta_security, "wpa3");
	}
	else
	{
		strcpy(device_config.sta_security, key->valuestring);
	}

	ESP_LOGE(TAG, "device_config.sta_security: %s", device_config.wakeup_volt);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"ap_auto_disable");
	if(key == 0)
	{
		strcpy(device_config.ap_auto_disable, "disable");
	}
	else
	{
		strcpy(device_config.ap_auto_disable, key->valuestring);
	}

	ESP_LOGE(TAG, "device_config.ap_auto_disable: %s", device_config.ap_auto_disable);
	//*****	

	//*****
	key = cJSON_GetObjectItem(root,"keep_alive");
	if(key == 0)
	{
		strcpy(device_config.keep_alive, "30");
	}
	else
	{
		uint32_t keep_alive = atoi(device_config.keep_alive);

		if(keep_alive > 120 && keep_alive < 1)
		{
			strcpy(device_config.keep_alive, "30");
		}

		strcpy(device_config.keep_alive, key->valuestring);
	}

	ESP_LOGE(TAG, "device_config.keep_alive: %s", device_config.keep_alive);
	//*****

	cJSON_Delete(root);
	return;


config_error:
    // Check if destination file exists before renaming

    if (stat(FS_MOUNT_POINT"/config.json", &st) == 0)
    {
    	ESP_LOGE(TAG, "config.json file error, restoring default");
        // Delete it if it exists
        unlink(FS_MOUNT_POINT"/config.json");
		FILE* f = fopen(FS_MOUNT_POINT"/config.json", "w");
		// sprintf(device_config_default, device_id, device_id);
		fprintf(f, device_config_default, (char*)device_id, (char*)device_id, (char*)device_id);
		fclose(f);
		vTaskDelay(3000 / portTICK_PERIOD_MS);
		esp_restart();
    }
	cJSON_Delete(root);
}

void config_server_wifi_connected(bool flag)
{
	if(flag)
	{
		xEventGroupSetBits( xServerEventGroup, WIFI_CONNECTED_BIT );
	}
	else
	{
		xEventGroupClearBits( xServerEventGroup, WIFI_CONNECTED_BIT );
	}
}
//
//bool config_server_get_wifi_connected(void)
//{
//	EventBits_t uxBits;
//	if(xServerEventGroup != NULL)
//	{
//		uxBits = xEventGroupGetBits(xServerEventGroup);
//
//		return (uxBits & WIFI_CONNECTED_BIT)?1:0;
//	}
//	else return 0;
//}

void config_server_set_sta_ip(char* ip)
{
	xQueueOverwrite(xip_Queue, ip);
}
void config_server_get_sta_ip(char* ip)
{
	xQueuePeek(xip_Queue, ip, 0);
}

void vrestartTimerCallback( TimerHandle_t xTimer )
{
//	vTaskDelay(1000 / portTICK_PERIOD_MS);
	esp_restart();
}


//static char* device_config = NULL;
static uint8_t esp_spiffs_flag = 0;
static httpd_config_t config = HTTPD_DEFAULT_CONFIG();
static httpd_handle_t config_server_init(void)
{
//	const char* base_path = "/"; //useless?
//
//    if (server_data)
//    {
//        ESP_LOGE(TAG, "File server already started");
//        return ESP_ERR_INVALID_STATE;
//    }
//
//    /* Allocate memory for server data */
//    server_data = calloc(1, sizeof(struct file_server_data));
//    if (!server_data)
//    {
//        ESP_LOGE(TAG, "Failed to allocate memory for server data");
//        return ESP_ERR_NO_MEM;
//    }
//
//    strlcpy(server_data->base_path, base_path,
//            sizeof(server_data->base_path));

    config.lru_purge_enable = true;

    if(xServerEventGroup == NULL)
    {
    	xServerEventGroup = xEventGroupCreate();
    	config_server_wifi_connected(0);
    }

    if(xip_Queue == NULL)
    {
    	xip_Queue = xQueueCreate(1, 20);
    }

    if(esp_spiffs_flag == 0)
    {
		ESP_LOGI(TAG, "Initializing SPIFFS");

		esp_vfs_spiffs_conf_t conf = {
		  .base_path = FS_MOUNT_POINT"",
		  .partition_label = NULL,
		  .max_files = 5,
		  .format_if_mount_failed = true
		};

		// Use settings defined above to initialize and mount SPIFFS filesystem.
		// Note: esp_vfs_spiffs_register is an all-in-one convenience function.
		esp_err_t ret = esp_vfs_spiffs_register(&conf);

		if (ret != ESP_OK)
		{
			if (ret == ESP_FAIL)
			{
				ESP_LOGE(TAG, "Failed to mount or format filesystem");
			}
			else if (ret == ESP_ERR_NOT_FOUND)
			{
				ESP_LOGE(TAG, "Failed to find SPIFFS partition");
			}
			else
			{
				ESP_LOGE(TAG, "Failed to initialize SPIFFS (%s)", esp_err_to_name(ret));
			}
//			return;
		}

		size_t total = 0, used = 0;
		ret = esp_spiffs_info(conf.partition_label, &total, &used);
		if (ret != ESP_OK)
		{
			ESP_LOGE(TAG, "Failed to get SPIFFS partition information (%s)", esp_err_to_name(ret));
		}
		else
		{
			ESP_LOGI(TAG, "Partition size: total: %d, used: %d", total, used);
		}

		// Use POSIX and C standard library functions to work with files.
		// First create a file.
		ESP_LOGI(TAG, "Opening file");
		FILE* f = fopen(FS_MOUNT_POINT"/config.json", "r");
		if (f == NULL)
		{
			ESP_LOGI(TAG, "Config file does not exist, load default");
			f = fopen(FS_MOUNT_POINT"/config.json", "w");
//			fwrite(device_config_default , 1 , sizeof(device_config_default) , f );
			fprintf(f, device_config_default, (char*)device_id, (char*)device_id, (char*)device_id);
			fclose(f);
			f = fopen(FS_MOUNT_POINT"/config.json", "r");
		}
		fseek(f, 0, SEEK_END);
		int filesize = ftell(f);
		fseek(f, 0, SEEK_SET);


		device_config_file = malloc(filesize+1);
		ESP_LOGI(__func__, "File size: %d", filesize);
		fseek(f, 0, SEEK_SET);
		fread(device_config_file, sizeof(char), filesize, f);
		device_config_file[filesize] = 0;
		fseek(f, 0, SEEK_SET);
		ESP_LOGI(TAG, "config.json: %s", device_config_file);
		config_server_load_cfg(device_config_file);
		
		FILE* f1 = fopen(FS_MOUNT_POINT"/mqtt_canfilt.json", "r");
		if (f1 != NULL)
		{
			fseek(f1, 0, SEEK_END);
			filesize = ftell(f1);
			fseek(f1, 0, SEEK_SET);
			mqtt_canflt_file = malloc(filesize+1);
			ESP_LOGI(__func__, "mqtt_canflt_file File size: %d", filesize);
			fseek(f1, 0, SEEK_SET);
			fread(mqtt_canflt_file, sizeof(char), filesize, f1);
			mqtt_canflt_file[filesize] = 0;
			fseek(f1, 0, SEEK_SET);
			fclose(f1);
			ESP_LOGI(TAG, "mqtt_canfilt.json: %s", mqtt_canflt_file);
		}
    }

    xrestartTimer= xTimerCreate
                       ( /* Just a text name, not used by the RTOS
                         kernel. */
                         "Timer",
                         /* The timer period in ticks, must be
                         greater than 0. */
						 (2000 / portTICK_PERIOD_MS),
                         /* The timers will auto-reload themselves
                         when they expire. */
                         pdTRUE,
                         /* The ID is used to store a count of the
                         number of times the timer has expired, which
                         is initialised to 0. */
                         ( void * ) 0,
                         /* Each timer calls the same callback when
                         it expires. */
                         vrestartTimerCallback
                       );

    // Start the httpd server
	config.max_uri_handlers = 18;
	config.stack_size = 5120;
    ESP_LOGI(TAG, "Starting server on port: '%d'", config.server_port);
    if (httpd_start(&server, &config) == ESP_OK)
    {
        // Set URI handlers
        ESP_LOGI(TAG, "Registering URI handlers");
        httpd_register_uri_handler(server, &index_uri);
        httpd_register_uri_handler(server, &store_config_uri);
        httpd_register_uri_handler(server, &check_status_uri);
        httpd_register_uri_handler(server, &load_config_uri);
        httpd_register_uri_handler(server, &logo_uri);
        httpd_register_uri_handler(server, &ws);
        httpd_register_uri_handler(server, &file_upload);
		httpd_register_uri_handler(server, &system_reboot);
		httpd_register_uri_handler(server, &store_canflt_uri);
		httpd_register_uri_handler(server, &load_canflt_uri);
		httpd_register_uri_handler(server, &store_auto_data_uri);
		httpd_register_uri_handler(server, &load_pid_auto_uri);
		httpd_register_uri_handler(server, &load_pid_auto_conf_uri);
		httpd_register_uri_handler(server, &upload_car_data);
		httpd_register_uri_handler(server, &autopid_data);
		httpd_register_uri_handler(server, &load_car_config_uri);
		httpd_register_uri_handler(server, &store_car_data_uri);
		httpd_register_uri_handler(server, &scan_available_pids_uri);
        #if CONFIG_EXAMPLE_BASIC_AUTH
        httpd_register_basic_auth(server);
        #endif
        return server;
    }

    ESP_LOGI(TAG, "Error starting server!");
    return NULL;
}
void config_server_restart(void)
{
    // Start the httpd server
    ESP_LOGI(TAG, "Starting server on port: '%d'", config.server_port);
    if (httpd_start(&server, &config) == ESP_OK)
    {
        // Set URI handlers
        ESP_LOGI(TAG, "Registering URI handlers");
        httpd_register_uri_handler(server, &index_uri);
        httpd_register_uri_handler(server, &store_config_uri);
        httpd_register_uri_handler(server, &check_status_uri);
        httpd_register_uri_handler(server, &load_config_uri);
        httpd_register_uri_handler(server, &logo_uri);
        httpd_register_uri_handler(server, &ws);
        httpd_register_uri_handler(server, &file_upload);
		httpd_register_uri_handler(server, &system_reboot);
		httpd_register_uri_handler(server, &store_canflt_uri);
		httpd_register_uri_handler(server, &load_canflt_uri);
        return;
    }

    ESP_LOGI(TAG, "Error starting server!");
}
void config_server_stop(void)
{
    if (server)
    {
        ESP_LOGI(TAG, "Stopping webserver");
        httpd_stop(server);
        server = NULL;
    }
}
static void websocket_task(void *pvParameters)
{
	static xdev_buffer ucTX_Buffer;
	httpd_ws_frame_t ws_pkt;  
	ESP_LOGI(TAG, "websocket_task started");
	while(1)
	{
		xQueueReceive(*xTX_Queue, &ucTX_Buffer, portMAX_DELAY);

		memset(&ws_pkt, 0, sizeof(httpd_ws_frame_t));
		ws_pkt.payload = (uint8_t*)ucTX_Buffer.ucElement;
		ws_pkt.len = ucTX_Buffer.usLen;
		ws_pkt.type = HTTPD_WS_TYPE_TEXT;

	    esp_err_t ret = httpd_ws_send_frame_async(rsp_arg.hd, rsp_arg.fd, &ws_pkt);
	    if (ret != ESP_OK)
	    {
//	    	tcp_server_resume();
	    	gpio_set_level(ws_led, 1);
	    	xEventGroupClearBits( xServerEventGroup, WS_CONNECTED_BIT );
//	    	vTaskSuspend( NULL );

	        ESP_LOGE(TAG, "httpd_ws_send_frame_async failed  %d", ret);
	    }
	}

}

bool config_server_ws_connected(void)
{
	EventBits_t ux_bits;
	if(xServerEventGroup != NULL)
	{
		ux_bits = xEventGroupGetBits(xServerEventGroup);

		return (ux_bits & WS_CONNECTED_BIT);
	}
	else return 0;
}

void config_server_start(QueueHandle_t *xTXp_Queue, QueueHandle_t *xRXp_Queue, uint8_t connected_led, char * did)
{
    if (server == NULL)
    {
		device_id = did;
    	ws_led = connected_led;
    	xTX_Queue = xTXp_Queue;
    	xRX_Queue = xRXp_Queue;
        ESP_LOGI(TAG, "Starting webserver");
        server = config_server_init();

        xTaskCreate(websocket_task, "ws_task", 4096, (void*)AF_INET, 5, &xwebsocket_handle);

    }
}
int8_t config_server_get_ble_config(void)
{
	if(strcmp(device_config.ble_status, "enable") == 0)
	{
		return 1;
	}
	else if(strcmp(device_config.ble_status, "disable") == 0)
	{
		return 0;
	}
	return -1;
}

int8_t config_server_get_sleep_config(void)
{
	if(strcmp(device_config.sleep_status, "enable") == 0)
	{
		return 1;
	}
	else if(strcmp(device_config.sleep_status, "disable") == 0)
	{
		return 0;
	}
	return -1;
}

int8_t config_server_get_sleep_volt(float *sleep_volt)
{
	*sleep_volt = atof(device_config.sleep_volt);

	if(*sleep_volt >= 12.0f && *sleep_volt <= 15.0f)
	{
		return 1;
	}
	return -1;
}

int8_t config_server_get_wakeup_volt(float *wakeup_volt)
{
	*wakeup_volt = atof(device_config.wakeup_volt);

	if(*wakeup_volt >= 12.0f && *wakeup_volt <= 15.0f)
	{
		return 1;
	}
	return -1;
}

int8_t config_server_get_sleep_time(uint32_t *sleep_time)
{
    char *endptr;
    long slp_t = strtol(device_config.sleep_time, &endptr, 10);
    
    // Check for conversion errors
    if (*endptr != '\0' || endptr == device_config.sleep_time)
	{
        return -1;
    }
    
    // Validate range
    if (slp_t < 1 || slp_t > 30)
	{
        return -1;
    }
    
    *sleep_time = (uint32_t)slp_t;
    return 1;
}

int8_t config_server_get_battery_alert_config(void)
{
	if(strcmp(device_config.batt_alert, "enable") == 0)
	{
		return 1;
	}
	else if(strcmp(device_config.batt_alert, "disable") == 0)
	{
		return 0;
	}
	return -1;
}

int32_t config_server_get_alert_port(void)
{
	int port_val = atoi(device_config.batt_alert_port);

	if(port_val > 0 && port_val <= 65535)
	{
		return port_val;
	}
	return -1;
}

char *config_server_get_alert_ssid(void)
{
	return device_config.batt_alert_ssid;
}

char *config_server_get_alert_pass(void)
{
	return device_config.batt_alert_pass;
}

char *config_server_get_alert_protocol(void)
{
	return device_config.batt_alert_protocol;
}

char *config_server_get_alert_url(void)
{
	return device_config.batt_alert_url;
}

char *config_server_get_alert_topic(void)
{
	return device_config.batt_alert_topic;
}

char *config_server_get_alert_mqtt_user(void)
{
	return device_config.batt_mqtt_user;
}

char *config_server_get_alert_mqtt_pass(void)
{
	return device_config.batt_mqtt_pass;
}

int8_t config_server_get_keep_alive(uint32_t *keep_alive)
{
    char *endptr;
    long kp_alive = strtol(device_config.keep_alive, &endptr, 10);
    
    // Check for conversion errors
    if (*endptr != '\0' || endptr == device_config.keep_alive)
	{
        return -1;
    }
    
    // Validate range
    if (kp_alive < 1 || kp_alive > 120)
	{
        return -1;
    }
    
    *keep_alive = (uint32_t)kp_alive;
    return 1;
}

int config_server_get_alert_time(void)
{
	if(strcmp(device_config.batt_alert_time, "1") == 0)
	{
		return 1;
	}
	else if(strcmp(device_config.batt_alert_time, "6") == 0)
	{
		return 6;
	}
	else if(strcmp(device_config.batt_alert_time, "12") == 0)
	{
		return 12;
	}
	else if(strcmp(device_config.batt_alert_time, "24") == 0)
	{
		return 24;
	}
	else
	{
		return -1;
	}

}

int8_t config_server_get_alert_volt(float *alert_volt)
{
	*alert_volt = atof(device_config.batt_alert_volt);

	if(device_config.batt_alert_volt[2] != '.')
	{
		return -1;
	}

	if(*alert_volt > 8.0f && *alert_volt <= 15.0f)
	{
		return 1;
	}
	return -1;
}

int8_t config_server_mqtt_en_config(void)
{
	if(config_server_get_ble_config())
	{
		return 0;
	}
	if(strcmp(device_config.mqtt_en, "enable") == 0)
	{
		return 1;
	}
	else if(strcmp(device_config.mqtt_en, "disable") == 0)
	{
		return 0;
	}
	return -1;
}

int8_t config_server_mqtt_tx_en_config(void)
{
	if(strcmp(device_config.mqtt_tx_en, "enable") == 0)
	{
		return 1;
	}
	else if(strcmp(device_config.mqtt_tx_en, "disable") == 0)
	{
		return 0;
	}
	return -1;
}

int8_t config_server_mqtt_rx_en_config(void)
{
	if(strcmp(device_config.mqtt_rx_en, "enable") == 0)
	{
		return 1;
	}
	else if(strcmp(device_config.mqtt_rx_en, "disable") == 0)
	{
		return 0;
	}
	return -1;
}

int8_t config_server_mqtt_elm327_log(void)
{
	if(strcmp(device_config.mqtt_elm327_log, "enable") == 0)
	{
		return 1;
	}
	else if(strcmp(device_config.mqtt_elm327_log, "disable") == 0)
	{
		return 0;
	}
	return -1;
}
char *config_server_get_mqtt_url(void)
{
	return device_config.mqtt_url;
}

int32_t config_server_get_mqtt_port(void)
{
	int port_val = atoi(device_config.mqtt_port);

	if(port_val > 0 && port_val <= 65535)
	{
		return port_val;
	}
	return -1;
}

char *config_server_get_mqtt_user(void)
{
	return device_config.mqtt_user;
}

char *config_server_get_mmqtt_pass(void)
{
	return device_config.mqtt_pass;
}

char *config_server_get_mqtt_tx_topic(void)
{
	return device_config.mqtt_tx_topic;
}

char *config_server_get_mqtt_rx_topic(void)
{
	return device_config.mqtt_rx_topic;
}

char *config_server_get_mqtt_status_topic(void)
{
	return device_config.mqtt_status_topic;
}

char *config_server_get_mqtt_canflt(void)
{
	return mqtt_canflt_file;
}

wifi_security_t config_server_get_sta_security(void)
{
	if (strcmp(device_config.sta_security, "wpa2") == 0)
	{
		return WIFI_WPA2_PSK;
	}
	else if (strcmp(device_config.sta_security, "wpa3") == 0)
	{
		return WIFI_WPA3_PSK;
	}
	return WIFI_MAX;
}

int8_t config_server_get_ap_auto_disable(void)
{
	if(strcmp(device_config.ap_auto_disable, "enable") == 0)
	{
		return 1;
	}
	else if(strcmp(device_config.ap_auto_disable, "disable") == 0)
	{
		return 0;
	}
	return -1;
}

void config_server_set_ble_config(uint8_t b)
{
	cJSON * root;
	root = cJSON_Parse(device_config_file);
	if(b == 1)
	{
		cJSON_SetValuestring(cJSON_GetObjectItem(root,"ble_status"), "enable");
	}
	else if(b==0)
	{
		cJSON_SetValuestring(cJSON_GetObjectItem(root,"ble_status"), "disable");
	}
	const char *resp_str = cJSON_Print(root);
	ESP_LOGI(TAG, "resp_str:%s", resp_str);
	FILE* f = fopen(FS_MOUNT_POINT"/config.json", "w");
	if (f != NULL)
	{
		fprintf(f, resp_str);
		fclose(f);
	}
	xTimerStart( xrestartTimer, 0 );
	free((void *)resp_str);
    cJSON_Delete(root);
}

