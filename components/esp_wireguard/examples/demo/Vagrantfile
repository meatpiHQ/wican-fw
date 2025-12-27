# A vagrant box for testing. The VM is configured to use a brigde network,
# i.e. the VM is attached to the same network of the host OS. The interface
# uses DHCP.
#
# How to test the example:
#
# Make sure the follwings are installed on local machine:
#
# * `vagrant`
# * `virtualbox`
#
# Boot the VM. To boot the VM, run:
# > vagrant up
#
# At the initial boot, `vagrant` downloads my VM image (~700MB).
#
# Login to the server. To login to the VM, run:
# > vagrant ssh
#
# sudo requires no password.
#
# See the IP address of the server.
# > ifconfig em1
# em1: flags=808843<UP,BROADCAST,RUNNING,SIMPLEX,MULTICAST,AUTOCONF4> mtu 1500
#         lladdr 08:00:27:02:ba:ab
#         index 2 priority 0 llprio 3
#         groups: egress
#         media: Ethernet autoselect (1000baseT full-duplex)
#         status: active
#         inet 192.168.99.25 netmask 0xffffff00 broadcast 192.168.99.255
#
# in this case, the IP address of the server is `192.168.99.25`.
#
# Ensure there is no `sdkconfig`. Delete it if there is.
#
# Configure the example by running `idf.py menuconfig`. You must change at
# least the followings:
#
# * ESP_WIFI_SSID
# * ESP_WIFI_PASSWORD
# * WG_PEER_ADDRESS
#
# Change `ESP_WIFI_SSID` and `ESP_WIFI_PASSWORD` to your SSID and password.
# Use the IP adddress of the server for `WG_PEER_ADDRESS`.
#
# Additionally, modify the maximum log verbosity.  Select [Component config] ->
# [Log output] -> [Maximum log verbosity], and choose `Debug`.
#
# Flash the example by running `idf.py flash monitor`.
#
# Below is the default configuration of the example.
#
# client secret key: IsvT72MAXzA8EtV0FSD1QT59B4x0oe6Uea5rd/dDzhE=
# client public key: uyCfLulk5l7Bv/yCJ0nm1J3VL71YU4LISK/EHhwe43g=
#
# server secret key: iN8Rsdc10MFjkeqJ352OvtoMhkG5AFZWc/k4cS9odHM=
# server public key: FjrsQ/HD1Q8fUlFILIasDlOuajMeZov4NGqMJpkswiw=
#
# preshared key: 0/2H97Sd5EJ9LAAAYUglVjPYv7ihNIm/ziuv6BtSI50=
#
# wg(4) network: 192.168.4.0/24
# IP address of the server: 192.168.4.254
# allowed IP address of the client: 192.168.4.58
# the server port: 12912
#
# Other useful commands for the test:
#
# for details of wg(4) interface, run:
# ifconfig wg0
#
# to destroy wg(4), run:
# ifconfig wg0 destroy
#
# to create wg(4), run:
# sh /etc/netstart wg0
#
# to see packets from the client to the server, run:
# tcpdump -ni em1 host $ip.add.re.ss
#
# replace $ip.add.re.ss with the client IP address.
#
# to see decrypted packets, run:
# tcpdump -ni wg0
#
# to see debug log from wg(4), run:
# tail -f /var/log/messages

Vagrant.configure("2") do |config|
  config.vm.box = "trombik/ansible-openbsd-7.1-amd64"
  config.vm.network "public_network"
  config.vm.provision "shell", inline: <<-SHELL
    rcctl enable ntpd
    rcctl start ntpd
    touch /etc/hostname.wg0
    chmod 600 /etc/hostname.wg0
    echo "debug" >> /etc/hostname.wg0
    echo "wgkey iN8Rsdc10MFjkeqJ352OvtoMhkG5AFZWc/k4cS9odHM= wgport 12912" >> /etc/hostname.wg0
    echo "inet 192.168.4.254 255.255.255.0" >> /etc/hostname.wg0
    echo "wgpeer uyCfLulk5l7Bv/yCJ0nm1J3VL71YU4LISK/EHhwe43g= wgaip 192.168.4.58/32 wgpsk 0/2H97Sd5EJ9LAAAYUglVjPYv7ihNIm/ziuv6BtSI50=" >> /etc/hostname.wg0
    sh /etc/netstart wg0 start
  SHELL
end
