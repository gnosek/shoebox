from ctypes import CDLL
import click
import os
import logging

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


def build_uid_map(base_uid, subuid_count):
    uidmap = [
        # uid inside, uid outside, count
        (0, base_uid, 1)  # map base_uid to userns root
    ]

    if os.geteuid() == 0:
        # only root may map to others' uids
        uidmap.append(
            (1, (base_uid * subuid_count) + 1, subuid_count - 1)  # map rest of userns uid space
        )
    else:
        logging.warn('not running as root, setting up identity user map only')
    return '\n'.join('{0} {1} {2}'.format(*s) for s in uidmap)


def create_userns(subuid_count=100000):
    pid = os.fork()
    rd, wr = os.pipe()
    if pid == 0:  # child
        os.close(wr)
        os.read(rd, 1)
        parent = os.getppid()
        uid_map = build_uid_map(os.getuid(), subuid_count)
        with open('/proc/{0}/uid_map'.format(parent), 'w') as fp:
            print >> fp, uid_map
        with open('/proc/{0}/gid_map'.format(parent), 'w') as fp:
            print >> fp, uid_map
        os._exit(0)
    else:
        os.close(rd)
        unshare(CLONE_NEWUSER)
        os.close(wr)
        os.waitpid(pid, 0)
        os.setgroups([0])

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


def create_namespaces(overlay_lower, overlay_upper, target, volumes):
    if volumes is None:
        volumes = []

    unshare(CLONE_NEWNS|CLONE_NEWIPC|CLONE_NEWUTS|CLONE_NEWPID)
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

    mount('overlayfs', target, 'overlayfs', MS_NOSUID, 'lowerdir={0},upperdir={1}'.format(overlay_lower, overlay_upper))
    for volume_source, volume_target in volumes:
        real_target = target_subdir(volume_target)
        if not os.path.exists(real_target):
            os.makedirs(real_target, 0o755)
        bind_mount(volume_source, real_target, rec=True)

    target_proc = target_subdir('/proc')
    mount('proc', target_proc, 'proc', MS_NOEXEC|MS_NODEV|MS_NOSUID, None)
    for path in ('sysrq-trigger', 'sys', 'irq', 'bus'):
        abs_path = os.path.join(target_proc, path)
        bind_mount(abs_path, abs_path)
        bind_mount(abs_path, abs_path, readonly=True)

    target_sys = target_subdir('/sys')
    try:
        bind_mount('/sys', target_sys)
        bind_mount(target_sys, target_sys, readonly=True)
    except OSError:
        logger.debug('Failed to mount sysfs, probably not owned by us')

    old_root = target_subdir('/mnt')
    pivot_root(target, old_root)
    os.chdir('/')
    unmount_subtree('/mnt')


def drop_capabilities():
    pass


def run_image(image_id, volumes=None, containers_dir='containers', delta_dir='delta', runtime_dir='runtime', entry_point='/bin/bash', subuid_count=100000):
    source_dir = os.path.join(containers_dir.encode('utf-8'), str(image_id))
    if not os.path.exists(source_dir):
        raise RuntimeError('{0} does not exist'.format(source_dir))
    upper_dir = os.path.join(delta_dir.encode('utf-8'), str(image_id))
    if not os.path.exists(upper_dir):
        os.makedirs(upper_dir, mode=0o700)
    target_dir = os.path.join(runtime_dir.encode('utf-8'), str(image_id))
    if not os.path.exists(target_dir):
        os.makedirs(target_dir, mode=0o700)

    create_userns(subuid_count=subuid_count)
    create_namespaces(source_dir, upper_dir, target_dir, volumes)
    drop_capabilities()
    os.execv(entry_point, [entry_point])


@click.command()
@click.option('--volume', '-v', help='mount volume src:dest', multiple=True)
@click.option('--containers-dir', default='containers', help='container image repository')
@click.option('--delta-dir', default='delta', help='container overlay repository')
@click.option('--runtime-dir', default='runtime', help='container runtime directory')
@click.option('--subuid-count', default=100000, help='number of subuids to allocate')
@click.argument('image_id')
@click.argument('entry_point')
def cli(image_id, volume=None, containers_dir='containers', delta_dir='delta', runtime_dir='runtime', entry_point='/bin/bash', subuid_count=100000):
    logging.basicConfig(level=logging.INFO)
    if volume:
        volume = [v.split(':', 1) for v in volume]
    run_image(image_id, volume, containers_dir, delta_dir, runtime_dir, entry_point, subuid_count)
