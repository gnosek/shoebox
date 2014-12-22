from ctypes import CDLL

try:
    libc = CDLL('libc.so.6')
except OSError:
    libc = None

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

# linux/sched.h
CLONE_NEWNS = 0x00020000
CLONE_NEWUTS = 0x04000000
CLONE_NEWIPC = 0x08000000
CLONE_NEWNET = 0x40000000
CLONE_NEWUSER = 0x10000000
CLONE_NEWPID = 0x20000000


def mount(device, target, fstype, flags, options):
    if libc is None:
        raise NotImplementedError()
    device = device.encode('utf-8')
    target = target.encode('utf-8')
    fstype = fstype.encode('utf-8')
    if options is not None:
        options = options.encode('utf-8')
    if libc.mount(device, target, fstype, flags | MS_MGC_VAL, options) < 0:
        raise OSError('Failed to mount {0} at {1}'.format(device, target))


def bind_mount(source, target, readonly=False, rec=False):
    if libc is None:
        raise NotImplementedError()
    source = source.encode('utf-8')
    target = target.encode('utf-8')
    flags = MS_BIND | MS_MGC_VAL
    if rec:
        flags |= MS_REC
    if readonly:
        flags |= MS_RDONLY
    if libc.mount(source, target, 'none', flags, None) < 0:
        raise OSError('Failed to bind mount {0} at {1}'.format(source, target))


def pivot_root(new_root, old_root):
    if libc is None:
        raise NotImplementedError()
    new_root = new_root.encode('utf-8')
    old_root = old_root.encode('utf-8')
    if libc.pivot_root(new_root, old_root) < 0:
        raise OSError('Failed to pivot root {0} -> {1}'.format(new_root, old_root))


def umount(path):
    if libc is None:
        raise NotImplementedError()
    path = path.encode('utf-8')
    if libc.umount2(path, MNT_DETACH) < 0:
        raise OSError('Failed to unmount {0}'.format(path))


def unshare(flags):
    if libc is None:
        raise NotImplementedError()
    if libc.unshare(flags) != 0:
        # errno gets clobbered so that's all we know
        raise OSError('Failed to unshare {0:x}'.format(flags))


def sethostname(hostname):
    if libc is None:
        raise NotImplementedError()
    hostname = hostname.encode('utf-8')
    if libc.sethostname(hostname, len(hostname)) != 0:
        # errno gets clobbered so that's all we know
        raise OSError('Failed to sethostname {0}'.format(hostname))
