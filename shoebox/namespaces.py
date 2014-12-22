from ctypes import CDLL
import logging
import os

from shoebox.capabilities import drop_caps
from shoebox.user_namespace import setup_userns


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


class ContainerNamespace(object):
    def __init__(self, filesystem, target_uid=None, target_gid=None, private_net=None):
        self.target_uid = target_uid
        self.target_gid = target_gid
        self.filesystem = filesystem
        self.private_net = private_net

    def __repr__(self):
        return '<{id}> {layers!r} + {volumes!r} -> {target}, {target_uid}:{target_gid} special_fs:{special_fs}'.format(
            id=id(self), **self.__dict__)

    def create_userns(self):
        namespaces = CLONE_NEWUSER | CLONE_NEWNS | CLONE_NEWIPC | CLONE_NEWUTS | CLONE_NEWPID

        with setup_userns(self.target_uid, self.target_gid):
            if self.private_net:
                namespaces |= CLONE_NEWNET
                with self.private_net.setup_netns():
                    unshare(namespaces)
            else:
                unshare(namespaces)

    def build(self):
        self.filesystem.check_root_dir()
        self.create_userns()
        self.filesystem.build()
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
