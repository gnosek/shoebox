import json

import os

from shoebox.dockerfile import from_docker_metadata, to_docker_metadata

from shoebox.namespaces import ContainerNamespace


def mangle_volume_name(vol):
    return vol.strip('/').replace('_', '__').replace('/', '_')


class Container(object):
    def __init__(self, shoebox_dir, container_id):
        self.container_id = container_id
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
            target = os.path.join(self.volume_root, mangle_volume_name(vol)).encode('utf-8')
            while os.path.exists(target) and os.path.islink(target):
                target = os.readlink(target)
            if not os.path.exists(target):
                os.makedirs(target, mode=0o755)
            volumes.append((target, vol))
        return volumes

    def namespace(self, target_uid, target_gid, private_net):
        layers = [self.target_base, self.target_delta]
        return ContainerNamespace(self.target_root, layers, self.volumes(), target_uid, target_gid, True, private_net)

    def build_namespace(self, target_uid, target_gid):
        return ContainerNamespace(self.target_base, None, None, target_uid, target_gid, special_fs=False)

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

