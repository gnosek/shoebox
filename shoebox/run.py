import json
import logging

import click
import os

from shoebox.dockerfile import from_docker_metadata
from shoebox.namespaces import ContainerNamespace


def mangle_volume_name(vol):
    return vol.strip('/').replace('_', '__').replace('/', '_')


@click.command()
@click.argument('container_id')
@click.argument('command', nargs=-1)
@click.option('--shoebox-dir', default='~/.shoebox', help='base directory for downloads')
@click.option('--entrypoint', help='override image entrypoint')
@click.option('--target-uid', '-u', help='UID inside container (default: use newuidmap)', type=click.INT)
@click.option('--target-gid', '-g', help='GID inside container (default: use newgidmap)', type=click.INT)
def run(container_id, shoebox_dir, command, entrypoint, target_uid=None, target_gid=None):
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger('shoebox.build')

    shoebox_dir = os.path.expanduser(shoebox_dir)
    runtime_dir = os.path.join(shoebox_dir, 'containers', container_id)
    metadata_file = os.path.join(runtime_dir, 'metadata.json')
    target_base = os.path.join(runtime_dir, 'base')
    target_delta = os.path.join(runtime_dir, 'delta')
    target_root = os.path.join(runtime_dir, 'root')
    volume_root = os.path.join(runtime_dir, 'volumes')

    metadata = from_docker_metadata(json.load(open(metadata_file)))
    volumes = []
    for vol in metadata.volumes:
        target = os.path.join(volume_root, mangle_volume_name(vol)).encode('utf-8')
        while os.path.exists(target) and os.path.islink(target):
            target = os.readlink(target)
        if not os.path.exists(target):
            os.makedirs(target, mode=0o755)
        volumes.append((target, vol))

    if entrypoint is None:
        entrypoint = metadata.entrypoint or []
    else:
        entrypoint = [entrypoint]

    if not command:
        command = metadata.command or []

    command = list(entrypoint) + list(command)
    if not command:
        command = ['bash']

    namespace = ContainerNamespace(target_root, [target_base, target_delta], volumes, target_uid, target_gid)
    namespace.build()
    os.execvpe(command[0], command, metadata.context.environ)
