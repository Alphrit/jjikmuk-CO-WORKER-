"""The runtime install must work without the former constraints file."""

from pathlib import Path

import pytest
from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


AI_ROOT = Path(__file__).resolve().parents[1]


def _lines(filename):
    return [
        line.strip()
        for line in (AI_ROOT / filename).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _requirements(filename):
    return [Requirement(line) for line in _lines(filename) if not line.startswith("-")]


def test_runtime_requirements_are_self_contained_and_exactly_pinned():
    assert not any(line.startswith("-") for line in _lines("requirements.txt"))
    requirements = _requirements("requirements.txt")
    names = [canonicalize_name(req.name) for req in requirements]
    assert len(names) == len(set(names))
    for req in requirements:
        assert req.url is None
        specifiers = list(req.specifier)
        assert len(specifiers) == 1
        assert specifiers[0].operator == "=="
        assert "*" not in specifiers[0].version


def test_runtime_keeps_required_extras_and_excludes_test_only_packages():
    requirements = {
        canonicalize_name(req.name): req
        for req in _requirements("requirements.txt")
    }
    assert requirements["uvicorn"].extras == {"standard"}
    assert requirements["pymysql"].extras == {"rsa"}
    assert {"fastapi", "pydantic", "sqlalchemy", "chromadb", "openai"} <= requirements.keys()
    assert not {"pytest", "iniconfig", "pluggy", "langchain-community", "langgraph"} & requirements.keys()


def test_development_list_reuses_runtime_and_keeps_test_pins():
    includes = [line for line in _lines("requirements-dev.txt") if line.startswith("-")]
    assert includes == ["-r requirements.txt"]
    requirements = _requirements("requirements-dev.txt")
    assert {canonicalize_name(req.name) for req in requirements} == {"pytest", "iniconfig", "pluggy"}
    assert all(str(req.specifier).startswith("==") for req in requirements)


@pytest.mark.parametrize(("sys_platform", "implementation", "enabled"), [
    ("win32", "CPython", False),
    ("cygwin", "CPython", False),
    ("linux", "PyPy", False),
    ("darwin", "CPython", True),
    ("linux", "CPython", True),
])
def test_uvloop_retains_platform_conditions(sys_platform, implementation, enabled):
    uvloop = next(req for req in _requirements("requirements.txt") if req.name == "uvloop")
    assert uvloop.marker is not None
    environment = {
        **default_environment(),
        "sys_platform": sys_platform,
        "platform_python_implementation": implementation,
    }
    assert uvloop.marker.evaluate(environment) is enabled
