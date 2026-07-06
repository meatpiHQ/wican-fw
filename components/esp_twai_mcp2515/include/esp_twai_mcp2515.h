/*
 * SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#pragma once

#include "hal/gpio_types.h"
#include "esp_twai.h"

#ifdef __cplusplus
extern "C" {
#endif

/// @brief Opaque handle to an MCP2515-backed TWAI node
typedef spi_host_device_t twai_mcp2515_spi_bus_handle_t;

/// @brief MCP2515 node configuration
typedef struct {
    struct {
        gpio_num_t int_gpio;                /**< MCP2515 INT pin */
        gpio_num_t cs_gpio;                 /**< MCP2515 SPI CS pin */
    } io_cfg;
    uint32_t spi_clock_hz;                   /**< SPI clock frequency in Hz */
    uint32_t oscillator_hz;                  /**< MCP2515 oscillator frequency in Hz, set 0 to use 8000000 */
    twai_timing_basic_config_t bit_timing;   /**< Bit timing configuration for classic TWAI */
    int fail_retry_cnt;                      /**< MCP2515 supports only -1 (auto retry) or 0 (one-shot) */
    uint32_t timestamp_resolution_hz;        /**< Timestamp resolution in Hz, 0 to disable timestamp conversion */
    uint32_t tx_queue_depth;                 /**< Depth of software transmit queue */
    struct {
        uint32_t enable_loopback: 1;         /**< Enable MCP2515 loopback mode */
        uint32_t enable_listen_only: 1;      /**< Enable MCP2515 listen-only mode */
    } flags;
} twai_mcp2515_node_config_t;

/**
 * @brief Create a TWAI node backed by an external MCP2515 controller
 *
 * @param[in] bus SPI host where MCP2515 device is attached
 * @param[in] node_config MCP2515 node configuration
 * @param[out] node_ret Returned TWAI node handle
 *
 * @note The caller must install GPIO ISR service before creating node:
 *       gpio_install_isr_service(...)
 *
 * @return
 *      - ESP_OK: Success
 *      - ESP_ERR_INVALID_ARG: Invalid argument
 *      - ESP_ERR_NO_MEM: Out of memory
 *      - ESP_ERR_INVALID_STATE: Driver state or hardware state invalid
 *      - ESP_FAIL: Other failure
 */
esp_err_t twai_new_node_mcp2515(twai_mcp2515_spi_bus_handle_t bus, const twai_mcp2515_node_config_t *node_config, twai_node_handle_t *node_ret);

#ifdef __cplusplus
}
#endif
