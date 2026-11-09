"""Static smoke checks for the local QuantOS dashboard shell."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


HTML_PATH = Path(__file__).resolve().parents[2] / "src/quantos/paper/webdash.html"
EXPECTED_NAV = ("Overview", "Strategies", "Trades", "Sentiment", "Logs")
EXPECTED_ROUTES = tuple(f"#/{label.lower()}" for label in EXPECTED_NAV)


class WebdashUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = HTML_PATH.read_text(encoding="utf-8")
        cls.nav = re.search(
            r"\b(?:const|let)\s+NAV\s*=\s*\[(.*?)\]\s*;",
            cls.html,
            re.DOTALL,
        )

    def test_sidebar_is_the_only_primary_navigation(self) -> None:
        self.assertRegex(self.html, r'<nav\b[^>]*\bid=["\']tabs["\']')
        self.assertNotRegex(self.html, r'class=["\'][^"\']*\btab\b')
        self.assertIsNotNone(self.nav, "the sidebar must be generated from one NAV array")
        assert self.nav is not None
        nav_source = self.nav.group(1)
        for label, route in zip(EXPECTED_NAV, EXPECTED_ROUTES, strict=True):
            with self.subTest(label=label):
                self.assertEqual(len(re.findall(rf"\blabel\s*:\s*['\"]{label}['\"]", nav_source)), 1)
                self.assertEqual(len(re.findall(rf"\broute\s*:\s*['\"]{re.escape(route)}['\"]", nav_source)), 1)
        self.assertRegex(self.html, r"NAV\.map\(")
        self.assertIn("nav-item", self.html)

    def test_routes_and_view_roots_exist(self) -> None:
        for route in EXPECTED_ROUTES:
            with self.subTest(route=route):
                self.assertIn(route, self.html)
        for view_id in ("Overview", "Strategies", "Trades", "Sentiment", "Logs"):
            with self.subTest(view=view_id):
                self.assertRegex(
                    self.html,
                    rf"data-tab=[\"']{view_id}[\"']",
                )

    def test_key_live_panels_remain_addressable(self) -> None:
        for element_id in (
            "stopAll", "eqPanel", "acct", "ovStrats", "alerts", "stStrats",
            "ml1hPanel", "funnel", "positions", "orders", "fills",
            "senti", "health", "log", "newsPanel",
        ):
            with self.subTest(element_id=element_id):
                self.assertRegex(self.html, rf"\bid=[\"']{element_id}[\"']")

    def test_uses_local_assets_and_a_single_aura_token_block(self) -> None:
        head = self.html.split("</head>", 1)[0]
        for match in re.finditer(r"<(?:script|link)\b[^>]*>", head, re.IGNORECASE):
            tag = match.group(0)
            self.assertNotRegex(tag, r"https?://")
            self.assertNotRegex(tag, r"fonts\.googleapis|fonts\.gstatic")

        token_blocks = re.findall(r":root\s*\{[^}]*--az-[^}]*\}", self.html, re.DOTALL)
        self.assertEqual(len(token_blocks), 1, "Aura design tokens must live in one :root block")
        for token in (
            "--az-base", "--az-elevated", "--az-sidebar", "--az-panel-solid",
            "--az-panel-glass", "--az-border-subtle", "--az-border-active",
            "--az-focus", "--az-text-primary", "--az-text-secondary",
            "--az-text-muted", "--az-positive", "--az-negative", "--az-warning",
            "--az-info", "--az-chart-base",
        ):
            with self.subTest(token=token):
                self.assertIn(token, token_blocks[0])


if __name__ == "__main__":
    unittest.main()
