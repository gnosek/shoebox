from ctypes import CDLL
import getpass
import itertools
import logging
import subprocess
import tempfile

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
    with open(path) as fp:
        for line in fp:
            map_login, id_min, id_count = line.strip().split(':')
            if map_login != username:
                continue
            id_min = int(id_min)
            id_count = int(id_count)
            id_ranges.append(((id_min, id_count)))

    # arbitrary kernel limit of five entries
    # we're counting from 0
    for id_min, id_count in sorted(id_ranges)[:5]:
        yield lower_id, id_min, id_count
        lower_id += id_count


def apply_id_maps(pid):
    uid_map = itertools.chain(*load_id_map('/etc/subuid', os.getuid()))
    gid_map = itertools.chain(*load_id_map('/etc/subgid', os.getgid()))

    subprocess.check_call(['newuidmap', str(pid)] + [str(uid) for uid in uid_map])
    subprocess.check_call(['newgidmap', str(pid)] + [str(gid) for gid in gid_map])


def single_id_map(map_name, id_inside, id_outside):
    with open('/proc/self/{0}_map'.format(map_name), 'w') as fp:
        print >> fp, '{0} {1} 1'.format(id_inside, id_outside)


def create_userns(target_uid=None, target_gid=None):
    if target_uid is None and target_gid is None:
        pid = os.fork()
        rd, wr = os.pipe()
        if pid == 0:  # child
            os.close(wr)
            os.read(rd, 1)
            apply_id_maps(os.getppid())
            os._exit(0)
        else:
            os.close(rd)
            unshare(CLONE_NEWUSER)
            os.close(wr)
            os.waitpid(pid, 0)
            return 0, 0
    elif target_uid is None or target_gid is None:
        raise RuntimeError('If either of target uid/gid is present both are required')

    uid, gid = os.getuid(), os.getgid()

    unshare(CLONE_NEWUSER)
    single_id_map('uid', target_uid, uid)
    single_id_map('gid', target_gid, gid)


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


def unmount_subtree(tree):
    with open('/proc/mounts', 'r') as mounts:
        for line in reversed(list(mounts)):
            mnt = line.split()
            mountpoint = mnt[1]
            if mountpoint == tree or mountpoint.startswith(tree + '/'):
                libc.umount2(mnt[1], MNT_DETACH)


def makedev(target_dir_func, name):
    target = target_dir_func(name)
    if not os.path.exists(target):
        with open(target, 'w') as fp:
            print >> fp, 'Dummy file to be overmounted by shoebox run'
    elif True:  # TODO: if not device
        bind_mount(name.encode('utf-8'), target.encode('utf-8'))


def mount_root_fs(target, overlayfs_layers):
    target = target.encode('utf-8')

    if overlayfs_layers is None:
        overlayfs_layers = []

    if overlayfs_layers and len(overlayfs_layers) != 2:
        raise NotImplementedError("Stacked overlayfs not supported (yet)")

    if overlayfs_layers:
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

    mount('devpts', devpts.encode('utf-8'), 'devpts', MS_NOEXEC | MS_NODEV | MS_NOSUID, 'newinstance')
    if not os.path.exists(ptmx):
        os.symlink('pts/ptmx', ptmx)
    else:
        bind_mount(os.path.join(devpts, 'ptmx').encode('utf-8'), ptmx.encode('utf-8'))

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


def pivot_namespace_root(target):
    target = target.encode('utf-8')
    old_root = tempfile.mkdtemp(prefix='.oldroot', dir=target)
    pivot_root(target, old_root)
    os.chdir('/')
    pivoted_old_root = '/' + os.path.basename(old_root)
    unmount_subtree(pivoted_old_root)
    os.rmdir(pivoted_old_root)


def create_namespaces(target, overlay_lower, overlay_upper, volumes):
    if volumes is None:
        volumes = []

    unshare(CLONE_NEWNS | CLONE_NEWIPC | CLONE_NEWUTS | CLONE_NEWPID)
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

    if overlay_upper is not None and overlay_lower is not None:
        layers = [overlay_lower, overlay_upper]
    else:
        layers = None

    mount_root_fs(target, layers)
    if volumes:
        mount_volumes(target_subdir, volumes)

    mount_devices(target_subdir)
    mount_procfs(target_subdir)
    mount_sysfs(target_subdir)
    pivot_namespace_root(target)


def build_container_namespace(source_dir, delta_dir, runtime_dir, volumes=None, target_uid=None, target_gid=None):
    if source_dir and not os.path.exists(source_dir):
        raise RuntimeError('{0} does not exist'.format(source_dir))
    if delta_dir and not os.path.exists(delta_dir):
        os.makedirs(delta_dir, mode=0o755)
    if delta_dir and not os.path.exists(runtime_dir):
        os.makedirs(runtime_dir, mode=0o755)

    create_userns(target_uid, target_gid)

    if target_uid is None and target_gid is None:
        target_uid, target_gid = 0, 0
    create_namespaces(runtime_dir, source_dir, delta_dir, volumes)
    drop_caps()
    os.seteuid(target_uid)
    os.setegid(target_gid)
    os.setuid(target_uid)
    os.setgid(target_gid)
    os.setgroups([target_gid])
