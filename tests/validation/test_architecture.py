"""Foundation checks for frozen V1 module boundaries."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_ROOT = PROJECT_ROOT / "src" / "quantos"
DOMAIN_ROOT = PROJECT_ROOT / "src" / "quantos" / "domain"
APPLICATION_ROOT = PROJECT_ROOT / "src" / "quantos" / "application"
STORAGE_ROOT = PROJECT_ROOT / "src" / "quantos" / "infrastructure" / "storage"
RISK_CONTRACT = DOMAIN_ROOT / "risk" / "contracts.py"
EXPECTED_MODULES = {"market_data", "features", "alpha", "risk", "execution", "evaluation"}
NETWORK_MODULES = {"aiohttp", "http", "httpx", "requests", "socket", "urllib"}


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
                    any(module.split(".")[0] in {"duckdb", "pyarrow"} for module in modules)
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

