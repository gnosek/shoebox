from collections import namedtuple
import json
import datetime
import re

import pyparsing as p

from shoebox.exec_commands import RunCommand, CopyCommand, AddCommand


RunContext = namedtuple('RunContext', 'environ user workdir')
ExecContext = namedtuple('ExecContext', 'namespace basedir')

Dockerfile = namedtuple(
    'Dockerfile',
    'base_image base_image_id context run_commands expose entrypoint volumes command repo onbuild hostname')

eol = p.LineEnd().suppress()
sp = p.White().suppress()
ch = p.Literal
escaped_char = ch('\\').suppress() + p.Regex('.')

CommentLine = (p.LineStart() + ch('#') + p.restOfLine + eol).suppress()
EmptyLine = (p.LineStart() + eol).suppress()

nc_word = p.Word(p.printables + ' \t', excludeChars='\\')
nc_escape = ch('\\') + p.NotAny(eol)
nc_line = p.NotAny(ch('#')) + p.Combine(p.ZeroOrMore(nc_word | nc_escape))

DirectiveValue = p.Combine(
    p.ZeroOrMore(
        (nc_line + ch('\\').suppress() + eol) |
        CommentLine |
        EmptyLine
    ) +
    ((nc_line + eol) | p.stringEnd)
)


class Stanza(object):
    onbuild_allowed = True

    def __repr__(self):
        d = dict(self.__dict__)
        if len(d) == 1:
            return '{0}: {1[1]}'.format(self.__class__.__name__, d.popitem())
        return '{0}: {1!r}'.format(self.__class__.__name__, dict(self.__dict__))

    def evaluate(self, context):
        """

        :type context: Dockerfile
        """
        return context


class DockerfileCommand(object):
    parser = p.NoMatch()

    @classmethod
    def parse(cls, value):
        return cls.parser.parseString(value, parseAll=True)


class FromDockerfileCommand(DockerfileCommand):
    class FromCommand(Stanza):

        onbuild_allowed = False

        def __init__(self, tokens):
            self.image_name = tokens['image_name']
            self.tag = tokens['tag'][0]

        def __str__(self):
            return 'FROM {0}:{1}'.format(self.image_name, self.tag)

        def evaluate(self, context):
            """

            :type context: Dockerfile
            """
            assert context.base_image_id is None
            if context.repo is None:
                # noinspection PyProtectedMember
                context = context._replace(base_image=(self.image_name, self.tag))
            else:
                metadata = context.repo.metadata(self.image_name, self.tag)
                context = inherit_docker_metadata(metadata)
            return context

    image_name = p.Word(p.alphanums + './-')
    tag = ch(':').suppress() + p.Word(p.alphanums + '.-')

    parser = (image_name('image_name') + p.Optional(tag, default='latest')('tag')).setParseAction(FromCommand)


def env_quoted_string(tokens):
    return EnvRefCommand.env_value.parseString(tokens[0], parseAll=True)


class EnvRefCommand(DockerfileCommand):
    class EnvReference(Stanza):
        def __init__(self, tokens):
            self.reference = tokens['ref']

        def __str__(self):
            return '${' + self.reference + '}'

        def expand(self, environ):
            return environ[self.reference]

    class EnvStaticString(Stanza):
        def __init__(self, tokens):
            self.string = tokens[0]

        def __str__(self):
            return self.string.replace('\\', '\\\\').replace('$', '\\')

        # noinspection PyUnusedLocal
        def expand(self, environ):
            return self.string

    env_var = p.Word(p.alphas + '_', bodyChars=p.alphanums + '_')

    env_ref = \
        p.Combine(ch('$') + env_var('ref')) | \
        p.Combine(ch('${') + env_var('ref') + ch('}').suppress())

    env_value = p.OneOrMore(
        p.MatchFirst((
            p.Combine(escaped_char).setParseAction(EnvStaticString),
            env_ref.leaveWhitespace().setParseAction(EnvReference),
            p.Word(p.printables + ' \t', excludeChars='$\\').leaveWhitespace().setParseAction(EnvStaticString)
        ))
    )

    env_word_value = p.Group(
        (
            p.QuotedString('"', escChar='\\', multiline=True) |
            p.QuotedString("'", escChar='\\', multiline=True) |
            p.Combine(p.OneOrMore(p.Word(p.printables, excludeChars='\\') | escaped_char))
        ).leaveWhitespace().setParseAction(env_quoted_string)
    )


class EnvDockerfileCommand(EnvRefCommand):
    class EnvVariable(Stanza):
        def __init__(self, tokens):
            self.name = tokens['name']
            self.value = list(tokens['value'])

        def __str__(self):
            return 'ENV {0} {1}'.format(self.name, ''.join(str(v) for v in self.value))

        def evaluate(self, context):
            environ = dict(context.context.environ)
            environ[self.name] = ''.join(v.expand(environ) for v in self.value)
            # noinspection PyProtectedMember
            subcontext = context.context._replace(environ=environ)
            # noinspection PyProtectedMember
            return context._replace(context=subcontext)

    env_var = EnvRefCommand.env_var
    env_value = EnvRefCommand.env_value
    env_word_value = EnvRefCommand.env_word_value

    env_spaced = (env_var('name') + sp + env_value('value')).setParseAction(EnvVariable)
    env_kw = (env_var('name') + ch('=').suppress() + env_word_value('value')).setParseAction(EnvVariable)

    parser = env_spaced | p.OneOrMore(env_kw)


class WorkdirDockerfileCommand(EnvRefCommand):
    class WorkDir(Stanza):
        def __init__(self, tokens):
            self.workdir = tokens['workdir']

        def __str__(self):
            return 'WORKDIR {0}'.format(''.join(str(v) for v in self.workdir))

        def evaluate(self, context):
            environ = context.context.environ
            workdir = ''.join(v.expand(environ) for v in self.workdir)
            # noinspection PyProtectedMember
            subcontext = context.context._replace(workdir=workdir)
            # noinspection PyProtectedMember
            return context._replace(context=subcontext)

    parser = EnvRefCommand.env_word_value('workdir').setParseAction(WorkDir)


class ExposeDockerfileCommand(EnvRefCommand):
    class ExposePort(Stanza):
        def __init__(self, tokens):
            self.port = tokens['port']

        def __str__(self):
            return 'EXPOSE {0}'.format(''.join(str(v) for v in self.port))

        def evaluate(self, context):
            environ = context.context.environ
            port = ''.join(v.expand(environ) for v in self.port)
            if '/' in port:
                port, proto = port.split('/', 1)
                port = int(port)
            else:
                proto = 'tcp'
                port = int(port)
            # noinspection PyProtectedMember
            context = context._replace(expose=context.expose | {(port, proto)})
            return context

    single_parser = EnvRefCommand.env_word_value('port').setParseAction(ExposePort)
    parser = single_parser + p.ZeroOrMore(sp + single_parser)


class AddDockerfileCommand(EnvRefCommand):
    class Add(Stanza):
        def __init__(self, tokens):
            path_list = tokens['path_list'].asList()
            self.sources = path_list[:-1]
            self.destination = path_list[-1]

        def __str__(self):
            return 'ADD {0} {1}'.format(' '.join([''.join(str(v) for v in src) for src in self.sources]),
                                        ''.join(str(v) for v in self.destination))

        def evaluate(self, context):
            environ = context.context.environ
            sources = [''.join(v.expand(environ) for v in src) for src in self.sources]
            destination = ''.join(v.expand(environ) for v in self.destination)
            commands = list(context.run_commands)
            commands.append(AddCommand(sources, destination))
            # noinspection PyProtectedMember
            context = context._replace(run_commands=commands)
            return context

    single_parser = EnvRefCommand.env_word_value('path')
    parser = p.Group(single_parser + p.ZeroOrMore(sp + single_parser))('path_list').setParseAction(Add)


class CopyDockerfileCommand(EnvRefCommand):
    class Copy(Stanza):
        def __init__(self, tokens):
            path_list = tokens['path_list'].asList()
            self.sources = path_list[:-1]
            self.destination = path_list[-1]

        def __str__(self):
            return 'COPY {0} {1}'.format(' '.join([''.join(str(v) for v in src) for src in self.sources]),
                                         ''.join(str(v) for v in self.destination))

        def evaluate(self, context):
            environ = context.context.environ
            sources = [''.join(v.expand(environ) for v in src) for src in self.sources]
            destination = ''.join(v.expand(environ) for v in self.destination)
            commands = list(context.run_commands)
            commands.append(CopyCommand(sources, destination))
            # noinspection PyProtectedMember
            context = context._replace(run_commands=commands)
            return context

    single_parser = EnvRefCommand.env_word_value('path')
    parser = p.Group(single_parser + p.ZeroOrMore(sp + single_parser))('path_list').setParseAction(Copy)


class VolumeDockerfileCommand(EnvRefCommand):
    class Volume(Stanza):
        def __init__(self, tokens):
            self.path = tokens['path']

        def __str__(self):
            return 'VOLUME {0}'.format(''.join(str(v) for v in self.path))

        def evaluate(self, context):
            environ = context.context.environ
            path = ''.join(v.expand(environ) for v in self.path)
            volumes = set(context.volumes)
            volumes.add(path)
            # noinspection PyProtectedMember
            return context._replace(volumes=volumes)

    parser = EnvRefCommand.env_word_value('path').setParseAction(Volume)
    multi_parser = parser + p.ZeroOrMore(sp + parser)

    @classmethod
    def parse(cls, value):
        try:
            paths = []
            for v in json.loads(value):
                paths.extend(cls.parser.parseString(v, parseAll=True).asList())
        except ValueError:
            paths = cls.multi_parser.parseString(value, parseAll=True).asList()
        return paths


class ExecCommand(EnvRefCommand):
    @classmethod
    def parse_maybe_json(cls, value):
        try:
            return json.loads(value)
        except ValueError:
            return ['/bin/sh', '-c', value]


def format_exec_command(keyword, value):
    if value[:2] == ['/bin/sh', '-c'] and len(value) == 3:
        return '{0} {1}'.format(keyword, value[2])
    return '{0} {1}'.format(keyword, json.dumps(value))


class UserDockerfileCommand(EnvRefCommand):
    class User(Stanza):
        def __init__(self, tokens):
            self.name = tokens['name']

        def __str__(self):
            return 'USER {0}'.format(''.join(str(v) for v in self.name))

        def evaluate(self, context):
            environ = context.context.environ
            username = ''.join(v.expand(environ) for v in self.name)
            # noinspection PyProtectedMember
            subcontext = context.context._replace(user=username)
            # noinspection PyProtectedMember
            return context._replace(context=subcontext)

    parser = EnvRefCommand.env_word_value('name').setParseAction(User)


class MaintainerDockerfileCommand(EnvRefCommand):
    class Maintainer(Stanza):
        onbuild_allowed = False

        def __init__(self, maintainer):
            self.maintainer = maintainer

        def __str__(self):
            return 'MAINTAINER {0}'.format(self.maintainer)

    @classmethod
    def parse(cls, value):
        return cls.Maintainer(value)


class InsertDockerfileCommand(EnvRefCommand):
    @classmethod
    def parse(cls, value):
        return p.ParseResults([])


class RunDockerfileCommand(ExecCommand):
    class RunCommand(Stanza):
        def __init__(self, command):
            self.command = command

        def __str__(self):
            return format_exec_command('RUN', self.command)

        def evaluate(self, context):
            commands = list(context.run_commands)
            commands.append(RunCommand(self.command, context.context))
            # noinspection PyProtectedMember
            context = context._replace(run_commands=commands)
            return context

    @classmethod
    def parse(cls, value):
        return cls.RunCommand(cls.parse_maybe_json(value))


class CmdDockerfileCommand(ExecCommand):
    class Cmd(Stanza):
        def __init__(self, command):
            self.command = command

        def __str__(self):
            return format_exec_command('CMD', self.command)

        def evaluate(self, context):
            # noinspection PyProtectedMember
            return context._replace(command=self.command)

    @classmethod
    def parse(cls, value):
        return cls.Cmd(cls.parse_maybe_json(value))


class EntrypointDockerfileCommand(ExecCommand):
    class Entrypoint(Stanza):
        def __init__(self, command):
            self.command = command

        def __str__(self):
            return format_exec_command('ENTRYPOINT', self.command)

        def evaluate(self, context):
            # noinspection PyProtectedMember
            return context._replace(entrypoint=self.command)

    @classmethod
    def parse(cls, value):
        return cls.Entrypoint(cls.parse_maybe_json(value))


class OnbuildDockerfileCommand(DockerfileCommand):
    class OnBuild(Stanza):
        onbuild_allowed = False

        def __init__(self, tokens):
            self.command = tokens[0]
            if not self.command.onbuild_allowed:
                raise ValueError('Directive {0!s} not allowed in ONBUILD'.format(self.command))

        def __str__(self):
            return 'ONBUILD {0}'.format(str(self.command))

        def evaluate(self, context):
            onbuild = list(context.onbuild)
            onbuild.append(self.command)
            # noinspection PyProtectedMember
            return context._replace(onbuild=onbuild)

    @classmethod
    def parse(cls, value):
        parser = (DockerfileLine + p.Empty())
        return parser.setParseAction(cls.OnBuild).parseString(value, parseAll=True)


class FallbackDockerDirective(Stanza):
    def __init__(self, tokens):
        self.name = tokens['name']
        self.value = tokens['value']

    def evaluate(self, context):
        raise NotImplementedError('Unparsed directive {0!r}'.format(self))


DOCKER_COMMANDS = {
    'USER': UserDockerfileCommand,
    'ONBUILD': OnbuildDockerfileCommand,
    'WORKDIR': WorkdirDockerfileCommand,
    'ENV': EnvDockerfileCommand,
    'MAINTAINER': MaintainerDockerfileCommand,
    'FROM': FromDockerfileCommand,
    'ADD': AddDockerfileCommand,
    'COPY': CopyDockerfileCommand,
    'RUN': RunDockerfileCommand,
    'CMD': CmdDockerfileCommand,
    'ENTRYPOINT': EntrypointDockerfileCommand,
    'EXPOSE': ExposeDockerfileCommand,
    'VOLUME': VolumeDockerfileCommand,
    'INSERT': InsertDockerfileCommand,
}


def docker_directive(tokens):
    try:
        command = DOCKER_COMMANDS[tokens['name']]
        parse = command.parse
    except KeyError:
        return FallbackDockerDirective(tokens)
    try:
        return parse(tokens['value'])
    except p.ParseException as exc:
        print tokens['value']
        print exc
        return FallbackDockerDirective(tokens)


DockerfileLine = (p.LineStart() + p.Word(p.alphas)('name') + DirectiveValue('value')).setParseAction(docker_directive)

DockerfileParser = p.OneOrMore(
    CommentLine |
    EmptyLine |
    DockerfileLine
).setWhitespaceChars(' \t')


def strip_whitespace_after_continuations(s):
    # allowing "\ " as a continuation is a whole new level of stupid
    return re.sub(r'\\\s*$', r'\\', s, re.MULTILINE)


def empty_dockerfile(repo):
    context = RunContext(environ={
        'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
    }, user='root', workdir='/')
    base_dockerfile = Dockerfile(
        base_image=None,
        base_image_id=None,
        context=context,
        run_commands=[],
        expose=set(),
        entrypoint=None,
        volumes=set(),
        command=None,
        repo=repo,
        onbuild=[],
        hostname=None,
    )
    return base_dockerfile


def parse_dockerfile(dockerfile, base_dockerfile=None, repo=None):
    dockerfile = strip_whitespace_after_continuations(dockerfile)
    if base_dockerfile is None:
        parsed_dockerfile = empty_dockerfile(repo)
    else:
        parsed_dockerfile = base_dockerfile
    for directive in DockerfileParser.parseString(dockerfile, parseAll=True):
        parsed_dockerfile = directive.evaluate(parsed_dockerfile)
    return parsed_dockerfile


def from_docker_metadata(meta_json):
    config = meta_json['config']
    context = RunContext(
        environ=dict([kv.split('=', 1) for kv in config['Env']]),
        user=config['User'] or 'root',
        workdir=config['WorkingDir'] or '/')

    def split_port(port_str):
        port, proto = port_str.split('/', 1)
        port = int(port)
        return port, proto

    if config['ExposedPorts']:
        ports = set(split_port(port) for port in config['ExposedPorts'].keys())
    else:
        ports = set()

    if config['Volumes']:
        volumes = set(config['Volumes'].keys())
    else:
        volumes = set()

    onbuild = []
    if config['OnBuild']:
        for v in config['OnBuild']:
            onbuild.extend(OnbuildDockerfileCommand.parse(v))

    dockerfile = Dockerfile(
        base_image=None,
        base_image_id=config['Image'],
        context=context,
        run_commands=[],
        expose=ports,
        entrypoint=config['Entrypoint'],
        volumes=volumes,
        command=config['Cmd'],
        repo=None,
        onbuild=onbuild,
        hostname=config['Hostname'],
    )
    return dockerfile


def inherit_docker_metadata(metadata):
    context = from_docker_metadata(metadata)
    onbuild = context.onbuild or []
    # noinspection PyProtectedMember
    context = context._replace(base_image_id=metadata['id'], onbuild=[])
    for directive in onbuild:
        context = directive.command.evaluate(context)
    return context


def to_docker_metadata(container_id, dockerfile):
    """

    :type dockerfile: Dockerfile
    """
    if dockerfile.volumes:
        volumes = dict((v, {}) for v in sorted(dockerfile.volumes))
    else:
        volumes = None

    if dockerfile.expose:
        ports = dict(('{0}/{1}'.format(*port), {}) for port in sorted(dockerfile.expose))
    else:
        ports = None

    if dockerfile.onbuild:
        onbuild = [str(v) for v in dockerfile.onbuild]
    else:
        onbuild = []

    config = {
        'Env': ['='.join(kv) for kv in dockerfile.context.environ.items()],
        'Hostname': dockerfile.hostname,
        'Entrypoint': dockerfile.entrypoint,
        'PortSpecs': None,
        'Memory': 0,
        'OnBuild': onbuild,
        'OpenStdin': False,
        'User': dockerfile.context.user,
        'AttachStderr': False,
        'AttachStdout': False,
        'NetworkDisabled': False,
        'StdinOnce': False,
        'Cmd': dockerfile.command,  # container_config differs here
        'WorkingDir': dockerfile.context.workdir,
        'AttachStdin': False,
        'Volumes': volumes,
        'MemorySwap': 0,
        'Tty': False,
        'CpuShares': 0,
        'Domainname': '',
        'Image': container_id,  # not really,
        'SecurityOpt': None,
        'ExposedPorts': ports,
    }
    metadata = {
        'container': container_id,  # ???
        'parent': dockerfile.base_image_id,
        'created': datetime.datetime.now().isoformat(),
        'os': 'linux',
        'container_config': config,  # not really but we don't care
        'architecture': 'amd64',
        'docker_version': '1.3.0',
        'config': config,
        'id': container_id,
        'Size': 0
    }
    return metadata


if __name__ == '__main__':
    example = r'''# some comment
# another comment

# yet another

FROM foo/bar:latest
FROM foo/bar:x
# FROM foo/bar

# anything after FROM without tag explodes with "expected EOF"

ENV bzz q
ENV foo /bar
ENV bar $foo/bar
ENV baz ${foo}bar$foo
ENV baz \$foo baz \
bazar
ENV bax qw\$foo baz
ENV bax qw\\$foo baz

ENV ak=av
ENV ak=av bk=bv
ENV ck="ck ck=${foo}c\"v"

INSERT up-your-ass

EXPOSE 5432
EXPOSE 5431/tcp

ADD . foo /bar/$bzz

RUN apt-get update \
    && apt-get upgrade

RUN fpp \
   \
vf

RUN fpp \
   \
vf
'''
    import sys

    if len(sys.argv) > 1:
        example = open(sys.argv[1]).read().decode('utf-8')

    import pprint

    try:
        # noinspection PyProtectedMember
        pprint.pprint(dict(parse_dockerfile(example)._asdict()))
    except:
        for i, line in enumerate(example.splitlines()):
            print '{0:3}: {1}'.format(i, line)
        raise
