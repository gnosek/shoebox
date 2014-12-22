import collections
import os
import subprocess


class Helper(collections.namedtuple('Helper', 'name pid wr_pipe')):

    def wait(self):
        os.close(self.wr_pipe)
        _, ret = os.waitpid(self.pid, 0)
        exitcode = ret >> 8
        exitsig = ret & 0x7f
        if exitsig:
            exitcode = exitsig + 128
        if exitcode:
            raise subprocess.CalledProcessError(cmd=self.name, returncode=exitcode)


def spawn_helper(name, func, *args, **kwargs):
    rd, wr = os.pipe()
    pid = os.fork()
    if pid == 0:  # child
        os.close(wr)
        os.read(rd, 1)
        exitcode = 1
        try:
            func(*args, **kwargs)
            exitcode = 0
        finally:
            os._exit(exitcode)
    else:
        os.close(rd)
        return Helper(name, pid, wr)