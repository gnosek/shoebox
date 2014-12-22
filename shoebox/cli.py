import json
import logging
import os
import sys

import click

from shoebox import utils
from shoebox.build import build_container
from shoebox.dockerfile import parse_dockerfile
from shoebox.pull import DEFAULT_INDEX, ImageRepository
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
