import logging

import click

import os
import re

from shoebox.build import build
from shoebox.container import Container
from shoebox.dockerfile import inherit_docker_metadata
from shoebox.exec_commands import exec_in_namespace
from shoebox.namespaces import ContainerNamespace
from shoebox.networking import PrivateNetwork
from shoebox.pull import DEFAULT_INDEX, ImageRepository
from shoebox.rm import remove_container
from shoebox.user_namespace import UserNamespace


def is_container_id(container_id):
    return re.match('^[0-9a-f]{64}$', container_id)


@click.command()
@click.argument('container_id', required=False)
@click.argument('command', nargs=-1)
@click.option('--shoebox-dir', default='~/.shoebox', help='base directory for downloads')
@click.option('--index-url', default=DEFAULT_INDEX, help='docker image index')
@click.option('--from', help='create new container from image')
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
        target_gid=None, force=False, rm=False, bridge=None, ip=None, **kwargs):
    logging.basicConfig(level=logging.INFO)

    shoebox_dir = os.path.expanduser(shoebox_dir)

    if bridge != 'None' and ip is not None:
        private_net = PrivateNetwork(bridge, ip)
    else:
        private_net = None

    userns = UserNamespace(target_uid, target_gid)

    from_image = kwargs.pop('from', None)
    if from_image is None:
        container = Container(shoebox_dir, container_id)
        container.load_metadata()
    else:
        if container_id is None:
            logging.error('Either container_id or --from image is required')
            os._exit(1)
        storage_dir = os.path.join(shoebox_dir, 'images')
        repo = ImageRepository(index_url=index_url, storage_dir=storage_dir)
        try:
            image_id, tag = from_image.split(':', 1)
        except ValueError:
            image_id, tag = from_image, 'latest'
        metadata = repo.metadata(image_id, tag)
        metadata = inherit_docker_metadata(metadata)
        metadata = metadata._replace(run_commands=[])
        container = build(None, force, metadata, repo, shoebox_dir, userns)

    namespace = ContainerNamespace(container.filesystem(), userns, private_net, hostname=container.metadata.hostname)

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

    environ = context.environ
    if 'TERM' in os.environ:
        environ['TERM'] = os.environ['TERM']
    if 'LANG' in os.environ:
        environ['LANG'] = os.environ['LANG']

    container.write_pidfile()
    if private_net and private_net.ip_address:
        container.write_ip_address(private_net.ip_address)
    try:
        namespace.run(exec_in_namespace, context, command)
        if rm:
            remove_container(shoebox_dir, container_id, userns, False)
    finally:
        container.cleanup_runtime_files()
