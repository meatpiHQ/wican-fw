#ifndef __WIFI_NETWORK_H__
#define __WIFI_NETWORK_H__

bool wifi_network_is_connected(void);
void wifi_network_init(void);
void wifi_network_deinit(void);
void wifi_network_restart(void);
#endif
