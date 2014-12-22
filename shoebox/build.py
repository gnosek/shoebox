import logging
import os

from shoebox.container import Container
from shoebox.dockerfile import ExecContext
from shoebox.namespaces import ContainerNamespace


logger = logging.getLogger('shoebox.build')


def build_container(base_dir, force, dockerfile, repo, shoebox_dir, userns):
    container_id = os.urandom(32).encode('hex')
    container = Container(shoebox_dir, container_id)
    repo.unpack(container.target_base, dockerfile.base_image_id, force)
    # noinspection PyProtectedMember
    dockerfile = dockerfile._replace(hostname='h' + container_id[:8])
    container.save_metadata(dockerfile)

    namespace = ContainerNamespace(container.build_filesystem(), userns)
    exec_context = ExecContext(namespace=namespace, basedir=base_dir)
    for cmd in dockerfile.run_commands:
        try:
            cmd.execute(exec_context)
        except NotImplementedError:
            logger.error("Don't know how to run {0!r} yet".format(cmd))

    return container
