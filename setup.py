from distutils.core import setup

setup(
    name='shoebox',
    version='0.0.1',
    packages=['shoebox'],
    url='',
    license='MIT',
    author='Grzegorz Nosek',
    author_email='root@localdomain.pl',
    description='Tiny docker replacement',
    install_requires=['requests', 'click'],
    entry_points='''
        [console_scripts]
        shoebox-pull=shoebox.pull:pull
        shoebox-build=shoebox.build:cli
        shoebox-run=shoebox.run:run
        shoebox-rm=shoebox.rm:cli

        shoebox-ancestry=shoebox.pull:ancestry
        shoebox-metadata=shoebox.pull:get_metadata
    ''',
)
