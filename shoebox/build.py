import logging
import click
import os
from shoebox.dockerfile import parse_dockerfile
from shoebox.pull import ImageRepository, DEFAULT_INDEX


@click.command()
@click.argument('base_dir')
@click.option('--shoebox-dir', default='~/.shoebox', help='base directory for downloads')
@click.option('--index-url', default=DEFAULT_INDEX, help='docker image index')
@click.option('--force/--no-force', default=False, help='force download')
def build(base_dir, shoebox_dir, index_url, force):
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger('shoebox.build')
    container_id = os.urandom(32).encode('hex')
    dockerfile_path = os.path.join(base_dir, 'Dockerfile')

    shoebox_dir = os.path.expanduser(shoebox_dir)
    storage_dir = os.path.join(shoebox_dir, 'images')
    runtime_dir = os.path.join(shoebox_dir, 'containers')
    target_base = os.path.join(runtime_dir, container_id, 'base')
    repo = ImageRepository(index_url=index_url, storage_dir=storage_dir)

    parsed = parse_dockerfile(open(dockerfile_path).read(), repo=repo)

    base_image, base_tag = parsed.base_image
    repo.unpack(target_base, base_image, base_tag, force)

    print container_id