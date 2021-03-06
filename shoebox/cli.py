import json
import logging
import os
import sys

import click

from shoebox import utils
from shoebox.build import build_container
from shoebox.container import Container, ContainerLink
from shoebox.dockerfile import parse_dockerfile
from shoebox.networking import PrivateNetwork
from shoebox.pull import DEFAULT_INDEX, ImageRepository
from shoebox.rm import remove_container
from shoebox.run import run_container, load_container, clone_image
from shoebox.user_namespace import UserNamespace


@click.group()
@click.option('--shoebox-dir', default='~/.shoebox', help='base directory for downloads')
@click.option('--index-url', default=DEFAULT_INDEX, help='docker image index')
@click.option('--debug/--no-debug', help='debugging output')
@click.pass_context
def cli(ctx, shoebox_dir, index_url, debug):
    shoebox_dir = os.path.expanduser(shoebox_dir)
    storage_dir = os.path.join(shoebox_dir, 'images')
    ctx.obj = {
        'shoebox_dir': shoebox_dir,
        'repo': ImageRepository(index_url=index_url, storage_dir=storage_dir),
        'logger': logging.getLogger('shoebox.cli')
    }

    if debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logger = logging.getLogger('shoebox')
        hdlr = logging.StreamHandler()
        fs = logging.BASIC_FORMAT
        fmt = logging.Formatter(fs)
        hdlr.setFormatter(fmt)
        logger.addHandler(hdlr)
        logger.setLevel(logging.INFO)


@cli.command()
@click.argument('image')
@click.option('--tag', '-t', default='latest', help='image tag (version)')
@click.pass_obj
def metadata(obj, image, tag):
    repo = obj['repo']
    meta = repo.metadata(image, tag)
    print json.dumps(meta, indent=4)


@cli.command()
@click.argument('image')
@click.option('--tag', '-t', default='latest', help='image tag (version)')
@click.pass_obj
def ancestry(obj, image, tag):
    repo = obj['repo']
    for image_id in repo.ancestry(image, tag):
        print image_id


@cli.command()
@click.argument('image')
@click.option('--force/--no-force', default=False, help='force download')
@click.option('--tag', '-t', default='latest', help='tag to pull')
@click.pass_obj
def pull(obj, image, tag, force):
    repo = obj['repo']
    repo.pull(image, tag, force)


@cli.command()
@click.option('--quiet/--no-quiet', '-q', help='quiet mode (only container ids)')
@click.pass_obj
def ls(obj, quiet):
    utils.ls(obj['shoebox_dir'], quiet)


@cli.command()
@click.pass_obj
def ps(obj):
    utils.ps(obj['shoebox_dir'])


@cli.command(name='tag')
@click.argument('container_id')
@click.argument('tag')
@click.option('--force/--no-force', help='overwrite tag if it already exists')
@click.pass_obj
def tag_container(obj, container_id, tag, force):
    try:
        utils.tag_container(obj['shoebox_dir'], container_id, tag, force)
    except RuntimeError as exc:
        obj['logger'].error(exc)
        sys.exit(1)


@cli.command()
@click.argument('tag')
@click.pass_obj
def untag(obj, tag):
    try:
        utils.untag(obj['shoebox_dir'], tag)
    except RuntimeError as exc:
        obj['logger'].error(exc)
        sys.exit(1)


@cli.command()
@click.argument('base_dir')
@click.option('--force/--no-force', default=False, help='force download')
@click.option('--target-uid', '-U', help='UID inside container (default: use newuidmap)', type=click.INT)
@click.option('--target-gid', '-G', help='GID inside container (default: use newgidmap)', type=click.INT)
@click.pass_obj
def build(obj, base_dir, force, target_uid, target_gid):
    repo = obj['repo']
    shoebox_dir = obj['shoebox_dir']

    os.chdir(base_dir)
    dockerfile = parse_dockerfile(open('Dockerfile').read(), repo=repo)
    userns = UserNamespace(target_uid, target_gid)
    container = build_container(os.getcwd(), force, dockerfile, repo, shoebox_dir, userns)

    print container.container_id


@cli.command()
@click.argument('container_id', required=False)
@click.argument('command', nargs=-1)
@click.option('--bridge', default='auto',
              help='bridge to attach private network (requires lxc installed), None to disable')
@click.option('--entrypoint', help='override image entrypoint')
@click.option('--env', '-e', multiple=True, help='extra environment variables')
@click.option('--force/--no-force', default=False, help='force download')
@click.option('--from', 'from_image', help='create new container from image')
@click.option('--ip', help='private IP address (when using --bridge)')
@click.option('--link', multiple=True, help='link containers')
@click.option('--rm/--no-rm', help='remove container after exit')
@click.option('--target-gid', '-G', help='GID inside container (default: use newgidmap)', type=click.INT)
@click.option('--target-uid', '-U', help='UID inside container (default: use newuidmap)', type=click.INT)
@click.option('--user', '-u', help='user to run as')
@click.option('--workdir', '-w', help='work directory')
@click.pass_obj
def run(obj, container_id, command, bridge, entrypoint, env, force, from_image, ip, link, rm,
        target_uid, target_gid, user, workdir):
    shoebox_dir = obj['shoebox_dir']
    repo = obj['repo']

    if bridge != 'None' and ip is not None:
        private_net = PrivateNetwork(bridge, ip)
    else:
        private_net = None

    links = []
    if link is not None:
        if private_net and ip:
            for l in link:
                source, alias = l.split(':', 1)
                link_ct = Container(shoebox_dir, source)
                links.append(ContainerLink(link_ct, alias))
        else:
            logging.warning('Ignoring container links when running without private networking')

    userns = UserNamespace(target_uid, target_gid)

    if from_image is None:
        container = load_container(container_id, shoebox_dir)
    else:
        container = clone_image(force, from_image, repo, shoebox_dir, userns)

    run_container(container, userns, shoebox_dir, command, entrypoint, user, workdir, rm, private_net, links, env)


@cli.command()
@click.argument('container_id', nargs=-1)
@click.option('--target-uid', '-U', help='UID inside container (default: use newuidmap)', type=click.INT)
@click.option('--target-gid', '-G', help='GID inside container (default: use newgidmap)', type=click.INT)
@click.option('--volumes/--no-volumes', '-v', help='Also remove container volumes')
@click.pass_obj
def rm(obj, container_id, target_uid, target_gid, volumes):
    shoebox_dir = obj['shoebox_repo']
    userns = UserNamespace(target_uid, target_gid)
    for container in container_id:
        remove_container(shoebox_dir, container, userns, volumes)
