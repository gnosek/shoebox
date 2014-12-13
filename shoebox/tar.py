import tarfile
import tempfile
import os
from shoebox.namespaces import build_container_namespace


def copy_inside(base, delta, root, src_dir, members, dest_dir):

    rd, wr = os.pipe()
    pid = os.fork()
    if pid:
        os.close(rd)
        old_pwd = os.getcwd()
        try:
            os.chdir(src_dir)
            archive = os.fdopen(wr, 'w')
            tar = tarfile.open(fileobj=archive)
            for m in members:
                tar.add(m)
        finally:
            os.chdir(old_pwd)
        os.waitpid(pid, 0)
    else:
        os.close(wr)
        archive = os.fdopen(rd, 'r')
        tar = tarfile.open(fileobj=archive, mode='r')
        try:
            build_container_namespace(base, delta, root, target_uid=0, target_gid=0)

            # generally insecure but we're enclosed in the target namespace
            # so if things break, don't do that
            tar.extractall(dest_dir)
        finally:
            os._exit(0)


def unpack_inside(base, delta, root, archive_path, dest_dir):
    # TODO: support xz archives via subprocess.Popen piping to tar inside
    # will probably have to store uncompressed tar archive inside
    archive = tempfile.mkstemp(prefix='.image.', dir=root)
    pid = os.fork()
    if pid:
        os.waitpid(pid, 0)
        os.unlink(archive)
    else:
        try:
            build_container_namespace(base, delta, root, [archive_path, archive], target_uid=0, target_gid=0)
            tar = tarfile.open('/' + os.path.basename(archive), mode='r')
            tar.extractall(dest_dir)
        finally:
            os._exit(0)