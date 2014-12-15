import json
import logging

import click
import os
import re

from shoebox.build import build
from shoebox.dockerfile import from_docker_metadata, inherit_docker_metadata
from shoebox.exec_commands import exec_in_namespace
from shoebox.namespaces import ContainerNamespace
from shoebox.pull import DEFAULT_INDEX, ImageRepository
from shoebox.rm import remove_container


def is_container_id(container_id):
    return re.match('^[0-9a-f]{64}$', container_id)


def mangle_volume_name(vol):
    return vol.strip('/').replace('_', '__').replace('/', '_')


@click.command()
@click.argument('container_id')
@click.argument('command', nargs=-1)
@click.option('--shoebox-dir', default='~/.shoebox', help='base directory for downloads')
@click.option('--index-url', default=DEFAULT_INDEX, help='docker image index')
@click.option('--entrypoint', help='override image entrypoint')
@click.option('--target-uid', '-U', help='UID inside container (default: use newuidmap)', type=click.INT)
@click.option('--target-gid', '-G', help='GID inside container (default: use newgidmap)', type=click.INT)
@click.option('--force/--no-force', default=False, help='force download')
@click.option('--user', '-u', help='user to run as')
@click.option('--workdir', '-w', help='work directory')
@click.option('--rm/--no-rm', help='remove container after exit')
def run(container_id, shoebox_dir, index_url, command, entrypoint, user=None, workdir=None, target_uid=None,
        target_gid=None, force=False, rm=False):
    logging.basicConfig(level=logging.INFO)

    shoebox_dir = os.path.expanduser(shoebox_dir)

    if is_container_id(container_id):
        runtime_dir = os.path.join(shoebox_dir, 'containers', container_id)
        metadata_file = os.path.join(runtime_dir, 'metadata.json')
        target_base = os.path.join(runtime_dir, 'base')
        target_delta = os.path.join(runtime_dir, 'delta')
        target_root = os.path.join(runtime_dir, 'root')
        volume_root = os.path.join(runtime_dir, 'volumes')
        metadata = from_docker_metadata(json.load(open(metadata_file)))
    else:
        storage_dir = os.path.join(shoebox_dir, 'images')
        repo = ImageRepository(index_url=index_url, storage_dir=storage_dir)
        try:
            image_id, tag = container_id.split(':', 1)
        except ValueError:
            image_id, tag = container_id, 'latest'
        metadata = repo.metadata(image_id, tag)
        metadata = inherit_docker_metadata(metadata)
        metadata = metadata._replace(run_commands=[])
        container_id = build(None, force, metadata, repo, shoebox_dir, target_gid, target_uid)
        runtime_dir = os.path.join(shoebox_dir, 'containers', container_id)
        target_base = os.path.join(runtime_dir, 'base')
        target_delta = os.path.join(runtime_dir, 'delta')
        target_root = os.path.join(runtime_dir, 'root')
        volume_root = os.path.join(runtime_dir, 'volumes')

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

    context = metadata.context
    if user:
        context = context._replace(user=user)
    if workdir:
        context = context._replace(workdir=workdir)

    namespace = ContainerNamespace(target_root, [target_base, target_delta], volumes, target_uid, target_gid)

    if rm:
        namespace.run(exec_in_namespace, context, command)
        remove_container(shoebox_dir, container_id, False, target_uid, target_gid)
    else:
        namespace.execns(exec_in_namespace, context, command)
