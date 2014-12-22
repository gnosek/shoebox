import logging
import os

import click

from shoebox.container import Container
from shoebox.dockerfile import parse_dockerfile, ExecContext
from shoebox.namespaces import ContainerNamespace
from shoebox.pull import ImageRepository, DEFAULT_INDEX
from shoebox.user_namespace import UserNamespace


logger = logging.getLogger('shoebox.build')


def build(base_dir, force, dockerfile, repo, shoebox_dir, userns):
    container_id = os.urandom(32).encode('hex')
    container = Container(shoebox_dir, container_id)
    repo.unpack(container.target_base, dockerfile.base_image_id, force)
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


@click.command()
@click.argument('base_dir')
@click.option('--shoebox-dir', default='~/.shoebox', help='base directory for downloads')
@click.option('--index-url', default=DEFAULT_INDEX, help='docker image index')
@click.option('--force/--no-force', default=False, help='force download')
@click.option('--target-uid', '-U', help='UID inside container (default: use newuidmap)', type=click.INT)
@click.option('--target-gid', '-G', help='GID inside container (default: use newgidmap)', type=click.INT)
def cli(base_dir, shoebox_dir, index_url, force, target_uid, target_gid):
    logging.basicConfig(level=logging.INFO)
    os.chdir(base_dir)
    dockerfile_path = 'Dockerfile'

    shoebox_dir = os.path.expanduser(shoebox_dir)
    storage_dir = os.path.join(shoebox_dir, 'images')
    repo = ImageRepository(index_url=index_url, storage_dir=storage_dir)

    dockerfile = parse_dockerfile(open(dockerfile_path).read(), repo=repo)
    userns = UserNamespace(target_uid, target_gid)
    container = build(os.getcwd(), force, dockerfile, repo, shoebox_dir, userns)

    print container.container_id