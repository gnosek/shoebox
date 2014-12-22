import os
import subprocess
import sys

import click

from shoebox.container import Container, is_container_id


@click.command()
@click.option('--shoebox-dir', default='~/.shoebox', help='base directory')
@click.option('--quiet/--no-quiet', '-q', help='quiet mode (only container ids)')
def ls(shoebox_dir, quiet):
    shoebox_dir = os.path.expanduser(shoebox_dir)
    container_dir = os.path.join(shoebox_dir, 'containers')
    for container_id in os.listdir(container_dir):
        if is_container_id(container_id):
            print container_id
            if quiet:
                continue
            container = Container(shoebox_dir, container_id)
            tags = list(container.tags())
            if tags:
                print 'tags:', ' '.join(tags)


@click.command()
@click.option('--shoebox-dir', default='~/.shoebox', help='base directory')
def ps(shoebox_dir):
    shoebox_dir = os.path.expanduser(shoebox_dir)
    container_dir = os.path.join(shoebox_dir, 'containers')
    for container_id in os.listdir(container_dir):
        container = Container(shoebox_dir, container_id)
        pid = container.pid()
        if not pid:
            continue
        print container_id
        ip = container.ip_address()
        if ip:
            print 'ip address:', ip
        tags = list(container.tags())
        if tags:
            print 'tags:', ' '.join(tags)
        subprocess.check_call(['pstree', '-ap', str(pid)])


@click.command()
@click.option('--shoebox-dir', default='~/.shoebox', help='base directory')
@click.option('--force/--no-force', help='overwrite tag if it already exists')
@click.argument('container_id')
@click.argument('tag')
def tag(shoebox_dir, container_id, tag, force):
    shoebox_dir = os.path.expanduser(shoebox_dir)
    container_dir = os.path.join(shoebox_dir, 'containers')

    if not is_container_id(container_id):
        print >> sys.stderr, 'Invalid container_id format'
        sys.exit(1)

    if is_container_id(tag):
        print >> sys.stderr, 'Tag cannot be a valid container id'
        sys.exit(1)

    container_path = os.path.join(container_dir, container_id)
    tag_path = os.path.join(container_dir, tag)

    if not os.path.isdir(container_path):
        print >> sys.stderr, 'Container does not exist'
        sys.exit(1)

    if os.path.exists(tag_path):
        if not force:
            print >> sys.stderr, 'Tag already exists'
            sys.exit(1)
        else:
            os.unlink(tag_path)

    os.symlink(container_id, tag_path)


@click.command()
@click.option('--shoebox-dir', default='~/.shoebox', help='base directory')
@click.argument('tag')
def untag(shoebox_dir, tag):
    shoebox_dir = os.path.expanduser(shoebox_dir)
    container_dir = os.path.join(shoebox_dir, 'containers')

    if is_container_id(tag):
        print >> sys.stderr, 'Tag cannot be a valid container id'
        sys.exit(1)

    tag_path = os.path.join(container_dir, tag)

    if os.path.islink(tag_path):
        os.unlink(tag_path)
