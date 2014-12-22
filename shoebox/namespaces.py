from ctypes import CDLL
import logging
import os

from shoebox.capabilities import drop_caps
from shoebox.user_namespace import UserNamespace


libc = CDLL('libc.so.6')
logger = logging.getLogger('shoebox')


# linux/sched.h
CLONE_NEWNS = 0x00020000
CLONE_NEWUTS = 0x04000000
CLONE_NEWIPC = 0x08000000
CLONE_NEWNET = 0x40000000
CLONE_NEWUSER = 0x10000000
CLONE_NEWPID = 0x20000000


def unshare(flags):
    if libc.unshare(flags) != 0:
        # errno gets clobbered so that's all we know
        raise OSError('Failed to unshare {0:x}'.format(flags))


def sethostname(hostname):
    h = hostname.encode('utf-8')
    if libc.sethostname(h, len(h)) != 0:
        # errno gets clobbered so that's all we know
        raise OSError('Failed to sethostname {0}'.format(hostname))


class ContainerNamespace(object):
    def __init__(self, filesystem, user_namespace=None, private_net=None, hostname=None):
        self.filesystem = filesystem
        if user_namespace is None:
            self.user_namespace = UserNamespace(None, None)
        else:
            self.user_namespace = user_namespace
        self.private_net = private_net
        self.hostname = hostname

    def __repr__(self):
        return 'FS: {0!r}, USER: {1!r}, NET: {2!r}'.format(self.filesystem, self.user_namespace, self.private_net)

    def etc_hosts(self):
        base_hosts = """
127.0.0.1	localhost

# The following lines are desirable for IPv6 capable hosts
::1     localhost ip6-localhost ip6-loopback
ff02::1 ip6-allnodes
ff02::2 ip6-allrouters
"""
        if self.private_net and self.private_net.ip_address and self.hostname:
            return '{0} {1}'.format(self.private_net.ip_address, self.hostname) + base_hosts
        return base_hosts

    def etc_resolv_conf(self):
        resolvconf = []
        for line in open('/etc/resolv.conf'):
            line = line.strip()
            if line.startswith('nameserver'):
                _, ns = line.split()
                if ns.startswith('127.') and self.private_net and self.private_net.ip_address:
                    resolvconf.append('nameserver {0}'.format(self.private_net.gateway))
                    continue
            resolvconf.append(line)
        return '\n'.join(resolvconf)

    def build(self):
        self.filesystem.check_root_dir()
        resolvconf = self.etc_resolv_conf()

        with self.user_namespace.setup_userns():
            namespaces = CLONE_NEWUSER | CLONE_NEWNS | CLONE_NEWIPC | CLONE_NEWUTS | CLONE_NEWPID
            if self.private_net:
                namespaces |= CLONE_NEWNET
                with self.private_net.setup_netns():
                    unshare(namespaces)
            else:
                unshare(namespaces)

        if self.hostname:
            sethostname(self.hostname)

        self.filesystem.build()
        with open('/etc/hosts', 'w') as hosts:
            print >> hosts, self.etc_hosts()

        with open('/etc/resolv.conf', 'w') as resolv:
            print >> resolv, resolvconf

        drop_caps()
        os.setgroups([os.getgid()])

    def execns(self, ns_func, *args, **kwargs):
        exitcode = 1
        # noinspection PyBroadException
        try:
            self.build()
            ns_func(*args, **kwargs)
            exitcode = 0
        except:
            logger.exception('Exception inside namespace {0!r}'.format(self))
        finally:
            os._exit(exitcode)

    def run(self, ns_func, *args, **kwargs):
        pid = os.fork()
        if pid:
            _, ret = os.waitpid(pid, 0)
            exitcode = ret >> 8
            exitsig = ret & 0x7f
            if exitsig:
                raise RuntimeError('Subprocess caught signal {0}'.format(exitsig))
            elif exitcode:
                raise RuntimeError('Subprocess exited with status {0}'.format(exitcode))
        else:
            self.execns(ns_func, *args, **kwargs)
