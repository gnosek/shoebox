import logging
import click
import os
import shutil
import errno
from shoebox.namespaces import ContainerNamespace


logger = logging.getLogger('shoebox.rm')


def rm_layer(namespace):
    namespace.run(shutil.rmtree, '/', ignore_errors=True)


def remove_container(shoebox_dir, container_id, volumes=False, target_uid=None, target_gid=None):
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
            namespace = ContainerNamespace(directory, None, target_uid=target_uid, target_gid=target_gid, special_fs=False)
            logger.info('Removing {0}'.format(directory))
            rm_layer(namespace)
            os.rmdir(directory)
    if os.path.exists(metadata_file):
        os.unlink(metadata_file)

    try:
        os.rmdir(runtime_dir)
    except OSError as exc:
        if exc.errno == errno.ENOTEMPTY:
            logger.info('{0} not empty, not removing'.format(runtime_dir))
        else:
            raise


@click.command()
@click.argument('container_id')
@click.option('--shoebox-dir', default='~/.shoebox', help='base directory for downloads')
@click.option('--target-uid', '-U', help='UID inside container (default: use newuidmap)', type=click.INT)
@click.option('--target-gid', '-G', help='GID inside container (default: use newgidmap)', type=click.INT)
@click.option('--volumes/--no-volumes', '-v', help='Also remove container volumes')
def cli(container_id, shoebox_dir, target_uid=None, target_gid=None, volumes=False):
    logging.basicConfig(level=logging.INFO)

    shoebox_dir = os.path.expanduser(shoebox_dir)
    remove_container(shoebox_dir, container_id, volumes, target_uid, target_gid)
