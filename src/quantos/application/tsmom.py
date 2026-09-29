"""Narrow TSMOM composition, retaining V1-T2/T3 Risk and Execution."""
from dataclasses import dataclass
from quantos.domain.alpha.context import AlphaDecisionContext
from quantos.domain.alpha.tsmom import TsmomAlpha
from quantos.domain.evaluation.contracts import AlphaEvaluation

@dataclass(frozen=True, slots=True)
class TsmomDecisionFunction:
    alpha: TsmomAlpha

    def __call__(self, feature, context: AlphaDecisionContext):
        decision = self.alpha.decide(feature, context)
        return AlphaEvaluation(decision, self.alpha.calibration.entry_edge)
