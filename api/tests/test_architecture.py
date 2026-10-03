"""계층 규칙 검사 (ADR-035) — 의존은 안쪽(업무 규칙)으로만.

    router(HTTP) ─▶ service(업무 규칙, 순수) ◀─ repository(데이터 접근)

- service 는 웹 프레임워크·DB 드라이버·외부 HTTP 를 직접이든 간접이든 쓰지 않는다 (가짜 저장소로 단위 시험 가능)
- service 는 같은 기능의 router·repository 를 모른다 (저장소는 Protocol 로 주입)
- repository 는 HTTP 를 모른다
- core 는 features 를 모른다
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PKG = "aptlake_api"
ROOT = Path(__file__).resolve().parents[1] / "src" / PKG
FRAMEWORK = {"fastapi", "starlette", "pydantic"}
DRIVERS = {"psycopg", "psycopg_pool", "clickhouse_connect", "redis", "httpx", "boto3", "botocore", "pyarrow"}


def _module_name(path: Path) -> str:
    rel = path.relative_to(ROOT.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


MODULES = {_module_name(p): p for p in ROOT.rglob("*.py")}


def _imports(mod: str) -> set[str]:
    """모듈이 직접 가져오는 모듈 이름 (패키지 안은 절대 이름으로 풀고, 바깥은 최상위 이름만)."""
    path = MODULES[mod]
    pkg = mod if path.name == "__init__.py" else mod.rsplit(".", 1)[0]
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg.rsplit(".", node.level - 1)[0] if node.level > 1 else pkg
                base = f"{base}.{node.module}" if node.module else base
            else:
                base = node.module or ""
            out.add(base)
            out |= {f"{base}.{a.name}" for a in node.names if f"{base}.{a.name}" in MODULES}
    return {m if m.startswith(PKG) else m.split(".")[0] for m in out if m != "__future__"}


def _closure(mod: str) -> set[str]:
    """패키지 안에서 간접으로 닿는 모듈까지."""
    seen, todo = set(), [mod]
    while todo:
        m = todo.pop()
        if m in seen or m not in MODULES:
            continue
        seen.add(m)
        todo += [i for i in _imports(m) if i.startswith(PKG)]
    return seen


FEATURES = sorted({m.split(".")[2] for m in MODULES if m.startswith(f"{PKG}.features.") and m.count(".") >= 3})
SERVICES = [f"{PKG}.features.{f}.service" for f in FEATURES]


def test_every_feature_has_router_and_service():
    for f in FEATURES:
        assert f"{PKG}.features.{f}.router" in MODULES, f
        assert f"{PKG}.features.{f}.service" in MODULES, f


@pytest.mark.parametrize("service", SERVICES)
def test_service_is_pure(service):
    for m in _closure(service):
        bad = (_imports(m) & (FRAMEWORK | DRIVERS)) - {""}
        assert not bad, f"{service} → {m} 가 {sorted(bad)} 를 가져온다"


@pytest.mark.parametrize("service", SERVICES)
def test_service_does_not_know_adapters(service):
    feature = service.rsplit(".", 1)[0]
    own = {m for m in MODULES if m.startswith(feature + ".") and m != service}
    assert not (_imports(service) & own), "service 는 같은 기능의 router·repository·어댑터를 가져오지 않는다"
    other = {i for i in _imports(service) if i.startswith(f"{PKG}.features.") and not i.endswith(".service")}
    assert not other, f"다른 기능은 service 로만: {sorted(other)}"


def test_repositories_do_not_know_http():
    for m in MODULES:
        if m.startswith(f"{PKG}.features.") and m.rsplit(".", 1)[-1] in ("repository", "storage", "dagster", "worker"):
            assert not (_imports(m) & FRAMEWORK), m


def test_core_does_not_depend_on_features():
    for m in MODULES:
        if m.startswith(f"{PKG}.core."):
            assert not any(i.startswith(f"{PKG}.features") for i in _imports(m)), m


def test_import_parser_sees_relative_imports():
    """검사기 자체 확인: 상대 import 를 절대 이름으로 푼다."""
    imps = _imports(f"{PKG}.features.trades.repository")
    assert f"{PKG}.features.trades.service" in imps and "clickhouse_connect" in imps
