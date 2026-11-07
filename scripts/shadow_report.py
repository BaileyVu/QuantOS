"""Read shadow counterfactual evidence without writing to runtime state."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from quantos.paper.shadow import summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--state", type=Path, required=True)
    p.add_argument("--profile", default="pD")
    a = p.parse_args()
    if a.profile not in {"p1", "p5", "pA", "pR", "pL", "pL5", "pM10", "pM5", "pD"}:
        p.error("unknown profile")
    print(json.dumps(summary(a.state / f"shadow_{a.profile}.db", a.state / f"journal_{a.profile}.db"), indent=2))


if __name__ == "__main__": main()
