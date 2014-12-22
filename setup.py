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
    requires=['requests', 'click', 'pyroute2', 'pyparsing'],
    install_requires=['requests', 'click', 'pyroute2', 'pyparsing'],
    entry_points='''
        [console_scripts]
        shoebox=shoebox.cli:cli
        shoebox-run=shoebox.run:run
        shoebox-rm=shoebox.rm:cli
    ''',
)
