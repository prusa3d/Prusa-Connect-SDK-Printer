# -*- coding: utf-8 -*-
from setuptools import setup, find_namespace_packages

from prusa.connect.printer import pkg_info

REQUIRES = []
with open("requirements.txt", "r") as requires:
    for line in requires:
        REQUIRES.append(line.strip())

setup(
    name="prusa.connect.sdk.printer",
    version=pkg_info.version,
    description=pkg_info.description,
    author=pkg_info.author,
    author_email=pkg_info.author_email,
    maintainer=pkg_info.maintainer,
    maintainer_email=pkg_info.maintainer_email,
    url=pkg_info.url,
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
