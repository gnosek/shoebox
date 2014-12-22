import json
import os
import re

from shoebox.dockerfile import from_docker_metadata, to_docker_metadata
from shoebox.mount_namespace import FilesystemNamespace


def mangle_volume_name(vol):
    return vol.strip('/').replace('_', '__').replace('/', '_')


def is_container_id(container_id):
    return re.match('^[0-9a-f]{64}$', container_id)


class Container(object):
    def __init__(self, shoebox_dir, container_id):
        self.container_id = container_id
        self.container_base_dir = os.path.join(shoebox_dir, 'containers')
        self.runtime_dir = os.path.join(shoebox_dir, 'containers', container_id)
        self.metadata_file = os.path.join(self.runtime_dir, 'metadata.json')
        self.target_base = os.path.join(self.runtime_dir, 'base')
        self.target_delta = os.path.join(self.runtime_dir, 'delta')
        self.target_root = os.path.join(self.runtime_dir, 'root')
        self.volume_root = os.path.join(self.runtime_dir, 'volumes')
        self.pidfile = os.path.join(self.runtime_dir, 'pid')
        self.ip_address_file = os.path.join(self.runtime_dir, 'ip_address')
        self.metadata = None

    def load_metadata(self):
        self.metadata = from_docker_metadata(json.load(open(self.metadata_file)))

    def save_metadata(self, metadata):
        self.metadata = metadata
        with open(self.metadata_file, 'w') as fp:
            json.dump(to_docker_metadata(self.container_id, metadata), fp, indent=4)

    def volumes(self):
        volumes = []
        for vol in self.metadata.volumes:
            target = os.path.join(self.volume_root, mangle_volume_name(vol))
            while os.path.exists(target) and os.path.islink(target):
                target = os.readlink(target)
            if not os.path.exists(target):
                os.makedirs(target, mode=0o755)
            volumes.append((target, vol))
        return volumes

    def filesystem(self):
        layers = [self.target_base, self.target_delta]
        return FilesystemNamespace(self.target_root, layers, self.volumes(), True)

    def build_filesystem(self):
        return FilesystemNamespace(self.target_base)

    def write_pidfile(self):
        with open(self.pidfile, 'w') as fp:
            print >> fp, os.getpid()

    def pid(self):
        try:
            return int(open(self.pidfile).read())
        except (IOError, ValueError):
            return

    def write_ip_address(self, ip):
        with open(self.ip_address_file, 'w') as fp:
            print >> fp, ip

    def ip_address(self):
        try:
            return open(self.ip_address_file).read().strip()
        except (IOError, ValueError):
            return

    def cleanup_runtime_files(self):
        for p in (self.pidfile, self.ip_address_file):
            if os.path.exists(p):
                os.unlink(p)

    def tags(self):
        for tag in os.listdir(self.container_base_dir):
            tag_path = os.path.join(self.container_base_dir, tag)
            if not is_container_id(tag) and os.path.islink(tag_path):
                target = os.readlink(tag_path)
                if not target.startswith('/'):
                    target = os.path.abspath(os.path.join(self.container_base_dir, target))
                if target == self.runtime_dir:
                    yield tag


class ContainerLink(object):
    def __init__(self, container, alias):
        self.alias = alias
        self.source_container = container
        self.source_container.load_metadata()
        self.ports = self.source_container.metadata.expose
        if not self.ports:
            raise RuntimeError('Source container does not expose any ports')
        self.target_ip = self.source_container.ip_address()
        if not self.target_ip:
            raise RuntimeError('Source container has no IP address')

    def environ(self):
        env = {}
        lowest_port, lp_proto = sorted(self.ports)[0]
        label = self.alias.upper()
        env['{0}_NAME'.format(label)] = self.source_container.container_id
        env['{0}_PORT'.format(label)] = '{0}://{1}:{2}'.format(lp_proto, self.target_ip, lowest_port)
        for port, proto in self.ports:
            env['{0}_PORT_{1}_{2}'.format(label, port, proto.upper())] = '{0}://{1}:{2}'.format(
                proto, self.target_ip, port)
            env['{0}_PORT_{1}_{2}_PROTO'.format(label, port, proto.upper())] = proto.upper()
            env['{0}_PORT_{1}_{2}_PORT'.format(label, port, proto.upper())] = str(port)
            env['{0}_PORT_{1}_{2}_ADDR'.format(label, port, proto.upper())] = self.target_ip
        return env
