from contextlib import contextmanager
from ctypes import CDLL
import getpass
import itertools
import logging
import collections
import stat
import subprocess
import tempfile
import pyroute2

import os

from shoebox.capabilities import drop_caps


libc = CDLL('libc.so.6')

# linux/sched.h
CLONE_NEWNS = 0x00020000
CLONE_NEWUTS = 0x04000000
CLONE_NEWIPC = 0x08000000
CLONE_NEWNET = 0x40000000
CLONE_NEWUSER = 0x10000000
CLONE_NEWPID = 0x20000000

# linux/fs.h
MS_RDONLY = 1
MS_NOSUID = 2
MS_NODEV = 4
MS_NOEXEC = 8
MS_REMOUNT = 32
MS_BIND = 4096
MS_MOVE = 8192
MS_REC = 16384
MS_MGC_VAL = 0xC0ED0000

# sys/mount.h
MNT_DETACH = 2

logger = logging.getLogger('shoebox')


def unshare(flags):
    if libc.unshare(flags) != 0:
        # errno gets clobbered so that's all we know
        raise OSError('Failed to unshare {0:x}'.format(flags))


def load_id_map(path, base_id):
    username = getpass.getuser()
    lower_id = 0
    id_ranges = []
    can_map_self = False

    try:
        with open(path) as fp:
            for line in fp:
                map_login, id_min, id_count = line.strip().split(':')
                if map_login != username:
                    continue
                id_min = int(id_min)
                id_count = int(id_count)
                if id_min <= base_id < id_min + id_count:
                    can_map_self = True
                id_ranges.append((id_min, id_count))
    except IOError:
        return

    if id_ranges and not can_map_self:
        logger.warning(
            'Cannot map id {0} via {1}, consider adding: "{2}:{0}:1" or similar entry'.format(base_id, path, username))

    # arbitrary kernel limit of five entries
    # we're counting from 0
    for id_min, id_count in sorted(id_ranges)[:5]:
        yield lower_id, id_min, id_count
        lower_id += id_count


def apply_id_maps(pid, uid_map, gid_map):
    subprocess.check_call(['newuidmap', str(pid)] + [str(uid) for uid in uid_map])
    subprocess.check_call(['newgidmap', str(pid)] + [str(gid) for gid in gid_map])


def single_id_map(map_name, id_inside, id_outside):
    with open('/proc/self/{0}_map'.format(map_name), 'w') as fp:
        print >> fp, '{0} {1} 1'.format(id_inside, id_outside)


class PrivateNetwork(object):
    def __init__(self, bridge, ip_address, gateway, dev_type='veth'):
        self.bridge = bridge
        if ip_address:
            self.ip_address, prefixlen = ip_address.split('/', 1)
            self.prefixlen = int(prefixlen)
        else:
            self.ip_address = None
            self.prefixlen = None
        self.gateway = gateway
        self.dev_type = dev_type

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
        netns_helper = spawn_helper('netns', self.init_net_interface, os.getpid())

        yield

        netns_helper.wait()
        self.set_ip_address()

class Helper(collections.namedtuple('Helper', 'name pid wr_pipe')):

    def wait(self):
        os.close(self.wr_pipe)
        _, ret = os.waitpid(self.pid, 0)
        exitcode = ret >> 8
        exitsig = ret & 0x7f
        if exitsig:
            exitcode = exitsig + 128
        if exitcode:
            raise subprocess.CalledProcessError(cmd=self.name, returncode=exitcode)


def spawn_helper(name, func, *args, **kwargs):
    rd, wr = os.pipe()
    pid = os.fork()
    if pid == 0:  # child
        os.close(wr)
        os.read(rd, 1)
        exitcode = 1
        try:
            func(*args, **kwargs)
            exitcode = 0
        finally:
            os._exit(exitcode)
    else:
        os.close(rd)
        return Helper(name, pid, wr)


@contextmanager
def setup_userns(ugid_dict, target_uid=None, target_gid=None):
    uid_map = list(itertools.chain(*load_id_map('/etc/subuid', os.getuid())))
    gid_map = list(itertools.chain(*load_id_map('/etc/subgid', os.getgid())))
    uid, gid = os.getuid(), os.getgid()

    if not uid_map or not gid_map:
        logger.warning('No mapping found for current user in /etc/subuid or /etc/subgid, mapping root directly')
        target_uid = 0
        target_gid = 0

    idmap_helper = None

    if target_uid is None and target_gid is None:
        idmap_helper = spawn_helper('idmap', apply_id_maps, os.getpid(), uid_map, gid_map)
    elif target_uid is None or target_gid is None:
        raise RuntimeError('If either of target uid/gid is present both are required')

    yield

    if idmap_helper:
        try:
            idmap_helper.wait()
        except subprocess.CalledProcessError:
            logger.warning('UID/GID helper failed to run, mapping root directly')
            target_uid, target_gid = 0, 0

    if target_uid is not None:
        single_id_map('uid', target_uid, uid)
        single_id_map('gid', target_gid, gid)

    ugid_dict['uid'] = target_uid
    ugid_dict['gid'] = target_gid


def mount(device, target, fstype, flags, options):
    if libc.mount(device, target, fstype, flags | MS_MGC_VAL, options) < 0:
        raise OSError('Failed to mount {0} at {1}'.format(device, target))


def bind_mount(source, target, readonly=False, rec=False):
    flags = MS_BIND | MS_MGC_VAL
    if rec:
        flags |= MS_REC
    if readonly:
        flags |= MS_RDONLY
    if libc.mount(source, target, 'none', flags, None) < 0:
        raise OSError('Failed to bind mount {0} at {1}'.format(source, target))


def pivot_root(new_root, old_root):
    if libc.pivot_root(new_root, old_root) < 0:
        raise OSError('Failed to pivot root {0} -> {1}'.format(new_root, old_root))


def makedev(target_dir_func, name):
    target = target_dir_func(name)
    if not os.path.exists(target):
        with open(target, 'w') as fp:
            print >> fp, 'Dummy file to be overmounted by shoebox run'

    s = os.stat(target)
    if not s.st_mode & (stat.S_IFBLK | stat.S_IFCHR):
        bind_mount(name.encode('utf-8'), target.encode('utf-8'))


def mount_root_fs(target, overlayfs_layers):
    target = target.encode('utf-8')

    if overlayfs_layers is None:
        overlayfs_layers = []

    if overlayfs_layers and len(overlayfs_layers) != 2:
        raise NotImplementedError("Stacked overlayfs not supported (yet)")

    if overlayfs_layers:
        for layer in overlayfs_layers:
            if not os.path.exists(layer):
                os.makedirs(layer)
        lower, upper = overlayfs_layers
        lower = lower.encode('utf-8')
        upper = upper.encode('utf-8')
        mount('overlayfs', target, 'overlayfs', 0, 'lowerdir={0},upperdir={1}'.format(lower, upper))
    else:
        # make target a mount point, for pivot_root
        bind_mount(target, target)


def mount_volumes(target_dir_func, volumes):
    for volume_source, volume_target in volumes:
        real_target = target_dir_func(volume_target)
        if not os.path.exists(real_target):
            os.makedirs(real_target, 0o755)
        bind_mount(volume_source.encode('utf-8'), real_target.encode('utf-8'), rec=True)


def mount_devices(target_dir_func):
    devpts = target_dir_func('/dev/pts')
    ptmx = target_dir_func('/dev/ptmx')

    if not os.path.exists(devpts):
        os.makedirs(devpts, mode=0o755)

    mount('devpts', devpts.encode('utf-8'), 'devpts', MS_NOEXEC | MS_NODEV | MS_NOSUID, 'newinstance,ptmxmode=0666')
    if not os.path.exists(ptmx):
        os.symlink('pts/ptmx', ptmx)
    else:
        bind_mount(os.path.join(devpts, 'ptmx').encode('utf-8'), ptmx.encode('utf-8'))

    devshm = target_dir_func('/dev/shm')
    if os.path.exists(devshm):
        mount('devshm', devshm.encode('utf-8'), 'tmpfs', MS_NOEXEC | MS_NODEV | MS_NOSUID, None)

    devices = ('null', 'zero', 'tty', 'random', 'urandom')
    for dev in devices:
        makedev(target_dir_func, '/dev/' + dev)


def mount_procfs(target_dir_func):
    target_proc = target_dir_func('/proc')
    if not os.path.exists(target_proc):
        os.makedirs(target_proc, mode=0o755)
    mount('proc', target_proc.encode('utf-8'), 'proc', MS_NOEXEC | MS_NODEV | MS_NOSUID, None)
    for path in ('sysrq-trigger', 'sys', 'irq', 'bus'):
        abs_path = os.path.join(target_proc, path).encode('utf-8')
        bind_mount(abs_path, abs_path)
        bind_mount(abs_path, abs_path, readonly=True)


def mount_sysfs(target_dir_func):
    target_sys = target_dir_func('/sys').encode('utf-8')
    try:
        bind_mount('/sys', target_sys)
        bind_mount(target_sys, target_sys, readonly=True)
    except OSError:
        logger.debug('Failed to mount sysfs, probably not owned by us')


def mount_etc_files(target_dir_func):
    tmpfs = tempfile.mkdtemp(prefix='.etc', dir=target_dir_func('/'))
    mount('tmpfs', tmpfs.encode('utf-8'), 'tmpfs', MS_NOEXEC | MS_NODEV | MS_NOSUID, 'size=1m')
    for path in ('/etc/resolv.conf', '/etc/hosts', '/etc/hostname'):
        content = open(path).read()
        tmpfile = os.path.join(tmpfs, os.path.basename(path))
        with open(tmpfile, 'w') as fp:
            fp.write(content)
        target = target_dir_func(path)
        if not os.path.exists(target):
            open(target, 'w').close()
        bind_mount(tmpfile.encode('utf-8'), target.encode('utf-8'))
    libc.umount2(tmpfs.encode('utf-8'), MNT_DETACH)
    os.rmdir(tmpfs)


def pivot_namespace_root(target):
    target = target.encode('utf-8')
    old_root = tempfile.mkdtemp(prefix='.oldroot', dir=target)
    pivot_root(target, old_root)
    os.chdir('/')
    pivoted_old_root = '/' + os.path.basename(old_root)
    libc.umount2(pivoted_old_root, MNT_DETACH)
    os.rmdir(pivoted_old_root)


def create_namespaces(target, layers, volumes, special_fs=True, is_root=True):
    pid = os.fork()
    if pid:
        _, ret = os.waitpid(pid, 0)
        exitcode = ret >> 8
        sig = ret & 0x7f
        if sig:
            exitcode = 128 + sig
        os._exit(exitcode)

    def target_subdir(path):
        return os.path.join(target, path.lstrip('/'))

    mount_root_fs(target, layers)
    if volumes:
        mount_volumes(target_subdir, volumes)

    if special_fs:
        if is_root:
            mount_devices(target_subdir)
        else:
            logger.warning('Cannot mount devpts when not mapping to root, expect TTY malfunction')
        mount_procfs(target_subdir)
        mount_sysfs(target_subdir)
        mount_etc_files(target_subdir)
    pivot_namespace_root(target)


class ContainerNamespace(object):
    def __init__(self, target, layers, volumes=None, target_uid=None, target_gid=None, special_fs=True,
                 private_net=None):
        self.target = target
        self.layers = layers
        self.volumes = volumes
        self.target_uid = target_uid
        self.target_gid = target_gid
        self.special_fs = special_fs
        self.private_net = private_net

    def __repr__(self):
        return '<{id}> {layers!r} + {volumes!r} -> {target}, {target_uid}:{target_gid} special_fs:{special_fs}'.format(
            id=id(self), **self.__dict__)

    def create_userns(self):
        namespaces = CLONE_NEWUSER | CLONE_NEWNS | CLONE_NEWIPC | CLONE_NEWUTS | CLONE_NEWPID

        ugid_dict = {}
        with setup_userns(ugid_dict, self.target_uid, self.target_gid):
            if self.private_net:
                namespaces |= CLONE_NEWNET
                with self.private_net.setup_netns():
                    unshare(namespaces)
            else:
                unshare(namespaces)

        return ugid_dict['uid'], ugid_dict['gid']


    def build(self):
        if not os.path.exists(self.target):
            if self.layers:
                os.makedirs(self.target)
            else:
                raise RuntimeError('{0} does not exist'.format(self.target))

        target_uid, target_gid = self.create_userns()
        is_root = (target_uid == 0 and target_gid == 0)

        create_namespaces(self.target, self.layers, self.volumes, self.special_fs, is_root)
        drop_caps()
        os.seteuid(target_uid)
        os.setegid(target_gid)
        os.setuid(target_uid)
        os.setgid(target_gid)
        os.setgroups([target_gid])

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
