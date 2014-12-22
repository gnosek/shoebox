import logging
import shutil
import errno
import os

from shoebox.mount_namespace import FilesystemNamespace
from shoebox.namespaces import ContainerNamespace


logger = logging.getLogger('shoebox.rm')


def rm_layer(namespace):
    namespace.run(shutil.rmtree, '/', ignore_errors=True)


def remove_container(shoebox_dir, container_id, userns, volumes=False):
    runtime_dir = os.path.join(shoebox_dir, 'containers', container_id)
    target_base = os.path.join(runtime_dir, 'base')
    target_delta = os.path.join(runtime_dir, 'delta')
    target_root = os.path.join(runtime_dir, 'root')
    volume_root = os.path.join(runtime_dir, 'volumes')
    metadata_file = os.path.join(runtime_dir, 'metadata.json')

    if os.path.exists(target_root):
        os.rmdir(target_root)
    directories = [target_base, target_delta]
    if volumes:
        directories.append(volume_root)
    else:
        if os.path.exists(volume_root):
            logger.info('Preserving volumes in {0}'.format(volume_root))
    for directory in directories:
        if os.path.exists(directory):
            fs = FilesystemNamespace(directory)
            namespace = ContainerNamespace(fs, userns)
            logger.debug('Removing {0}'.format(directory))
            rm_layer(namespace)
            os.rmdir(directory)
    if os.path.exists(metadata_file):
        os.unlink(metadata_file)

    try:
        os.rmdir(runtime_dir)
        logger.info('Removed {0}'.format(container_id))
    except OSError as exc:
        if exc.errno == errno.ENOTEMPTY:
            logger.info('{0} not empty, not removing'.format(runtime_dir))
        else:
            raise
