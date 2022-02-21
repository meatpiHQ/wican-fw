

void ble_init(QueueHandle_t *xTXp_Queue, QueueHandle_t *xRXp_Queue, uint8_t connected_led, int passkey, uint8_t* uid);
bool ble_connected(void);
void ble_send(uint8_t* buf, uint8_t buf_len);
bool ble_tx_ready(void);
