import json
import logging
import click
import os
from shoebox.dockerfile import parse_dockerfile, to_docker_metadata, ExecContext
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
    runtime_dir = os.path.join(shoebox_dir, 'containers', container_id)
    target_base = os.path.join(runtime_dir, 'base')
    target_delta = os.path.join(runtime_dir, 'delta')
    target_root = os.path.join(runtime_dir, 'root')
    metadata_file = os.path.join(runtime_dir, 'metadata.json')
    repo = ImageRepository(index_url=index_url, storage_dir=storage_dir)

    parsed = parse_dockerfile(open(dockerfile_path).read(), repo=repo)

    repo.unpack(target_base, parsed.base_image_id, force)
    with open(metadata_file, 'w') as fp:
        json.dump(to_docker_metadata(container_id, parsed), fp, indent=4)

    exec_context = ExecContext(
        base=target_base,
        delta=target_delta,
        root=target_root,
        basedir=base_dir
    )
    for cmd in parsed.run_commands:
        try:
            cmd.execute(exec_context)
        except NotImplementedError:
            logger.error("Don't know how to run {0!r} yet".format(cmd))

    print container_id