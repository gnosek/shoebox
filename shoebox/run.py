import logging

import click

import os
import re

from shoebox.build import build
from shoebox.container import Container
from shoebox.dockerfile import inherit_docker_metadata
from shoebox.exec_commands import exec_in_namespace
from shoebox.networking import PrivateNetwork
from shoebox.pull import DEFAULT_INDEX, ImageRepository
from shoebox.rm import remove_container


def is_container_id(container_id):
    return re.match('^[0-9a-f]{64}$', container_id)


@click.command()
@click.argument('container_id')
@click.argument('command', nargs=-1)
@click.option('--shoebox-dir', default='~/.shoebox', help='base directory for downloads')
@click.option('--index-url', default=DEFAULT_INDEX, help='docker image index')
@click.option('--entrypoint', help='override image entrypoint')
@click.option('--target-uid', '-U', help='UID inside container (default: use newuidmap)', type=click.INT)
@click.option('--target-gid', '-G', help='GID inside container (default: use newgidmap)', type=click.INT)
@click.option('--bridge', default='auto', help='bridge to attach private network (requires lxc installed), None to disable')
@click.option('--ip', help='private IP address (when using --bridge)')
@click.option('--force/--no-force', default=False, help='force download')
@click.option('--user', '-u', help='user to run as')
@click.option('--workdir', '-w', help='work directory')
@click.option('--rm/--no-rm', help='remove container after exit')
def run(container_id, shoebox_dir, index_url, command, entrypoint, user=None, workdir=None, target_uid=None,
        target_gid=None, force=False, rm=False, bridge=None, ip=None):
    logging.basicConfig(level=logging.INFO)

    shoebox_dir = os.path.expanduser(shoebox_dir)

    if bridge != 'None':
        private_net = PrivateNetwork(bridge, ip)
    else:
        private_net = None

    if is_container_id(container_id):
        container = Container(shoebox_dir, container_id)
        container.load_metadata()
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
        container = build(None, force, metadata, repo, shoebox_dir, target_gid, target_uid)

    namespace = container.namespace(target_uid, target_gid, private_net)

    if entrypoint is None:
        entrypoint = container.metadata.entrypoint or []
    else:
        entrypoint = [entrypoint]

    if not command:
        command = container.metadata.command or []

    command = list(entrypoint) + list(command)
    if not command:
        command = ['bash']

    context = container.metadata.context
    if user:
        context = context._replace(user=user)
    if workdir:
        context = context._replace(workdir=workdir)

    container.write_pidfile()
    if private_net and private_net.ip_address:
        container.write_ip_address(private_net.ip_address)
    try:
        namespace.run(exec_in_namespace, context, command)
        if rm:
            remove_container(shoebox_dir, container_id, False, target_uid, target_gid)
    finally:
        container.cleanup_runtime_files()
