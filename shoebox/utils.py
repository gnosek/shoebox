import os
import subprocess

from shoebox.container import Container, is_container_id


def ls(shoebox_dir, quiet):
    shoebox_dir = os.path.expanduser(shoebox_dir)
    container_dir = os.path.join(shoebox_dir, 'containers')
    for container_id in os.listdir(container_dir):
        if is_container_id(container_id):
            if quiet:
                print container_id
                continue
            print 'container id:', container_id
            container = Container(shoebox_dir, container_id)
            tags = list(container.tags())
            if tags:
                print '  tags:', ' '.join(tags)


def ps(shoebox_dir):
    shoebox_dir = os.path.expanduser(shoebox_dir)
    container_dir = os.path.join(shoebox_dir, 'containers')
    for container_id in os.listdir(container_dir):
        if not is_container_id(container_id):
            continue
        container = Container(shoebox_dir, container_id)
        pid = container.pid()
        if not pid:
            continue
        print container_id
        ip = container.ip_address()
        if ip:
            print '  ip address:', ip
        tags = list(container.tags())
        if tags:
            print '  tags:', ' '.join(tags)
        print '  process tree:'
        pstree = subprocess.check_output(['pstree', '-ap', str(pid)])
        for line in pstree.splitlines():
            print '    ' + line
        print


def tag_container(shoebox_dir, container_id, tag, force):
    shoebox_dir = os.path.expanduser(shoebox_dir)
    container_dir = os.path.join(shoebox_dir, 'containers')

    if not is_container_id(container_id):
        raise RuntimeError('Invalid container_id format')

    if is_container_id(tag):
        raise RuntimeError('Tag cannot be a valid container id')

    container_path = os.path.join(container_dir, container_id)
    tag_path = os.path.join(container_dir, tag)

    if not os.path.isdir(container_path):
        raise RuntimeError('Container does not exist')

    if os.path.exists(tag_path):
        if not force:
            raise RuntimeError('Tag already exists')
        else:
            os.unlink(tag_path)

    os.symlink(container_id, tag_path)


def untag(shoebox_dir, tag):
    shoebox_dir = os.path.expanduser(shoebox_dir)
    container_dir = os.path.join(shoebox_dir, 'containers')

    if is_container_id(tag):
        raise RuntimeError('Tag cannot be a valid container id')

    tag_path = os.path.join(container_dir, tag)

    if os.path.islink(tag_path):
        os.unlink(tag_path)
