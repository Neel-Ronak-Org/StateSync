"""
tests/test_dependency_locks.py

WHY THIS EXISTS (2026-10-02 hygiene). Every image installed floating versions (">=" with no upper
bound, `python:3.12-slim`, `uv:latest`), so two builds of the same commit could differ, and the
uv.lock files were never read by any build. Each image now installs its requirements.lock, frozen
from the running container. Pinned here so the pieces cannot drift apart:
- every direct dependency in a pyproject.toml is in that service's lock and inside its range;
- every lock line is an exact pin;
- no Dockerfile uses a floating base or uv image.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

packaging = pytest.importorskip("packaging")
from packaging.requirements import Requirement  # noqa: E402
from packaging.utils import canonicalize_name  # noqa: E402
from packaging.version import Version  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
LOCKED_SERVICES = ("mcp", "orchestrator", "simulator", "parser", "reranker")


def _lock(service: str) -> dict[str, str]:
    pins = {}
    for line in (REPO / service / "requirements.lock").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("--"):
            continue
        name, sep, version = line.partition("==")
        assert sep and version and not re.search(r"[<>~!*]", version), f"{service}: not an exact pin: {line}"
        pins[canonicalize_name(name)] = version
    return pins


@pytest.mark.parametrize("service", ["mcp", "orchestrator"])
def test_every_declared_dependency_is_locked_inside_its_range(service):
    pins = _lock(service)
    deps = tomllib.loads((REPO / service / "pyproject.toml").read_text())["project"]["dependencies"]
    for dep in deps:
        req = Requirement(dep)
        if req.marker is not None and not req.marker.evaluate({"python_version": "3.12"}):
            continue
        name = canonicalize_name(req.name)
        assert name in pins, f"{service}: {req.name} is declared but not in requirements.lock"
        assert req.specifier.contains(Version(pins[name]), prereleases=True), \
            f"{service}: locked {req.name}=={pins[name]} is outside {req.specifier}"


@pytest.mark.parametrize("service", LOCKED_SERVICES)
def test_locks_are_exact_and_dockerfiles_install_them(service):
    assert _lock(service)
    dockerfile = (REPO / service / "Dockerfile").read_text()
    assert "requirements.lock" in dockerfile


def test_no_dockerfile_uses_a_floating_base_or_uv_image():
    for dockerfile in REPO.glob("*/Dockerfile"):
        text = dockerfile.read_text()
        stages = set(re.findall(r"^FROM\s+\S+\s+AS\s+(\S+)", text, re.M | re.I))
        images = re.findall(r"^FROM\s+(\S+)", text, re.M) + re.findall(r"COPY --from=(\S+)", text)
        for image in (i for i in images if i not in stages):
            assert not image.endswith(":latest") and ":" in image, f"{dockerfile}: floating image {image}"
            if re.match(r"(python|node|nginx):", image):
                assert re.match(r"\w+:\d+\.\d+\.\d+-", image), f"{dockerfile}: unpinned patch version {image}"
