import json
import logging
import click
import os
from shoebox.dockerfile import parse_dockerfile, to_docker_metadata, ExecContext
from shoebox.namespaces import ContainerNamespace
from shoebox.pull import ImageRepository, DEFAULT_INDEX

logger = logging.getLogger('shoebox.build')

def mangle_volume_name(vol):
    return vol.strip('/').replace('_', '__').replace('/', '_')

def build(base_dir, force, dockerfile, repo, shoebox_dir, target_gid, target_uid):
    container_id = os.urandom(32).encode('hex')
    runtime_dir = os.path.join(shoebox_dir, 'containers', container_id)
    target_base = os.path.join(runtime_dir, 'base')
    target_delta = os.path.join(runtime_dir, 'delta')
    target_root = os.path.join(runtime_dir, 'root')
    volume_root = os.path.join(runtime_dir, 'volumes')
    metadata_file = os.path.join(runtime_dir, 'metadata.json')
    repo.unpack(target_base, dockerfile.base_image_id, force)

    with open(metadata_file, 'w') as fp:
        json.dump(to_docker_metadata(container_id, dockerfile), fp, indent=4)

    volumes = []
    for vol in dockerfile.volumes:
        target = os.path.join(volume_root, mangle_volume_name(vol)).encode('utf-8')
        while os.path.exists(target) and os.path.islink(target):
            target = os.readlink(target)
        if not os.path.exists(target):
            os.makedirs(target, mode=0o755)
        volumes.append((target, vol))
    exec_context = ExecContext(
        namespace=ContainerNamespace(
            target=target_root,
            layers=[target_base, target_delta],
            target_uid=target_uid,
            target_gid=target_gid,
            special_fs=False,
        ),
        basedir=base_dir
    )
    for cmd in dockerfile.run_commands:
        try:
            cmd.execute(exec_context)
        except NotImplementedError:
            logger.error("Don't know how to run {0!r} yet".format(cmd))

    return container_id


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
    container_id = build(base_dir, force, dockerfile, repo, shoebox_dir, target_gid, target_uid)

    print container_id