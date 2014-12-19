import logging

import click
import os

from shoebox.container import Container
from shoebox.dockerfile import parse_dockerfile, ExecContext
from shoebox.pull import ImageRepository, DEFAULT_INDEX


logger = logging.getLogger('shoebox.build')


def build(base_dir, force, dockerfile, repo, shoebox_dir, target_gid, target_uid):
    container_id = os.urandom(32).encode('hex')
    container = Container(shoebox_dir, container_id)
    repo.unpack(container.target_base, dockerfile.base_image_id, force)
    container.save_metadata(dockerfile)

    namespace = container.build_namespace(target_uid, target_gid)
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
    dockerfile_path = os.path.join(base_dir, 'Dockerfile')

    shoebox_dir = os.path.expanduser(shoebox_dir)
    storage_dir = os.path.join(shoebox_dir, 'images')
    repo = ImageRepository(index_url=index_url, storage_dir=storage_dir)

    dockerfile = parse_dockerfile(open(dockerfile_path).read(), repo=repo)
    container = build(base_dir, force, dockerfile, repo, shoebox_dir, target_gid, target_uid)

    print container.container_id