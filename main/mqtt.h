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

#ifndef MQTT_h
#define MQTT_h

#include "driver/twai.h"
#include "elm327.h"

#define MQTT_CAN        0x00
#define MQTT_RX         ELM327_CAN_RX
#define MQTT_TX         ELM327_CAN_TX

typedef struct
{
    uint8_t type;
    twai_message_t frame;
}mqtt_can_message_t;

void mqtt_init(char* id, uint8_t connected_led, QueueHandle_t *xtx_queue);
int mqtt_connected(void);
void mqtt_publish(const char *topic, const char *data, int len, int qos, int retain);
#endif
