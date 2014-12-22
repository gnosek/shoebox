from contextlib import contextmanager
import getpass
import itertools
import logging
import os
import subprocess

from shoebox.namespace_utils import spawn_helper


logger = logging.getLogger('shoebox')


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


class UserNamespace(object):
    def __init__(self, target_uid=None, target_gid=None):
        self.target_uid = target_uid
        self.target_gid = target_gid

    def __repr__(self):
        return 'uid:{0} gid:{1}'.format(self.target_uid, self.target_gid)

    @contextmanager
    def setup_userns(self):
        uid_map = list(itertools.chain(*load_id_map('/etc/subuid', os.getuid())))
        gid_map = list(itertools.chain(*load_id_map('/etc/subgid', os.getgid())))
        uid, gid = os.getuid(), os.getgid()

        if not uid_map or not gid_map:
            logger.warning('No mapping found for current user in /etc/subuid or /etc/subgid, mapping root directly')
            target_uid = 0
            target_gid = 0

        idmap_helper = None

        if self.target_uid is None and self.target_gid is None:
            idmap_helper = spawn_helper('idmap', apply_id_maps, os.getpid(), uid_map, gid_map)
        elif self.target_uid is None or self.target_gid is None:
            raise RuntimeError('If either of target uid/gid is present both are required')

        yield

        if idmap_helper:
            try:
                idmap_helper.wait()
            except subprocess.CalledProcessError:
                logger.warning('UID/GID helper failed to run, mapping root directly')
                target_uid, target_gid = 0, 0

        if self.target_uid is not None:
            single_id_map('uid', self.target_uid, uid)
            single_id_map('gid', self.target_gid, gid)
