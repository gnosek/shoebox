from collections import namedtuple
import os
from shoebox.tar import CopyFiles


def get_passwd_id(path, key):
    for entry in open(path):
        fields = entry.strip().split(':')
        if fields[0] == key:
            return int(fields[2]), int(fields[3])
    raise KeyError('{0} not found in {1}'.format(key, path))


class RunCommand(namedtuple('RunCommand', 'command context')):
    def execute(self, exec_context):
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
            uid, gid = get_passwd_id('/etc/passwd', self.context.user)
            # TODO: initgroups from /etc/group
            os.setgroups([gid])
            os.setgid(gid)
            os.setuid(uid)
            os.setegid(gid)
            os.seteuid(uid)
            os.chdir(self.context.workdir)
            os.execvpe(self.command[0], self.command, self.context.environ)


class CopyCommand(namedtuple('CopyCommand', 'src_paths dst_path')):
    def execute(self, exec_context):
        CopyFiles(exec_context.namespace, self.dst_path, exec_context.basedir, self.src_paths).run()


class AddCommand(namedtuple('AddCommand', 'src_paths dst_path')):
    def execute(self, exec_context):
        raise NotImplementedError()