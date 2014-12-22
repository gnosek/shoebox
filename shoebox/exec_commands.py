from collections import namedtuple
import errno
import logging

import os

from shoebox.tar import CopyFiles, DownloadFiles, detect_tar_format, UnpackArchive


logger = logging.getLogger('shoebox.exec_commands')


def get_passwd_id(path, key):
    try:
        for entry in open(path):
            fields = entry.strip().split(':')
            if fields[0] == key:
                return int(fields[2]), int(fields[3])
    except IOError:
        if key in ('root', ''):
            return 0, 0
        raise
    raise KeyError('{0} not found in {1}'.format(key, path))


def get_groups(path, user):
    groups = set()
    try:
        for entry in open(path):
            fields = entry.strip().split(':')
            if len(fields) > 3:
                members = fields[3].split(',')
                if user in members:
                    groups.add(int(fields[2]))
    except IOError:
        if user in ('root', ''):
            return {0}
        raise
    return groups


def exec_in_namespace(context, command):
    if os.geteuid() != 0:
        uid, gid = os.getuid(), os.getgid()
        groups = set(os.getgroups())
        if context.user not in ('root', ''):
            logger.warning('Ignoring request to switch to user {0}, running whole container as {1}:{2} already'.format(
                context.user, uid, gid))
    else:
        uid, gid = get_passwd_id('/etc/passwd', context.user)
        groups = get_groups('/etc/group', context.user)
    setgroups_fallback = False
    try:
        os.setgroups(list(groups) + [gid])
    except OSError as exc:
        if exc.errno == errno.EINVAL:
            # cannot map all the groups, e.g. when running in 1:1 uid map
            logger.warning('Failed to map groups, possibly due to direct uid/gid mapping')
            setgroups_fallback = True
        else:
            raise
    try:
        if setgroups_fallback:
            os.setgroups([gid])
        os.setgid(gid)
        os.setuid(uid)
        os.setegid(gid)
        os.seteuid(uid)
    except OSError as exc:
        if exc.errno == errno.EINVAL:
            logger.error(
                'Cannot switch to user {0} ({1}:{2}), possibly due to direct uid/gid mapping'.format(
                    context.user, uid, gid))
            # noinspection PyProtectedMember
            os._exit(1)

    os.chdir(context.workdir)
    os.execvpe(command[0], command, context.environ)


class RunCommand(namedtuple('RunCommand', 'command context')):
    def execute(self, exec_context):
        logger.info('RUN {0}'.format(self.command))
        exec_context.namespace.run(exec_in_namespace, self.context, self.command)


class CopyCommand(namedtuple('CopyCommand', 'src_paths dst_path')):
    def execute(self, exec_context):
        if exec_context.basedir is None:
            logger.warning('Skipping COPY {0} -> {1} -- no base directory'.format(self.src_paths, self.dst_path))
            return
        if len(self.src_paths) > 1 and not self.dst_path.endswith('/'):
            raise RuntimeError('With multiple source files target must be a directory (end with /)')
        logger.info('COPY {0} -> {1}'.format(self.src_paths, self.dst_path))
        CopyFiles(exec_context.namespace, self.dst_path, exec_context.basedir, self.src_paths).run()


def src_type(path):
    if path.startswith('http://') or path.startswith('https://'):
        return 'url'

    if detect_tar_format(path):
        return 'tar'

    return 'file'


class AddCommand(namedtuple('AddCommand', 'src_paths dst_path')):
    def handle_item(self, namespace, basedir, path):
        item_type = src_type(path)
        if item_type == 'url':
            basedir = basedir or os.getcwd()
            logger.info('Downloading {0} -> {1}'.format(path, self.dst_path))
            DownloadFiles(namespace, self.dst_path, basedir, [path]).run()
        elif item_type == 'tar':
            if not basedir:
                logger.warning('Skipping ADD {0} -> {1} -- no base directory'.format(path, self.dst_path))
            logger.info('Extracting {0} -> {1}'.format(path, self.dst_path))
            UnpackArchive(namespace, self.dst_path, basedir, path).run()
        else:
            logger.info('Copying {0} -> {1}'.format(path, self.dst_path))
            CopyFiles(namespace, self.dst_path, basedir, [path]).run()

    def execute(self, exec_context):
        if len(self.src_paths) > 1 and not self.dst_path.endswith('/'):
            raise RuntimeError('With multiple source files target must be a directory (end with /)')
        if all(src_type(src) == 'file' for src in self.src_paths):
            # no urls or archives, copy them in one go
            if exec_context.basedir is None:
                logger.warning('Skipping ADD {0} -> {1} -- no base directory'.format(self.src_paths, self.dst_path))
                return
            logger.info('Copying {0} -> {1}'.format(self.src_paths, self.dst_path))
            CopyFiles(exec_context.namespace, self.dst_path, exec_context.basedir, self.src_paths).run()
        else:
            # slow path, handle one item at a time
            for src in self.src_paths:
                self.handle_item(exec_context.namespace, exec_context.basedir, src)
