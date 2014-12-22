from _socket import AF_INET
from contextlib import contextmanager
import getpass
import os
import subprocess
try:
    import pyroute2
except ImportError:
    pyroute2 = None
from shoebox.namespace_utils import spawn_helper


def detect_bridge():
    username = getpass.getuser()
    with open('/etc/lxc/lxc-usernet') as usernet:
        for line in usernet:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            user, dev_type, bridge, count = line.split()
            if user != username:
                continue
            return bridge, dev_type
    return None, None


class PrivateNetwork(object):
    def __init__(self, bridge, ip_address, dev_type='veth'):
        if bridge == 'auto':
            bridge, dev_type = detect_bridge()
        self.bridge = bridge
        self.ip_address = ip_address
        self.gateway, self.prefixlen = self.gateway_settings()
        self.dev_type = dev_type

    def __repr__(self):
        return '{0}/{1} via {2}@{3}'.format(self.ip_address, self.prefixlen, self.gateway, self.bridge)

    def gateway_settings(self):
        """return ip/mask of bridge to use as default gateway"""
        iproute = pyroute2.IPRoute()
        bridge = iproute.link_lookup(ifname=self.bridge)[0]
        gateway = None
        for addr in iproute.get_addr(AF_INET):
            if addr['index'] != bridge:
                continue
            for name, value in addr['attrs']:
                if name == 'IFA_ADDRESS':
                    gateway = value
            return gateway, addr['prefixlen']

    def init_net_interface(self, pid):
        subprocess.check_output(['/usr/lib/x86_64-linux-gnu/lxc/lxc-user-nic', str(pid), self.dev_type, self.bridge])

    def set_ip_address(self):
        iproute = pyroute2.IPRoute()
        loopback = iproute.link_lookup(ifname='lo')[0]
        eth0 = iproute.link_lookup(ifname='eth0')[0]
        iproute.link('set', index=loopback, state='up')
        iproute.link('set', index=eth0, state='up')
        if self.ip_address:
            iproute.addr('add', index=eth0, address=self.ip_address, mask=self.prefixlen)
            if self.gateway:
                iproute.route('add', dst='0.0.0.0', mask=0, gateway=self.gateway)

    @contextmanager
    def setup_netns(self):
        if self.bridge:
            if pyroute2 is None:
                raise NotImplementedError()
            netns_helper = spawn_helper('netns', self.init_net_interface, os.getpid())
        else:
            netns_helper = None  # make PyCharm happy

        yield

        if self.bridge:
            netns_helper.wait()
            self.set_ip_address()