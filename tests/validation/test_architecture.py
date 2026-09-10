"""Foundation checks for frozen V1 module boundaries."""

from __future__ import annotations

import ast
from pathlib import Path
import tomllib
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_ROOT = PROJECT_ROOT / "src" / "quantos"
DOMAIN_ROOT = PROJECT_ROOT / "src" / "quantos" / "domain"
APPLICATION_ROOT = PROJECT_ROOT / "src" / "quantos" / "application"
STORAGE_ROOT = PROJECT_ROOT / "src" / "quantos" / "infrastructure" / "storage"
BINANCE_ROOT = PRODUCTION_ROOT / "infrastructure" / "binance"
RISK_CONTRACT = DOMAIN_ROOT / "risk" / "contracts.py"
EXPECTED_MODULES = {"market_data", "features", "alpha", "risk", "execution", "evaluation"}
NETWORK_MODULES = {"aiohttp", "http", "httpx", "requests", "socket", "urllib", "websockets"}


def _imported_modules(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
            modules.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return tuple(modules)


class ArchitectureTests(unittest.TestCase):
    def test_feature_engine_has_only_domain_and_pure_standard_library_dependencies(self) -> None:
        allowed_standard = {"__future__", "datetime", "decimal", "dataclasses", "types", "typing"}
        allowed_domain = (
            "quantos.domain.common", "quantos.domain.features", "quantos.domain.market_data",
        )
        for path in (DOMAIN_ROOT / "features").rglob("*.py"):
            with self.subTest(path=path.name):
                for module in _imported_modules(path):
                    self.assertTrue(
                        module.split(".")[0] in allowed_standard
                        or any(module == prefix or module.startswith(prefix + ".")
                               for prefix in allowed_domain),
                        module,
                    )

    def test_features_have_no_float_clock_or_io_calls(self) -> None:
        forbidden_calls = {"float", "open", "__import__", "eval", "exec", "getattr"}
        forbidden_attributes = {"now", "utcnow", "today", "time", "monotonic", "setcontext"}
        for path in (DOMAIN_ROOT / "features").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                with self.subTest(path=path.name, line=getattr(node, "lineno", None)):
                    if isinstance(node, ast.Constant):
                        self.assertNotIsInstance(node.value, float)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                        self.assertNotIn(node.func.id, forbidden_calls)
                    if isinstance(node, ast.Attribute):
                        self.assertNotIn(node.attr, forbidden_attributes)

    def test_project_has_no_third_party_technical_analysis_dependency(self) -> None:
        project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        for requirement in project["project"]["dependencies"]:
            name = requirement.split("==")[0].lower().replace("_", "-")
            self.assertNotIn(name, {"ta", "ta-lib", "pandas", "numpy", "pandas-ta", "finta"})

    def test_exactly_six_production_domain_areas_exist(self) -> None:
        actual_modules = {
            path.name for path in DOMAIN_ROOT.iterdir() if path.is_dir() and not path.name.startswith("__")
        }

        self.assertEqual(actual_modules, EXPECTED_MODULES)

    def test_domain_does_not_depend_on_infrastructure(self) -> None:
        source = "\n".join(path.read_text(encoding="utf-8") for path in DOMAIN_ROOT.rglob("*.py"))

        self.assertNotIn("quantos.infrastructure", source)
        self.assertNotIn("binance", source.lower())
        self.assertNotIn("duckdb", source.lower())
        self.assertNotIn("parquet", source.lower())
        self.assertNotIn("pyarrow", source.lower())
        self.assertNotIn("websockets", source.lower())

    def test_application_does_not_depend_on_infrastructure(self) -> None:
        for path in APPLICATION_ROOT.rglob("*.py"):
            with self.subTest(path=path.relative_to(PROJECT_ROOT)):
                modules = _imported_modules(path)
                self.assertFalse(
                    any(
                        module == "quantos.infrastructure"
                        or module.startswith("quantos.infrastructure.")
                        for module in modules
                    )
                )
                self.assertFalse(
                    any(module.split(".")[0] in {"duckdb", "pyarrow", "websockets"} for module in modules)
                )

    def test_duckdb_imports_are_confined_to_infrastructure_storage(self) -> None:
        importers = tuple(
            path
            for path in PRODUCTION_ROOT.rglob("*.py")
            if any(module.split(".")[0] == "duckdb" for module in _imported_modules(path))
        )

        self.assertTrue(importers)
        for path in importers:
            with self.subTest(path=path.relative_to(PROJECT_ROOT)):
                self.assertTrue(path.is_relative_to(STORAGE_ROOT))

    def test_phase_2c2_application_and_storage_have_no_network_dependency(self) -> None:
        for root in (APPLICATION_ROOT, STORAGE_ROOT):
            for path in root.rglob("*.py"):
                with self.subTest(path=path.relative_to(PROJECT_ROOT)):
                    modules = _imported_modules(path)
                    self.assertFalse(
                        any(module.split(".")[0] in NETWORK_MODULES for module in modules)
                    )
                    self.assertFalse(
                        any(
                            module == "quantos.infrastructure.binance"
                            or module.startswith("quantos.infrastructure.binance.")
                            for module in modules
                        )
                    )

    def test_production_code_does_not_hardcode_canonical_data_root(self) -> None:
        source = "\n".join(
            path.read_text(encoding="utf-8") for path in PRODUCTION_ROOT.rglob("*.py")
        )
        normalized_source = source.lower().replace("\\\\", "\\").replace("/", "\\")

        self.assertNotIn(r"g:\quantos-data", normalized_source)

    def test_risk_contract_does_not_depend_on_execution_contract(self) -> None:
        source = RISK_CONTRACT.read_text(encoding="utf-8")

        self.assertNotIn("quantos.domain.execution", source)

    def test_websockets_imports_are_confined_to_binance_infrastructure(self) -> None:
        importers = tuple(
            path for path in PRODUCTION_ROOT.rglob("*.py")
            if any(module.split(".")[0] == "websockets" for module in _imported_modules(path))
        )
        self.assertTrue(importers)
        for path in importers:
            with self.subTest(path=path.relative_to(PROJECT_ROOT)):
                self.assertTrue(path.is_relative_to(BINANCE_ROOT))

    def test_live_adapter_has_no_persistence_or_trading_dependencies(self) -> None:
        for name in ("live_klines.py", "live_stream.py"):
            path = BINANCE_ROOT / name
            with self.subTest(path=name):
                for module in _imported_modules(path):
                    if module.startswith("quantos."):
                        self.assertTrue(module.startswith((
                            "quantos.domain.common", "quantos.domain.market_data",
                            "quantos.infrastructure.binance.live_klines",
                        )), module)
                    self.assertNotIn(module.split(".")[0], {"duckdb", "pyarrow", "pathlib", "os"})
                source = path.read_text(encoding="utf-8")
                for forbidden in ("api_key", "api_secret", "listenKey", "/api/v3/order", "fapi.binance"):
                    self.assertNotIn(forbidden, source)

