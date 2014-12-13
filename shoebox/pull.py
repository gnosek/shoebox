import json
import logging
import os
import requests
import click
import subprocess

DEFAULT_INDEX = 'https://index.docker.io'

class ImageRepository(object):

    def __init__(self, index_url=DEFAULT_INDEX, storage_dir='images'):
        self.index_url = index_url
        self.token = None
        self.repositories = []
        self.storage_dir = os.path.abspath(storage_dir)
        self.logger = logging.getLogger('shoebox.pull')

    def request_access(self, image):
        self.logger.info('Requesting access to image {0} at {1}'.format(image, self.index_url))
        response = requests.get('{0}/v1/repositories/{1}/images'.format(self.index_url, image), headers={'X-Docker-Token': 'true'})
        response.raise_for_status()

        self.token = response.headers.get('X-Docker-Token')
        repository_protocol = self.index_url.split(':', 1)[0]
        self.repositories = ['{0}://{1}'.format(repository_protocol, r.strip()) for r in response.headers['X-Docker-Endpoints'].split(',')]
        if not self.repositories:
            self.repositories = [self.index_url]

        logging.info('Auth token: {0}'.format(self.token))
        logging.info('Repository endpoints: {0}'.format(self.repositories))

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
            self.logger.info('Repository request: {0}'.format(repo_url))
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
            #  already downloaded
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
            #  already downloaded
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
                    self.logger.info(progress_format.format(downloaded >> 10))
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

    def unpack(self, target_dir, image, tag='latest', force_download=False):
        self.request_access(image)

        tags = self.list_tags(image)
        target_image_id = tags[tag]

        if not os.path.exists(target_dir):
            os.makedirs(target_dir, mode=0o755)

        for image_id in reversed(self.ancestors(target_image_id)):
            layer = self.download_image(image_id, force=force_download)
            subprocess.check_call(['tar', 'xf', layer, '-C', target_dir])

        self.logger.info('Unpacked {0}:{1} in {2}'.format(image, tag, target_dir))
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


@click.command()
@click.option('--storage-dir', default='~/.shoebox/images', help='image repository')
@click.option('--index-url', default=DEFAULT_INDEX, help='docker image index')
@click.option('--force/--no-force', default=False, help='force download')
@click.option('--tag', default='latest', help='tag to pull')
@click.argument('image')
def pull(image, tag='latest', force=False, index_url=DEFAULT_INDEX, storage_dir='images'):
    logging.basicConfig(level=logging.DEBUG)
    repo = ImageRepository(index_url=index_url, storage_dir=os.path.expanduser(storage_dir))
    metadata = repo.pull(image, tag, force)

    import pprint
    pprint.pprint(metadata[-1])

@click.command()
@click.option('--index-url', default=DEFAULT_INDEX, help='docker image index')
@click.option('--tag', default='latest', help='tag to pull')
@click.argument('image')
def ancestry(image, tag='latest', index_url=DEFAULT_INDEX, storage_dir='images'):
    logging.basicConfig(level=logging.DEBUG)
    repo = ImageRepository(index_url=index_url, storage_dir=storage_dir)
    for image_id in repo.ancestry(image, tag):
        print image_id

@click.command()
@click.option('--index-url', default=DEFAULT_INDEX, help='docker image index')
@click.option('--tag', default='latest', help='tag to pull')
@click.argument('image')
def metadata(image, tag='latest', index_url=DEFAULT_INDEX, storage_dir='images'):
    logging.basicConfig(level=logging.DEBUG)
    repo = ImageRepository(index_url=index_url, storage_dir=storage_dir)
    print json.dumps(repo.metadata(image, tag), indent=4)
