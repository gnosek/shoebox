from ctypes import CDLL
import getpass
import click
import itertools
import os
import logging
import subprocess
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
    with open(path) as fp:
        for n, line in enumerate(fp):
            map_login, id_min, id_count = line.strip().split(':')
            if map_login != username:
                continue
            id_min = int(id_min)
            id_count = int(id_count)
            yield lower_id, id_min, id_count
            lower_id += id_count
            if n == 4:
                # arbitrary kernel limit of five entries
                # we're counting from 0
                break


def apply_id_maps(pid):
    uid_map = itertools.chain(*load_id_map('/etc/subuid', os.getuid()))
    gid_map = itertools.chain(*load_id_map('/etc/subgid', os.getgid()))

    subprocess.check_call(['newuidmap', str(pid)] + [str(uid) for uid in uid_map])
    subprocess.check_call(['newgidmap', str(pid)] + [str(gid) for gid in gid_map])


def create_userns():
    pid = os.fork()
    rd, wr = os.pipe()
    if pid == 0:  # child
        os.close(wr)
        os.read(rd, 1)
        # todo: user identity mapping (w/o suid helper): uid -> uid or uid -> 0
        # will need to change setuid call later
        apply_id_maps(os.getppid())
        os._exit(0)
    else:
        os.close(rd)
        unshare(CLONE_NEWUSER)
        os.close(wr)
        os.waitpid(pid, 0)

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


def run_image(image_id, volumes=None, containers_dir='containers', delta_dir='delta', runtime_dir='runtime', entry_point='/bin/bash'):
    source_dir = os.path.join(containers_dir.encode('utf-8'), str(image_id))
    if not os.path.exists(source_dir):
        raise RuntimeError('{0} does not exist'.format(source_dir))
    upper_dir = os.path.join(delta_dir.encode('utf-8'), str(image_id))
    if not os.path.exists(upper_dir):
        os.makedirs(upper_dir, mode=0o755)
    target_dir = os.path.join(runtime_dir.encode('utf-8'), str(image_id))
    if not os.path.exists(target_dir):
        os.makedirs(target_dir, mode=0o755)

    create_userns()
    create_namespaces(source_dir, upper_dir, target_dir, volumes)
    drop_caps()
    os.seteuid(0)
    os.setegid(0)
    os.setuid(0)
    os.setgid(0)
    os.setgroups([0])
    os.execv(entry_point, [entry_point])


@click.command()
@click.option('--volume', '-v', help='mount volume src:dest', multiple=True)
@click.option('--containers-dir', default='containers', help='container image repository')
@click.option('--delta-dir', default='delta', help='container overlay repository')
@click.option('--runtime-dir', default='runtime', help='container runtime directory')
@click.argument('image_id')
@click.argument('entry_point')
def cli(image_id, volume=None, containers_dir='containers', delta_dir='delta', runtime_dir='runtime', entry_point='/bin/bash'):
    logging.basicConfig(level=logging.INFO)
    if volume:
        volume = [v.split(':', 1) for v in volume]
    run_image(image_id, volume, containers_dir, delta_dir, runtime_dir, entry_point)
