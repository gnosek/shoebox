import click
import os
import subprocess
from shoebox.container import Container


@click.command()
@click.option('--shoebox-dir', default='~/.shoebox', help='base directory')
def ls(shoebox_dir):
    shoebox_dir = os.path.expanduser(shoebox_dir)
    container_dir = os.path.join(shoebox_dir, 'containers')
    for container_id in os.listdir(container_dir):
        print container_id


@click.command()
@click.option('--shoebox-dir', default='~/.shoebox', help='base directory')
def ps(shoebox_dir):
    shoebox_dir = os.path.expanduser(shoebox_dir)
    container_dir = os.path.join(shoebox_dir, 'containers')
    for container_id in os.listdir(container_dir):
        container = Container(shoebox_dir, container_id)
        ip = container.ip_address()
        if ip:
            print container_id, ip
        else:
            print container_id
        pid = container.pid()
        if pid:
            subprocess.check_call(['pstree', '-ap', str(pid)])