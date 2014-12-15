import copy
import logging
import tarfile
import operator
import errno
import urlparse
import datetime

import os
import requests
from shoebox.namespaces import ContainerNamespace


logger = logging.getLogger('shoebox.tar')


class ContainerTarFile(tarfile.TarFile):

    def gettarinfo(self, name=None, arcname=None, fileobj=None):
        tarinfo = super(ContainerTarFile, self).gettarinfo(name, arcname, fileobj)
        tarinfo.uid = 0
        tarinfo.gid = 0
        tarinfo.uname = 'root'
        tarinfo.gname = 'root'
        return tarinfo

    def extractall(self, path='.', members=None):
        """Extract files, ignoring permission errors

        Done on one pass instead of extractall loop to support tar streams.
        Mostly copied from TarFile.extractall()
        """
        directories = []

        for tarinfo in self:
            if tarinfo.isdir():
                # Extract directories with a safe mode.
                directories.append(tarinfo)
                tarinfo = copy.copy(tarinfo)
                tarinfo.mode = 0700
            try:
                self.extract(tarinfo, path)
            except OSError as exc:
                if exc.errno == errno.EPERM:
                    logger.warning('Insufficient permissions to extract {0}, skipping'.format(tarinfo.name))

        # Reverse sort directories.
        directories.sort(key=operator.attrgetter('name'))
        directories.reverse()

        # Set correct owner, mtime and filemode on directories.
        for tarinfo in directories:
            dirpath = os.path.join(path, tarinfo.name)
            try:
                self.chown(tarinfo, dirpath)
                self.utime(tarinfo, dirpath)
                self.chmod(tarinfo, dirpath)
            except tarfile.ExtractError as e:
                logger.warning('Failed to set permissions/times on {0}: {1}'.format(dirpath, e))

        # close the archive stream
        self.close()


class ExtractTarBase(object):
    def __init__(self, namespace, dest_dir):
        self.namespace = namespace
        self.dest_dir = dest_dir

    def extract_from_fp(self, fp):
        # TODO: xz images
        try:
            tar = ContainerTarFile.open(fileobj=fp, mode='r|*')
        except tarfile.ReadError as exc:
            if exc.message == 'empty file':
                # oh well, this happens
                os._exit(0)
            raise

        # generally extracting arbitrary archives is insecure but we're
        # enclosed in the target namespace so if things break, damage is
        # limited to the container
        self.namespace.execns(tar.extractall, self.dest_dir)

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

    def add(self, tar, member):
        if os.path.isdir(member):
            tar.add(member, arcname='.')
        else:
            tar.add(member)

    def parent_setup(self):
        os.close(self.rpipe)
        archive = os.fdopen(self.wpipe, 'w')
        tar = ContainerTarFile.open(fileobj=archive, mode='w|')
        def tar_add():
            for m in self.members:
                self.add(tar, m)
            tar.close()
            archive.close()
        src_namespace = ContainerNamespace(
            self.src_dir, [], target_uid=self.namespace.target_uid, target_gid=self.namespace.target_gid, special_fs=False)
        src_namespace.run(tar_add)
        archive.close()

    def child_setup(self):
        os.close(self.wpipe)
        return os.fdopen(self.rpipe, 'r')


class DownloadFiles(CopyFiles):

    def add(self, tar, member):
        response = requests.get(member, stream=True)
        response.raise_for_status()
        parsed = urlparse.urlparse(member)
        basename = os.path.basename(parsed.netloc.rstrip('/'))
        tarinfo = tarfile.TarInfo(name=basename)
        try:
            size = int(response.headers['Content-Length'])
        except (KeyError, ValueError):
            size = None
        tarinfo.size = size
        tarinfo.mtime = datetime.datetime.now()
        tar.add(tarinfo, fileobj=response.raw)