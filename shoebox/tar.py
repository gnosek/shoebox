import tarfile
import os
from shoebox.namespaces import build_container_namespace


def push_tar_file(base, delta, root, src_dir, members, dest_dir):

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
    else:
        os.close(wr)
        archive = os.fdopen(rd, 'r')
        tar = tarfile.open(fileobj=archive, mode='r')
        build_container_namespace(base, delta, root, target_uid=0, target_gid=0)

        # generally insecure but we're enclosed in the target namespace
        # so if things break, don't do that
        tar.extractall(dest_dir)