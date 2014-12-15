import copy
import logging
import tarfile
import operator
import errno

import os


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

    # close the archive stream
    tar.close()


class ExtractTarBase(object):
    def __init__(self, namespace, dest_dir):
        self.namespace = namespace
        self.dest_dir = dest_dir

    def extract_from_fp(self, fp):
        # TODO: xz images
        try:
            tar = tarfile.open(fileobj=fp, mode='r|*')
        except tarfile.ReadError as exc:
            if exc.message == 'empty file':
                # oh well, this happens
                os._exit(0)
            raise

        # generally extracting arbitrary archives is insecure but we're
        # enclosed in the target namespace so if things break, damage is
        # limited to the container
        self.namespace.execns(extract, tar, self.dest_dir)

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


class ExtractTarFile(ExtractTarBase):
    # TODO: support xz archives via subprocess.Popen piping to tar inside

    def __init__(self, namespace, dest_dir, archive_path):
        super(ExtractTarFile, self).__init__(namespace, dest_dir)
        self.archive_path = archive_path

    def run(self):
        logger.info('Extracting {0} to {1} inside container'.format(self.archive_path, self.dest_dir))
        super(ExtractTarFile, self).run()

    def pre_setup(self):
        pass

    def parent_setup(self):
        pass

    def child_setup(self):
        return open(self.archive_path)


class CopyFiles(ExtractTarBase):
    def __init__(self, namespace, dest_dir, src_dir, members):
        super(CopyFiles, self).__init__(namespace, dest_dir)
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
