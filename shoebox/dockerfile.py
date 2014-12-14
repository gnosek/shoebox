from collections import namedtuple
import json
import datetime

import re
import pyparsing as p

from shoebox.tar import CopyFiles


RunContext = namedtuple('RunContext', 'environ user workdir')
ExecContext = namedtuple('ExecContext', 'namespace basedir')

class RunCommand(namedtuple('RunCommand', 'command context')):
    def execute(self, exec_context):
        raise NotImplementedError()

class CopyCommand(namedtuple('CopyCommand', 'src_paths dst_path')):
    def execute(self, exec_context):
        CopyFiles(exec_context.namespace, self.dst_path, exec_context.basedir, self.src_paths).run()

class AddCommand(namedtuple('AddCommand', 'src_paths dst_path')):
    def execute(self, exec_context):
        raise NotImplementedError()

Dockerfile = namedtuple('Dockerfile', 'base_image base_image_id context run_commands expose entrypoint volumes command repo')

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


class FROM_command(DockerfileCommand):
    class FromCommand(Stanza):
        def __init__(self, tokens):
            self.image_name = tokens['image_name']
            self.tag = tokens['tag'][0]

        def evaluate(self, context):
            """

            :type context: Dockerfile
            """
            assert context.base_image_id is None
            if context.repo is None:
                context = context._replace(base_image=(self.image_name, self.tag))
            else:
                metadata = context.repo.metadata(self.image_name, self.tag)
                context = from_docker_metadata(metadata)
                context = context._replace(base_image_id=metadata['id'])
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

        def expand(self, environ):
            return environ[self.reference]

    class EnvStaticString(Stanza):
        def __init__(self, tokens):
            self.string = tokens[0]

        # noinspection PyUnusedLocal
        def expand(self, environ):
            return self.string

    env_var = p.Word(p.alphas + '_', bodyChars=p.alphanums + '_')

    env_ref = p.Combine(ch('$') + env_var('ref')) | \
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

class ENV_command(EnvRefCommand):
    class EnvVariable(Stanza):
        def __init__(self, tokens):
            self.name = tokens['name']
            self.value = list(tokens['value'])

        def evaluate(self, context):
            environ = dict(context.context.environ)
            environ[self.name] = ''.join(v.expand(environ) for v in self.value)
            subcontext = context.context._replace(environ=environ)
            return context._replace(context=subcontext)

    env_var = EnvRefCommand.env_var
    env_value = EnvRefCommand.env_value
    env_word_value = EnvRefCommand.env_word_value

    env_spaced = (env_var('name') + sp + env_value('value')).setParseAction(EnvVariable)
    env_kw = (env_var('name') + ch('=').suppress() + env_word_value('value')).setParseAction(EnvVariable)

    parser = env_spaced | p.OneOrMore(env_kw)


class WORKDIR_command(EnvRefCommand):
    class WorkDir(Stanza):
        def __init__(self, tokens):
            self.workdir = tokens['workdir']

        def evaluate(self, context):
            environ = context.context.environ
            workdir = ''.join(v.expand(environ) for v in self.workdir)
            subcontext = context.context._replace(workdir=workdir)
            return context._replace(context=subcontext)

    parser = EnvRefCommand.env_word_value('workdir').setParseAction(WorkDir)


class EXPOSE_command(EnvRefCommand):
    class ExposePort(Stanza):
        def __init__(self, tokens):
            self.port = tokens['port']

        def evaluate(self, context):
            environ = context.context.environ
            port = int(''.join(v.expand(environ) for v in self.port))
            context = context._replace(expose=context.expose + [port])
            return context

    single_parser = EnvRefCommand.env_word_value('port').setParseAction(ExposePort)
    parser = single_parser + p.ZeroOrMore(sp + single_parser)


class ADD_command(EnvRefCommand):
    class Add(Stanza):
        def __init__(self, tokens):
            path_list = tokens['path_list'].asList()
            self.sources = path_list[:-1]
            self.destination = path_list[-1]

        def evaluate(self, context):
            environ = context.context.environ
            sources = [''.join(v.expand(environ) for v in src) for src in self.sources]
            destination = ''.join(v.expand(environ) for v in self.destination)
            commands = list(context.run_commands)
            commands.append(AddCommand(sources, destination))
            context = context._replace(run_commands=commands)
            return context

    single_parser = EnvRefCommand.env_word_value('path')
    parser = p.Group(single_parser + p.ZeroOrMore(sp + single_parser))('path_list').setParseAction(Add)


class COPY_command(EnvRefCommand):
    class Copy(Stanza):
        def __init__(self, tokens):
            path_list = tokens['path_list'].asList()
            self.sources = path_list[:-1]
            self.destination = path_list[-1]

        def evaluate(self, context):
            environ = context.context.environ
            sources = [''.join(v.expand(environ) for v in src) for src in self.sources]
            destination = ''.join(v.expand(environ) for v in self.destination)
            commands = list(context.run_commands)
            commands.append(CopyCommand(sources, destination))
            context = context._replace(run_commands=commands)
            return context

    single_parser = EnvRefCommand.env_word_value('path')
    parser = p.Group(single_parser + p.ZeroOrMore(sp + single_parser))('path_list').setParseAction(Copy)


class VOLUME_command(EnvRefCommand):
    class Volume(Stanza):
        def __init__(self, tokens):
            self.path = tokens['path']

        def evaluate(self, context):
            environ = context.context.environ
            path = ''.join(v.expand(environ) for v in self.path)
            volumes = set(context.volumes)
            volumes.add(path)
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


class USER_command(EnvRefCommand):
    class User(Stanza):
        def __init__(self, tokens):
            self.name = tokens['name']

        def evaluate(self, context):
            environ = context.context.environ
            username = ''.join(v.expand(environ) for v in self.name)
            subcontext = context.context._replace(user=username)
            return context._replace(context=subcontext)


    parser = EnvRefCommand.env_word_value('name').setParseAction(User)


class MAINTAINER_command(EnvRefCommand):
    class Maintainer(Stanza):
        def __init__(self, maintainer):
            self.maintainer = maintainer

    @classmethod
    def parse(cls, value):
        return cls.Maintainer(value)


class INSERT_command(EnvRefCommand):
    @classmethod
    def parse(cls, value):
        return p.ParseResults([])


class RUN_command(ExecCommand):
    class RunCommand(Stanza):
        def __init__(self, command):
            self.command = command

        def evaluate(self, context):
            commands = list(context.run_commands)
            commands.append(RunCommand(self.command, context.context))
            context = context._replace(run_commands=commands)
            return context

    @classmethod
    def parse(cls, value):
        return cls.RunCommand(cls.parse_maybe_json(value))


class CMD_command(ExecCommand):
    class Cmd(Stanza):
        def __init__(self, command):
            self.command = command

        def evaluate(self, context):
            return context._replace(command=self.command)

    @classmethod
    def parse(cls, value):
        return cls.Cmd(cls.parse_maybe_json(value))


class ENTRYPOINT_command(ExecCommand):
    class Entrypoint(Stanza):
        def __init__(self, command):
            self.command = command

        def evaluate(self, context):
            return context._replace(entrypoint=self.command)

    @classmethod
    def parse(cls, value):
        return cls.Entrypoint(cls.parse_maybe_json(value))


class ONBUILD_command(DockerfileCommand):
    class OnBuild(Stanza):
        def __init__(self, tokens):
            self.command = tokens

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
    'USER': USER_command,
    'ONBUILD': ONBUILD_command,
    'WORKDIR': WORKDIR_command,
    'ENV': ENV_command,
    'MAINTAINER': MAINTAINER_command,
    'FROM': FROM_command,
    'ADD': ADD_command,
    'COPY': COPY_command,
    'RUN': RUN_command,
    'CMD': CMD_command,
    'ENTRYPOINT': ENTRYPOINT_command,
    'EXPOSE': EXPOSE_command,
    'VOLUME': VOLUME_command,
    'INSERT': INSERT_command,
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
        expose=[],
        entrypoint=None,
        volumes=set(),
        command=None,
        repo=repo
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

    if config['ExposedPorts']:
        ports = [int(port.split('/')[0]) for port in config['ExposedPorts'].keys()]
    else:
        ports = []

    if config['Volumes']:
        volumes = set(config['Volumes'].keys())
    else:
        volumes = set()

    dockerfile = Dockerfile(
        base_image=None,
        base_image_id=config['Image'],
        context=context,
        run_commands=[],  # TODO: load from OnBuild
        expose=ports,
        entrypoint=config['Entrypoint'],
        volumes=volumes,
        command=None,
        repo=None,
    )
    return dockerfile

def to_docker_metadata(container_id, dockerfile):
    """

    :type dockerfile: Dockerfile
    """
    if dockerfile.volumes:
        volumes = dict((v, {}) for v in sorted(dockerfile.volumes))
    else:
        volumes = None

    if dockerfile.expose:
        ports = dict(('{0}/tcp'.format(p), {}) for p in sorted(dockerfile.expose))
    else:
        ports = None

    config = {
        'Env': ['='.join(kv) for kv in dockerfile.context.environ.items()],
        'Hostname': 'h' + container_id[:8],
        'Entrypoint': dockerfile.entrypoint,
        'PortSpecs': None,
        'Memory': 0,
        'OnBuild': [],  # TODO: unparse back to strings
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
        pprint.pprint(dict(parse_dockerfile(example)._asdict()))
    except:
        for i, line in enumerate(example.splitlines()):
            print '{0:3}: {1}'.format(i, line)
        raise