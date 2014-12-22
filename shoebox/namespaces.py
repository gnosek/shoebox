from collections import defaultdict
import logging
import os

from shoebox.capabilities import drop_caps
from shoebox.libc import unshare, sethostname, CLONE_NEWUSER, CLONE_NEWNS, CLONE_NEWIPC, CLONE_NEWUTS, CLONE_NEWPID, \
    CLONE_NEWNET
from shoebox.user_namespace import UserNamespace


logger = logging.getLogger('shoebox')


class ContainerNamespace(object):
    def __init__(self, filesystem, user_namespace=None, private_net=None, hostname=None, links=None):
        self.filesystem = filesystem
        if user_namespace is None:
            self.user_namespace = UserNamespace(None, None)
        else:
            self.user_namespace = user_namespace
        self.private_net = private_net
        self.hostname = hostname
        self.links = links

    def __repr__(self):
        return 'FS: {0!r}, USER: {1!r}, NET: {2!r}'.format(self.filesystem, self.user_namespace, self.private_net)

    def linked_hostnames(self):
        if not self.links:
            return

        ip_names = defaultdict(set)
        ip_aliases = defaultdict(set)
        aliases = set()
        for link in self.links:
            ip_names[link.target_ip].add(link.source_container.container_id)
            ip_aliases[link.target_ip].add(link.alias)
            aliases.add(link.alias)

        for ip, names in ip_names.items():
            yield ip, list(ip_aliases[ip]) + [n for n in names if n not in aliases]

    def etc_hosts(self):
        base_hosts = """
127.0.0.1	localhost

# The following lines are desirable for IPv6 capable hosts
::1     localhost ip6-localhost ip6-loopback
ff02::1 ip6-allnodes
ff02::2 ip6-allrouters
"""
        hosts = []
        if self.private_net and self.private_net.ip_address and self.hostname:
            hosts.append('{0} {1}'.format(self.private_net.ip_address, self.hostname))

            for ip, names in self.linked_hostnames():
                hosts.append('{0} {1}'.format(ip, ' '.join(names)))

        return '\n'.join(hosts) + base_hosts

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
        if self.filesystem.special_fs:
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
