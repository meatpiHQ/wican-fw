/*
 * SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#pragma once

/* SPI command opcodes */
#define MCP2515_CMD_RESET                 0xC0    /* Reset internal registers/state */
#define MCP2515_CMD_READ                  0x03    /* Read register(s) over SPI */
#define MCP2515_CMD_WRITE                 0x02    /* Write register(s) over SPI */
#define MCP2515_CMD_BIT_MODIFY            0x05    /* Modify selected register bits */
#define MCP2515_CMD_READ_STATUS           0xA0    /* Read quick TX/RX status byte */
#define MCP2515_CMD_RX_STATUS             0xB0    /* Read RX buffer/filter status byte */
#define MCP2515_CMD_RTS_TXB0              0x81    /* Request transmit for TX buffer 0 */
#define MCP2515_CMD_RTS_TXB1              0x82    /* Request transmit for TX buffer 1 */
#define MCP2515_CMD_RTS_TXB2              0x84    /* Request transmit for TX buffer 2 */
#define MCP2515_CMD_RTS_ALL               0x87    /* Request transmit for all TX buffers */

/* Registers */
#define MCP2515_REG_CANSTAT               0x0E    /* CAN status register */
#define MCP2515_REG_CANCTRL               0x0F    /* CAN control register */
#define MCP2515_REG_TEC                   0x1C    /* TX error counter */
#define MCP2515_REG_REC                   0x1D    /* RX error counter */
#define MCP2515_REG_CNF3                  0x28    /* Bit timing config register 3 */
#define MCP2515_REG_CNF2                  0x29    /* Bit timing config register 2 */
#define MCP2515_REG_CNF1                  0x2A    /* Bit timing config register 1 */
#define MCP2515_REG_CANINTE               0x2B    /* CAN interrupt enable register */
#define MCP2515_REG_CANINTF               0x2C    /* CAN interrupt flag register */
#define MCP2515_REG_EFLG                  0x2D    /* Error flag register */

#define MCP2515_REG_RXF0SIDH              0x00    /* RX filter 0 SID high */
#define MCP2515_REG_RXF1SIDH              0x04    /* RX filter 1 SID high */
#define MCP2515_REG_RXF2SIDH              0x08    /* RX filter 2 SID high */
#define MCP2515_REG_RXF3SIDH              0x10    /* RX filter 3 SID high */
#define MCP2515_REG_RXF4SIDH              0x14    /* RX filter 4 SID high */
#define MCP2515_REG_RXF5SIDH              0x18    /* RX filter 5 SID high */
#define MCP2515_REG_RXM0SIDH              0x20    /* RX mask 0 SID high */
#define MCP2515_REG_RXM1SIDH              0x24    /* RX mask 1 SID high */

#define MCP2515_REG_TXB0CTRL              0x30    /* TX buffer 0 control */
#define MCP2515_REG_TXB0SIDH              0x31    /* TX buffer 0 SID high */
#define MCP2515_REG_TXB0SIDL              0x32    /* TX buffer 0 SID low */
#define MCP2515_REG_TXB0EID8              0x33    /* TX buffer 0 EID high */
#define MCP2515_REG_TXB0EID0              0x34    /* TX buffer 0 EID low */
#define MCP2515_REG_TXB0DLC               0x35    /* TX buffer 0 DLC/RTR */
#define MCP2515_REG_TXB0D0                0x36    /* TX buffer 0 data byte 0 base */

#define MCP2515_REG_TXB1CTRL              0x40    /* TX buffer 1 control */
#define MCP2515_REG_TXB1SIDH              0x41    /* TX buffer 1 SID high */
#define MCP2515_REG_TXB1SIDL              0x42    /* TX buffer 1 SID low */
#define MCP2515_REG_TXB1EID8              0x43    /* TX buffer 1 EID high */
#define MCP2515_REG_TXB1EID0              0x44    /* TX buffer 1 EID low */
#define MCP2515_REG_TXB1DLC               0x45    /* TX buffer 1 DLC/RTR */
#define MCP2515_REG_TXB1D0                0x46    /* TX buffer 1 data byte 0 base */

#define MCP2515_REG_TXB2CTRL              0x50    /* TX buffer 2 control */
#define MCP2515_REG_TXB2SIDH              0x51    /* TX buffer 2 SID high */
#define MCP2515_REG_TXB2SIDL              0x52    /* TX buffer 2 SID low */
#define MCP2515_REG_TXB2EID8              0x53    /* TX buffer 2 EID high */
#define MCP2515_REG_TXB2EID0              0x54    /* TX buffer 2 EID low */
#define MCP2515_REG_TXB2DLC               0x55    /* TX buffer 2 DLC/RTR */
#define MCP2515_REG_TXB2D0                0x56    /* TX buffer 2 data byte 0 base */

#define MCP2515_REG_RXB0CTRL              0x60    /* RX buffer 0 control */
#define MCP2515_REG_RXB0SIDH              0x61    /* RX buffer 0 SID high */
#define MCP2515_REG_RXB0SIDL              0x62    /* RX buffer 0 SID low */
#define MCP2515_REG_RXB0EID8              0x63    /* RX buffer 0 EID high */
#define MCP2515_REG_RXB0EID0              0x64    /* RX buffer 0 EID low */
#define MCP2515_REG_RXB0DLC               0x65    /* RX buffer 0 DLC/RTR */
#define MCP2515_REG_RXB0D0                0x66    /* RX buffer 0 data byte 0 base */

#define MCP2515_REG_RXB1CTRL              0x70    /* RX buffer 1 control */
#define MCP2515_REG_RXB1SIDH              0x71    /* RX buffer 1 SID high */
#define MCP2515_REG_RXB1SIDL              0x72    /* RX buffer 1 SID low */
#define MCP2515_REG_RXB1EID8              0x73    /* RX buffer 1 EID high */
#define MCP2515_REG_RXB1EID0              0x74    /* RX buffer 1 EID low */
#define MCP2515_REG_RXB1DLC               0x75    /* RX buffer 1 DLC/RTR */
#define MCP2515_REG_RXB1D0                0x76    /* RX buffer 1 data byte 0 base */

/* CANCTRL mode bits */
#define MCP2515_CANCTRL_REQOP_MASK        0xE0    /* Operation mode request mask */
#define MCP2515_CANCTRL_MODE_NORMAL       0x00    /* Normal operation mode */
#define MCP2515_CANCTRL_MODE_SLEEP        0x20    /* Sleep mode */
#define MCP2515_CANCTRL_MODE_LOOPBACK     0x40    /* Loopback mode */
#define MCP2515_CANCTRL_MODE_LISTEN_ONLY  0x60    /* Listen-only mode */
#define MCP2515_CANCTRL_MODE_CONFIG       0x80    /* Configuration mode */
#define MCP2515_CANCTRL_CLKPRE_MASK       0x03    /* CLKOUT prescaler field mask */
#define MCP2515_CANCTRL_CLKPRE_DIV1       0x00    /* CLKOUT prescaler /1 */
#define MCP2515_CANCTRL_CLKPRE_DIV2       0x01    /* CLKOUT prescaler /2 */
#define MCP2515_CANCTRL_CLKPRE_DIV4       0x02    /* CLKOUT prescaler /4 */
#define MCP2515_CANCTRL_CLKPRE_DIV8       0x03    /* CLKOUT prescaler /8 */
#define MCP2515_CANCTRL_CLKEN             0x04    /* Enable CLKOUT function */
#define MCP2515_CANCTRL_OSM               0x08    /* One-shot mode enable */
#define MCP2515_CANCTRL_ABAT              0x10    /* Abort all pending transmissions */

/* CANSTAT mode bits */
#define MCP2515_CANSTAT_OPMOD_MASK        0xE0    /* Current operation mode mask */
#define MCP2515_CANSTAT_ICOD_MASK         0x0E    /* Interrupt code bits */

/* Interrupt bits */
#define MCP2515_INT_RX0IF                 0x01    /* RX buffer 0 interrupt flag/enable */
#define MCP2515_INT_RX1IF                 0x02    /* RX buffer 1 interrupt flag/enable */
#define MCP2515_INT_TX0IF                 0x04    /* TX buffer 0 interrupt flag/enable */
#define MCP2515_INT_TX1IF                 0x08    /* TX buffer 1 interrupt flag/enable */
#define MCP2515_INT_TX2IF                 0x10    /* TX buffer 2 interrupt flag/enable */
#define MCP2515_INT_ERRIF                 0x20    /* Error interrupt flag/enable */
#define MCP2515_INT_WAKIF                 0x40    /* Wake-up interrupt flag/enable */
#define MCP2515_INT_MERRF                 0x80    /* Message error interrupt flag/enable */

/* EFLG bits */
#define MCP2515_EFLG_EWARN                0x01    /* Error warning state */
#define MCP2515_EFLG_RXWAR                0x02    /* RX warning threshold reached */
#define MCP2515_EFLG_TXWAR                0x04    /* TX warning threshold reached */
#define MCP2515_EFLG_RXEP                 0x08    /* RX error-passive state */
#define MCP2515_EFLG_TXEP                 0x10    /* TX error-passive state */
#define MCP2515_EFLG_TXBO                 0x20    /* Bus-off state */
#define MCP2515_EFLG_RX0OVR               0x40    /* RX buffer 0 overflow */
#define MCP2515_EFLG_RX1OVR               0x80    /* RX buffer 1 overflow */

/* TXBnCTRL bits */
#define MCP2515_TXBCTRL_TXREQ             0x08    /* TX request pending */
#define MCP2515_TXBCTRL_TXERR             0x10    /* TX error detected */
#define MCP2515_TXBCTRL_MLOA              0x20    /* Message lost arbitration */
#define MCP2515_TXBCTRL_ABTF              0x40    /* Message aborted flag */
#define MCP2515_TXBCTRL_TXP0              0x01    /* TX priority bit 0 */
#define MCP2515_TXBCTRL_TXP1              0x02    /* TX priority bit 1 */
#define MCP2515_TXBCTRL_TXP_MASK          0x03    /* TX priority field mask */

/* DLC bits */
#define MCP2515_DLC_LEN_MASK              0x0F    /* DLC[3:0] data length mask */
#define MCP2515_DLC_RTR                   0x40    /* RTR bit in DLC register */

/* CNF2 bits */
#define MCP2515_CNF2_BTLMODE              0x80    /* PHSEG2 freely programmable */
#define MCP2515_CNF2_SAM                  0x40    /* Bus line sampled 3 times */
#define MCP2515_CNF2_PHSEG1_MASK          0x38    /* PHSEG1 field mask */
#define MCP2515_CNF2_PRSEG_MASK           0x07    /* Propagation segment field mask */

/* CNF3 bits */
#define MCP2515_CNF3_SOF                  0x80    /* CLKOUT pin outputs SOF pulse */
#define MCP2515_CNF3_WAKFIL               0x40    /* Wake-up filter enable */
#define MCP2515_CNF3_PHSEG2_MASK          0x07    /* PHSEG2 field mask */

/* RXBnCTRL bits */
#define MCP2515_RXB0CTRL_RXM_MASK         0x60    /* RX buffer 0 receive mode field */
#define MCP2515_RXB0CTRL_RXM_STD_EXT      0x00    /* Receive valid messages via filters */
#define MCP2515_RXB0CTRL_RXM_EXT_ONLY     0x20    /* Receive only extended frames */
#define MCP2515_RXB0CTRL_RXM_STD_ONLY     0x40    /* Receive only standard frames */
#define MCP2515_RXB0CTRL_RXM_ALL          0x60    /* Receive all messages */
#define MCP2515_RXB0CTRL_BUKT             0x04    /* Rollover from RXB0 to RXB1 enable */
#define MCP2515_RXB0CTRL_RXRTR            0x08    /* Received remote transfer request */
#define MCP2515_RXB0CTRL_FILHIT_MASK      0x03    /* Filter hit code for RXB0 */

#define MCP2515_RXB1CTRL_RXM_MASK         0x60    /* RX buffer 1 receive mode field */
#define MCP2515_RXB1CTRL_RXM_STD_EXT      0x00    /* Receive valid messages via filters */
#define MCP2515_RXB1CTRL_RXM_EXT_ONLY     0x20    /* Receive only extended frames */
#define MCP2515_RXB1CTRL_RXM_STD_ONLY     0x40    /* Receive only standard frames */
#define MCP2515_RXB1CTRL_RXM_ALL          0x60    /* Receive all messages */
#define MCP2515_RXB1CTRL_RXRTR            0x08    /* Received remote transfer request */
#define MCP2515_RXB1CTRL_FILHIT_MASK      0x07    /* Filter hit code for RXB1 */

/* Common SIDL field masks (shared bit layout across TX/RX/filter SIDL registers) */
#define MCP2515_SIDL_SID_MASK             0xE0    /* SID[2:0] field mask in SIDL */
#define MCP2515_SIDL_EID_HIGH_MASK        0x03    /* EID[17:16] field mask in SIDL */

/* TXBnSIDL / RXFnSIDL semantics */
#define MCP2515_SIDL_EXIDE                0x08    /* Extended ID enable/select bit */

/* RXBnSIDL semantics */
#define MCP2515_SIDL_IDE                  0x08    /* Received frame identifier extension bit */
#define MCP2515_SIDL_SRR                  0x10    /* Standard frame remote request indicator */
