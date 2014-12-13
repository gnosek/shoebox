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
        shoebox-build=shoebox.build:build
        shoebox-run=shoebox.run:run

        shoebox-ancestry=shoebox.pull:ancestry
        shoebox-metadata=shoebox.pull:metadata
        shoebox-nsrun=shoebox.namespaces:cli
    ''',
)
