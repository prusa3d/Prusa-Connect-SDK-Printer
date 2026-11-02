Contributing to PrusaConnect SDK for Printer
============================================

Thank you for your interest in contributing. This document describes the
rules every change must follow. They mirror what the CI checks, so a change
that follows them will pass the pipeline.

Development setup
-----------------

The library supports **Python 3.9 and newer**. CI runs on 3.9, 3.11, 3.12
and 3.13, so do not use syntax or standard library features newer than 3.9
(for example ``match``, ``X | Y`` in runtime type expressions or
``datetime.UTC``).

.. code:: shell

    python -m venv .venv
    . .venv/bin/activate
    pip install -r requirements.txt
    pip install types-requests ruff pytest pytest-doctestplus pylint \
        requests_mock pytest-mypy func-timeout responses \
        pre-commit yapf

    pre-commit install

Runtime dependencies live in ``requirements.txt`` (``setup.py`` reads them
from there). Do not add a new dependency without a good reason and without
discussing it first; the SDK runs on small embedded printers.

Code style
----------

- Follow PEP 8. The line length limit is **79 characters**.
- Code is formatted with **yapf** (configuration in ``.style.yapf``).
  Do not reformat code you are not otherwise changing.
- Use UTF-8, LF line endings, 4 spaces for indentation (2 spaces in YAML,
  JSON, TOML, HTML, XML, JS and CSS), and end every file with a newline
  (see ``.editorconfig``). No trailing whitespace.
- Imports are sorted by ``ruff`` (isort rules).
- The package is typed (``py.typed``). Add type hints to all new public
  functions and methods and keep ``mypy`` clean.
- Write docstrings for public modules, classes and functions.
- Use the ``logging`` module (the ``log`` object of the module), never
  ``print``.
- Linters are configured in ``pyproject.toml`` (``[tool.ruff]``) and
  ``.pylintrc``. Prefer fixing the code over silencing a check. If you must
  add ``# noqa`` or ``# pylint: disable``, do it on the narrowest scope and
  name the specific rule.

Checks
------

All of the following must pass before a change is merged. They are exactly
what the CI workflow (``.github/workflows/python-tests.yml``) runs on every
push to ``master`` and on every pull request.

.. code:: shell

    # Linting
    ruff check .
    pylint prusa/connect/printer

    # Type check and doctests (including doctests in .rst files)
    pytest -v --mypy --doctest-plus --doctest-rst prusa/connect/printer

    # Tests (the timezone matters for time related tests)
    TZ=Europe/Prague pytest -v --mypy tests --log-cli-level=10

Pre-commit hooks (``.pre-commit-config.yaml``) run ``yapf``, ``ruff``,
``flake8`` and basic checks (valid YAML, end-of-file newline, no trailing
whitespace). Run them on all files with:

.. code:: shell

    pre-commit run --all-files

Tests
-----

- Tests live in ``tests/`` and use ``pytest``. Test files are named
  ``test_*.py``; shared fixtures are in ``tests/conftest.py`` and helpers in
  ``tests/util.py``.
- Every new feature and every bug fix must come with a test. A bug fix
  should include a test that fails without the fix.
- HTTP communication is mocked (``requests_mock``, ``responses``). Tests
  must not access the network or real printer hardware.
- Test G-code files belong in ``tests/gcodes/``.
- Tests must pass on all supported Python versions and must not depend on
  the local timezone beyond ``TZ=Europe/Prague`` used by CI.
- Examples in docstrings and in ``README.rst`` are executed as doctests;
  keep them correct.

Commits
-------

- Keep commits small and focused; prefer several small commits over one big
  one. Do not mix formatting changes with functional changes.
- Write commit messages in English. The first line is a short summary
  (ideally up to 72 characters) in the imperative mood. The history uses
  `Conventional Commits <https://www.conventionalcommits.org/>`_ prefixes
  such as ``fix:``, ``feat:``, ``docs:``, ``chore:`` and ``ci:`` with an
  optional scope, for example ``fix(ci): allow more positional arguments``.
- Describe what changes in behaviour (new, fixed, changed), not how it is
  implemented. Add a body only when the reason is not obvious.

Pull requests
-------------

- Branch from ``master`` and open the pull request against ``master``.
- The CI workflow must be green on all Python versions.
- Describe what the change does and why. Link related issues.
- Keep the pull request focused on one topic.
- Update ``README.rst`` when you change public behaviour or the API.

ChangeLog and versioning
------------------------

- User visible changes (features, fixes, behaviour changes, dependency and
  supported Python version changes) are listed in ``ChangeLog``. Add an
  entry under the current development version (at the top of the file) in
  the same pull request as the change.
- The version is defined by ``__version__`` and ``__date__`` in
  ``prusa/connect/printer/__init__.py``. The project uses semantic-like
  versioning; a development version has the ``devN`` suffix.
- A release is prepared in a dedicated commit
  (``chore(release): bump version to X.Y.Z``) that sets the version and date
  in ``__init__.py`` and the date in ``ChangeLog``.

Repository hygiene
------------------

- Do not commit editor backups (``*~``), virtual environments, caches
  (``.mypy_cache``, ``.pytest_cache``, ``.ruff_cache``), coverage data or
  other generated files.
- Never commit secrets such as tokens, fingerprints or real
  ``prusa_printer_settings.ini`` files.

License
-------

By contributing you agree that your contribution is provided under the same
terms as the project (see ``pyproject.toml``).
