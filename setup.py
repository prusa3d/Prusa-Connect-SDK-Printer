"""Setup of PrusaConnect SDK for Printer."""
import re

from setuptools import find_namespace_packages, setup  # type: ignore

METADATA = {}
with open("prusa/connect/printer/__init__.py", "r", encoding="utf-8") as info:
    METADATA = dict(re.findall(r'__([a-z_]+)__ = "([^"]+)"', info.read()))

REQUIRES = []
with open("requirements.txt", "r", encoding="utf-8") as requires:
    for line in requires:
        REQUIRES.append(line.strip())


def doc():
    """Return README.rst content."""
    with open('README.rst', 'r', encoding="utf-8") as readme:
        return readme.read().strip()


setup(name="prusa.connect.sdk.printer",
      version=METADATA["version"],
      description=METADATA["description"],
      author=METADATA["author_name"],
      author_email=METADATA["author_email"],
      maintainer=METADATA["author_name"],
      maintainer_email=METADATA["author_email"],
      license="Freeware",
      url=METADATA["url"],
      project_urls={
          "Bug Tracker":
          "https://github.com/prusa3d/Prusa-Connect-SDK-Printer/issues",
          "Source Code":
          "https://github.com/prusa3d/Prusa-Connect-SDK-Printer",
      },
      packages=find_namespace_packages(include=["prusa.connect.printer"]),
      package_data={'': ['py.typed']},
      data_files=[("share/doc/prusa-connect-printer",
                   ["README.rst", "ChangeLog"])],
      long_description=doc(),
      long_description_content_type="text/x-rst",
      classifiers=[
          "Development Status :: 5 - Production/Stable",
          "Intended Audience :: Developers",
          "Natural Language :: English",
          "License :: Freeware",
          "Operating System :: POSIX",
          "Programming Language :: Python :: 3 :: Only",
          "Topic :: Software Development :: Libraries :: Python Modules",
      ],
      python_requires=">=3.9",
      install_requires=REQUIRES,
      tests_require=[
          'pytest',
          'requests_mock',
          'pytest-mypy-plugins',
          'func-timeout',
      ])
