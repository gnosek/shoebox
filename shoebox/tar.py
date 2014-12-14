import copy
import logging
import tarfile
import operator
import errno
import sys

import os

from shoebox.namespaces import build_container_namespace


logger = logging.getLogger('shoebox.tar')


def extract(tar, dest_dir):
    """Extract files, ignoring permission errors

    Done on one pass instead of extractall loop to support tar streams.
    Mostly copied from TarFile.extractall()
    """
    directories = []

    for tarinfo in tar:
        if tarinfo.isdir():
            # Extract directories with a safe mode.
            directories.append(tarinfo)
            tarinfo = copy.copy(tarinfo)
            tarinfo.mode = 0700
        try:
            tar.extract(tarinfo, dest_dir)
        except OSError as exc:
            if exc.errno == errno.EPERM:
                logger.warning('Insufficient permissions to extract {0}, skipping'.format(tarinfo.name))

    # Reverse sort directories.
    directories.sort(key=operator.attrgetter('name'))
    directories.reverse()

    # Set correct owner, mtime and filemode on directories.
    for tarinfo in directories:
        dirpath = os.path.join(dest_dir, tarinfo.name)
        try:
            tar.chown(tarinfo, dirpath)
            tar.utime(tarinfo, dirpath)
            tar.chmod(tarinfo, dirpath)
        except tarfile.ExtractError as e:
            logger.warning('Failed to set permissions/times on {0}: {1}'.format(dirpath, e))


class TarExtractor(object):
    def __init__(self, root, layers, dest_dir):
        self.root = root
        self.layers = layers
        self.dest_dir = dest_dir

    def extract_from_fp(self, fp):
        # TODO: xz images
        tar = tarfile.open(fileobj=fp, mode='r|*')
        exitcode = 1
        try:
            build_container_namespace(self.root, self.layers, target_uid=0, target_gid=0)
            # generally insecure but we're enclosed in the target namespace
            # so if things break, don't do that
            extract(tar, self.dest_dir)
            tar.close()
            exitcode = 0
        except Exception as exc:
            print >> sys.stderr, exc
        finally:
            os._exit(exitcode)

    def pre_setup(self):
        raise NotImplementedError()

    def parent_setup(self):
        raise NotImplementedError()

    def child_setup(self):
        """

        :rtype : file
        """
        raise NotImplementedError()

    def run(self):
        self.pre_setup()
        pid = os.fork()
        if pid:
            try:
                self.parent_setup()
            finally:
                _, ret = os.waitpid(pid, 0)
                exitcode = ret >> 8
                exitsig = ret & 0x7f
                if exitsig:
                    raise RuntimeError('Extraction caught signal {0}'.format(exitsig))
                elif exitcode:
                    raise RuntimeError('Extraction exited with status {0}'.format(exitcode))
        else:
            fp = self.child_setup()
            self.extract_from_fp(fp)


class TarFileExtractor(TarExtractor):
    # TODO: support xz archives via subprocess.Popen piping to tar inside

    def __init__(self, root, layers, dest_dir, archive_path):
        super(TarFileExtractor, self).__init__(root, layers, dest_dir)
        self.archive_path = archive_path

    def pre_setup(self):
        pass

    def parent_setup(self):
        pass

    def child_setup(self):
        return open(self.archive_path)


class FileCopier(TarExtractor):
    def __init__(self, root, layers, dest_dir, src_dir, members):
        super(FileCopier, self).__init__(root, layers, dest_dir)
        self.src_dir = src_dir
        self.members = members
        self.rpipe = None
        self.wpipe = None

    def pre_setup(self):
        self.rpipe, self.wpipe = os.pipe()

    def parent_setup(self):
        os.close(self.rpipe)
        old_cwd = os.getcwd()
        try:
            os.chdir(self.src_dir)
            archive = os.fdopen(self.wpipe, 'w')
            tar = tarfile.open(fileobj=archive, mode='w|')
            for m in self.members:
                # TODO: check absolute paths not stepping outside src_dir
                # maybe enclose in a namespace itself
                tar.add(m)
            tar.close()
            archive.close()
        finally:
            os.chdir(old_cwd)

    def child_setup(self):
        os.close(self.wpipe)
        return os.fdopen(self.rpipe, 'r')


def copy_inside(root, layers, dest_dir, src_dir, members):
    fc = FileCopier(root, layers, dest_dir, src_dir, members)
    fc.run()


def unpack_inside(root, layers, dest_dir, archive_path):
    tx = TarFileExtractor(root, layers, dest_dir, archive_path)
    tx.run()
