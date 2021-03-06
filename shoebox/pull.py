import json
import logging
import os

import requests

from shoebox import tar
from shoebox.mount_namespace import FilesystemNamespace
from shoebox.namespaces import ContainerNamespace


DEFAULT_INDEX = 'https://index.docker.io'


class ImageRepository(object):
    def __init__(self, index_url=DEFAULT_INDEX, storage_dir='images'):
        self.index_url = index_url
        self.token = None
        self.repositories = []
        self.storage_dir = os.path.abspath(storage_dir)
        self.logger = logging.getLogger('shoebox.pull')
        self.progress_logger = logging.getLogger('shoebox.progress')

    def request_access(self, image):
        self.logger.debug('Requesting access to image {0} at {1}'.format(image, self.index_url))
        response = requests.get('{0}/v1/repositories/{1}/images'.format(self.index_url, image),
                                headers={'X-Docker-Token': 'true'})
        response.raise_for_status()

        self.token = response.headers.get('X-Docker-Token')
        repository_protocol = self.index_url.split(':', 1)[0]
        self.repositories = ['{0}://{1}'.format(repository_protocol, r.strip()) for r in
                             response.headers['X-Docker-Endpoints'].split(',')]
        if not self.repositories:
            self.repositories = [self.index_url]

        self.logger.debug('Auth token: {0}'.format(self.token))
        self.logger.debug('Repository endpoints: {0}'.format(self.repositories))

    def repository_request(self, url, stream=False):
        if not self.repositories:
            raise RuntimeError('No repositories to choose from, did you run request_access() first?')

        if self.token:
            headers = {'Authorization': 'Token {0}'.format(self.token)}
        else:
            headers = {}

        response = requests.Response()
        response.status_code = 500
        for repo in self.repositories:
            repo_url = '{0}{1}'.format(repo, url)
            self.logger.debug('Repository request: {0}'.format(repo_url))
            response = requests.get(repo_url, headers=headers, stream=stream)
            if response.status_code == 404:
                response.raise_for_status()
            elif response.status_code == 200:
                return response
        else:
            response.raise_for_status()

    def list_tags(self, image):
        response = self.repository_request('/v1/repositories/{0}/tags'.format(image))
        return response.json()

    def ancestors(self, image_id):
        response = self.repository_request('/v1/images/{0}/ancestry'.format(image_id))
        return response.json()

    def image_metadata(self, image_id):
        response = self.repository_request('/v1/images/{0}/json'.format(image_id))
        return response.json()

    def image_layer(self, image_id):
        response = self.repository_request('/v1/images/{0}/layer'.format(image_id), stream=True)
        return response

    def download_metadata(self, image_id, force=False):
        path = os.path.join(self.storage_dir, image_id + '.json')
        if not force and os.path.exists(path):
            # already downloaded
            metadata = open(path)
            return json.load(metadata)

        if not os.path.exists(self.storage_dir):
            os.makedirs(self.storage_dir, mode=0o755)

        metadata = self.image_metadata(image_id)
        with open(path, 'w') as fp:
            fp.write(json.dumps(metadata))
        return metadata

    def download_image(self, image_id, force=False):
        path = os.path.join(self.storage_dir, image_id)
        if not force and os.path.exists(path):
            # already downloaded
            return path

        if not os.path.exists(self.storage_dir):
            os.makedirs(self.storage_dir, mode=0o755)
        with open(path, 'w') as fp:
            self.logger.info('Downloading image: {0}'.format(image_id))
            image = self.image_layer(image_id)
            resp_size = image.headers.get('Content-Length')
            if resp_size:
                progress_format = 'Downloaded: {{0}}/{0} KB'.format(int(resp_size) >> 10)
            else:
                progress_format = 'Downloaded: {0} KB'
            downloaded = 0
            for chunk in image.iter_content(chunk_size=1 << 16):
                if chunk:
                    downloaded += len(chunk)
                    self.progress_logger.info(progress_format.format(downloaded >> 10))
                    fp.write(chunk)
                    fp.flush()

        return path

    def pull(self, image, tag='latest', force_download=False):
        self.request_access(image)

        tags = self.list_tags(image)
        target_image_id = tags[tag]
        metadata = []
        for image_id in reversed(self.ancestors(target_image_id)):
            self.download_image(image_id, force=force_download)
            metadata.append(self.download_metadata(image_id, force=force_download))
        return metadata

    def unpack(self, target_dir, image_id, force_download=False):
        if not os.path.exists(target_dir):
            os.makedirs(target_dir, mode=0o755)

        for image_id in reversed(self.ancestors(image_id)):
            layer = self.download_image(image_id, force=force_download)
            fs = FilesystemNamespace(target_dir)
            namespace = ContainerNamespace(fs)
            tar.ExtractTarFile(namespace, '/', layer).run()

        self.logger.debug('Unpacked {0} in {1}'.format(image_id, target_dir))
        return target_dir

    def ancestry(self, image, tag='latest'):
        self.request_access(image)

        tags = self.list_tags(image)
        target_image_id = tags[tag]
        return list(reversed(self.ancestors(target_image_id)))

    def metadata(self, image, tag='latest', use_cache=True):
        self.request_access(image)

        tags = self.list_tags(image)
        target_image_id = tags[tag]
        if use_cache:
            cached_path = os.path.join(self.storage_dir, '{0}.json'.format(target_image_id))
            if os.path.exists(cached_path):
                return json.load(open(cached_path))
        return self.image_metadata(target_image_id)
