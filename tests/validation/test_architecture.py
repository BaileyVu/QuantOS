"""Foundation checks for frozen V1 module boundaries."""

from __future__ import annotations

from pathlib import Path
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOMAIN_ROOT = PROJECT_ROOT / "src" / "quantos" / "domain"
APPLICATION_ROOT = PROJECT_ROOT / "src" / "quantos" / "application"
RISK_CONTRACT = DOMAIN_ROOT / "risk" / "contracts.py"
EXPECTED_MODULES = {"market_data", "features", "alpha", "risk", "execution", "evaluation"}


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

    def test_application_does_not_depend_on_infrastructure(self) -> None:
        source = "\n".join(path.read_text(encoding="utf-8") for path in APPLICATION_ROOT.rglob("*.py"))

        self.assertNotIn("quantos.infrastructure", source)

    def test_risk_contract_does_not_depend_on_execution_contract(self) -> None:
        source = RISK_CONTRACT.read_text(encoding="utf-8")

        self.assertNotIn("quantos.domain.execution", source)

