# -*- coding: utf-8 -*-
from setuptools import setup, find_namespace_packages

from prusa.connect.printer import __version__

REQUIRES = []
with open("requirements.txt", "r") as requires:
    for line in requires:
        REQUIRES.append(line.strip())


setup(
    name="prusa.connect.sdk.printer",
    version=__version__,
    description="Python printer library for Prusa Connect",
    author="Ondřej Tůma",
    author_email="ondrej.tuma@prusa3d.cz",
    maintainer="Ondrej Tuma",
    maintainer_email="ondre.tuma@prusa3d.cz",
    url="https://github.com/prusa3d/Prusa-Connect-SDK-Printer",
    packages=find_namespace_packages(include=["prusa.connect.*"]),
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: Developers",
        "Natural Language :: English",
        "Operating System :: POSIX",
        "Programming Language :: Python :: 3 :: Only",
        "Topic :: Software Development :: Libraries :: Python Modules"
    ],
    install_requires=REQUIRES,
    tests_require=['pytest', 'requests_mock', 'pytest-mypy-plugins']
)
