from ctypes import CDLL
import logging
import os
import stat
import tempfile


libc = CDLL('libc.so.6')
logger = logging.getLogger('shoebox')


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


class FilesystemNamespace(object):
    def __init__(self, target, layers=None, volumes=None, special_fs=False):
        self.target = target
        self.layers = layers
        self.volumes = volumes
        self.special_fs = special_fs

    def target_subdir(self, path):
        return os.path.join(self.target, path.lstrip('/'))

    def check_root_dir(self):
        if not os.path.exists(self.target):
            if self.layers:
                os.makedirs(self.target)
            else:
                raise RuntimeError('{0} does not exist'.format(self.target))

    def build(self):
        pid = os.fork()
        if pid:
            _, ret = os.waitpid(pid, 0)
            exitcode = ret >> 8
            sig = ret & 0x7f
            if sig:
                exitcode = 128 + sig
            os._exit(exitcode)

        mount_root_fs(self.target, self.layers)
        if self.volumes:
            mount_volumes(self.target_subdir, self.volumes)

        if self.special_fs:
            if os.geteuid() == 0:
                mount_devices(self.target_subdir)
            else:
                logger.warning('Cannot mount devpts when not mapping to root, expect TTY malfunction')
            mount_procfs(self.target_subdir)
            mount_sysfs(self.target_subdir)
            mount_etc_files(self.target_subdir)
        pivot_namespace_root(self.target)

