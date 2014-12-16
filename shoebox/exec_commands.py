from collections import namedtuple
import errno
import os
import logging
from shoebox.tar import CopyFiles, DownloadFiles


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
        os.setgroups(list(groups))
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
        logger.info('COPY {0} -> {1}'.format(self.src_paths, self.dst_path))
        CopyFiles(exec_context.namespace, self.dst_path, exec_context.basedir, self.src_paths).run()


class AddCommand(namedtuple('AddCommand', 'src_paths dst_path')):
    def execute(self, exec_context):
        files = []
        urls = []

        for src in self.src_paths:
            if src.startswith('http://') or src.startswith('https://'):
                urls.append(src)
            else:
                files.append(src)

        if urls:
            DownloadFiles(exec_context.namespace, self.dst_path, exec_context.basedir or '.', urls).run()

        if files:
            if exec_context.basedir is None:
                logger.warning('Skipping ADD {0} -> {1} -- no base directory'.format(files, self.dst_path))
                return
            logger.info('ADD {0} -> {1}'.format(files, self.dst_path))
            # TODO: unpack archives (even though it's kind of dumb)
            CopyFiles(exec_context.namespace, self.dst_path, exec_context.basedir, files).run()
