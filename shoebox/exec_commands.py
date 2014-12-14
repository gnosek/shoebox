from collections import namedtuple
import os
import logging
from shoebox.tar import CopyFiles


logger = logging.getLogger('shoebox.exec_commands')


def get_passwd_id(path, key):
    for entry in open(path):
        fields = entry.strip().split(':')
        if fields[0] == key:
            return int(fields[2]), int(fields[3])
    raise KeyError('{0} not found in {1}'.format(key, path))


def get_groups(path, user):
    groups = set()
    for entry in open(path):
        fields = entry.strip().split(':')
        if len(fields) > 3:
            members = fields[3].split(',')
            if user in members:
                groups.add(int(fields[2]))
    return groups


def exec_in_namespace(context, command):
    uid, gid = get_passwd_id('/etc/passwd', context.user)
    groups = get_groups('/etc/group', context.user)
    os.setgroups(list(groups))
    os.setgid(gid)
    os.setuid(uid)
    os.setegid(gid)
    os.seteuid(uid)
    os.chdir(context.workdir)
    os.execvpe(command[0], command, context.environ)


class RunCommand(namedtuple('RunCommand', 'command context')):
    def execute(self, exec_context):
        logger.info('RUN {0}'.format(self.command))
        pid = os.fork()
        if pid:
            _, ret = os.waitpid(pid, 0)
            exitcode = ret >> 8
            exitsig = ret & 0x7f
            if exitsig:
                raise RuntimeError('Command caught signal {0}'.format(exitsig))
            elif exitcode:
                raise RuntimeError('Command exited with status {0}'.format(exitcode))
        else:
            exec_context.namespace.build()
            exec_in_namespace(self.context, self.command)


class CopyCommand(namedtuple('CopyCommand', 'src_paths dst_path')):
    def execute(self, exec_context):
        logger.info('COPY {0} -> {1}'.format(self.src_paths, self.dst_path))
        CopyFiles(exec_context.namespace, self.dst_path, exec_context.basedir, self.src_paths).run()


class AddCommand(namedtuple('AddCommand', 'src_paths dst_path')):
    def execute(self, exec_context):
        logger.info('ADD {0} -> {1}'.format(self.src_paths, self.dst_path))
        # TODO: fetch remote URLs, unpack archives (even though it's kind of dumb)
        CopyFiles(exec_context.namespace, self.dst_path, exec_context.basedir, self.src_paths).run()
