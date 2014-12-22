import os

from shoebox.build import build_container
from shoebox.container import Container
from shoebox.dockerfile import inherit_docker_metadata
from shoebox.exec_commands import exec_in_namespace
from shoebox.namespaces import ContainerNamespace
from shoebox.rm import remove_container


def load_container(container_id, shoebox_dir):
    if container_id is None:
        raise RuntimeError('Either container_id or --from image is required')
    container = Container(shoebox_dir, container_id)
    try:
        container.load_metadata()
    except IOError:
        raise RuntimeError(
            'Cannot find container named {0}, '
            'check name or use run --from {0} to build container from repository image'.format(container_id))
    return container


def clone_image(force, from_image, repo, shoebox_dir, userns):
    try:
        image_id, tag = from_image.split(':', 1)
    except ValueError:
        image_id, tag = from_image, 'latest'
    metadata = repo.metadata(image_id, tag)
    metadata = inherit_docker_metadata(metadata)
    # noinspection PyProtectedMember
    metadata = metadata._replace(run_commands=[])
    container = build_container(None, force, metadata, repo, shoebox_dir, userns)
    return container


def run_container(container, userns, shoebox_dir, command, entrypoint, user=None, workdir=None,
                  rm=False, private_net=None, links=None, env=None):
    namespace = ContainerNamespace(
        container.filesystem(), userns, private_net, hostname=container.metadata.hostname, links=links)

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
        # noinspection PyProtectedMember
        context = context._replace(user=user)
    if workdir:
        # noinspection PyProtectedMember
        context = context._replace(workdir=workdir)

    environ = context.environ
    if 'TERM' in os.environ:
        environ['TERM'] = os.environ['TERM']
    if 'LANG' in os.environ:
        environ['LANG'] = os.environ['LANG']

    for l in links:
        environ.update(l.environ())

    if env:
        for var in env:
            k, v = var.split('=', 1)
            environ[k] = v

    container.write_pidfile()
    if private_net and private_net.ip_address:
        container.write_ip_address(private_net.ip_address)
    try:
        namespace.run(exec_in_namespace, context, command)
        if rm:
            remove_container(shoebox_dir, container.container_id, userns, False)
    finally:
        container.cleanup_runtime_files()
