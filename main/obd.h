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

#define UART_BUF_SIZE           (512)
#define OBD_UART_QUEUE_SIZE     (5)
#define OBD_QUEUE_SIZE          (5)
#define OBD_DATA_BUF_SIZE       (512)
#define OBD_CMD_BUF_SIZE        (512)
#define OBD_RESET_PIN           (GPIO_NUM_41)

typedef struct {
    char rsp_data[OBD_DATA_BUF_SIZE];  // Adjust the size of the buffer as needed
    uint8_t rsp_end_flag;
    size_t size;
} obd_rsp_t;

typedef struct {
    char cmd[OBD_CMD_BUF_SIZE];      // Adjust the size of the buffer as needed
    uint32_t timeout_ms;
    QueueHandle_t *rsp_queue;        // Pointer to the response queue for the command
    obd_rsp_t *obd_rsp;
} obd_cmd_t;

void obd_init(void);
void obd_log_response(char *buf, uint32_t size);
void obd_send_cmd(char *cmd, QueueHandle_t *rsp_queue, obd_rsp_t *obd_rsp, uint32_t timeout_ms);
void obd_write_cmd(char* cmd, char** rsp_buf, uint32_t *rsp_len, uint32_t timeout_ms);
int8_t obd_get_voltage(float *val);
