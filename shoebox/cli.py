import json
import logging
import os
import click
from shoebox.pull import DEFAULT_INDEX, ImageRepository


@click.group()
@click.option('--shoebox-dir', default='~/.shoebox', help='base directory for downloads')
@click.option('--index-url', default=DEFAULT_INDEX, help='docker image index')
@click.option('--debug/--no-debug', help='debugging output')
@click.pass_context
def cli(ctx, shoebox_dir, index_url, debug):
    ctx.obj['shoebox_dir'] = os.path.expanduser(shoebox_dir)
    ctx.obj['index_url'] = index_url

    storage_dir = os.path.join(ctx.obj['shoebox_dir'], 'images')
    ctx.obj['repo'] = ImageRepository(index_url=index_url, storage_dir=storage_dir)
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
@click.option('--tag', '-t', default='latest', help='tag to pull')
@click.pass_context
def metadata(ctx, image, tag):
    meta = ctx.obj['repo'].metadata(image, tag)
    print json.dumps(meta, indent=4)
