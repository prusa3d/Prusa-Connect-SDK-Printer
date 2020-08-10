# -*- coding: utf-8 -*-
import re
from pathlib import Path
from setuptools import setup, find_namespace_packages

REQUIRES = []
with open("requirements.txt", "r") as requires:
    for line in requires:
        REQUIRES.append(line.strip())

def find_version():
    version_file = Path("prusa/connect/printer/__init__.py").read_text()
    version_match = re.search(
        r"^__version__(\s)+=(\s)+\"(?P<version>[\w\.]+?)\"$", version_file, re.M
    )
    version = version_match.group("version")
    if not version:
        raise RuntimeError("Could not find version in prusa/connect/printer/__init__.py")
    return version

setup(
    name="prusa.connect.sdk.printer",
    version=find_version(),
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
