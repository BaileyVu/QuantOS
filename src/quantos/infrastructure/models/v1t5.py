"""Preregistered V1-T5 features and fold-local supervised model adapters.

Inputs are complete contiguous bars: UTC open seconds, O,H,L,C,V,quote V,trades.
Labels are future outcomes, never decision inputs. Caller supplies disjoint,
chronological train/validation masks; label end indices enforce boundary purging.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

SEED = 1729
FD_GRID = (0.25, 0.5, 0.75, 1.0)
A_CONFIGS = (
    {"horizon": 4, "barrier": 1.0, "bottleneck": 8, "noise": 0.0},
    {"horizon": 8, "barrier": 2.0, "bottleneck": 16, "noise": 0.05},
)
B_CONFIGS = ({"max_depth": 2, "n_estimators": 100},
             {"max_depth": 3, "n_estimators": 200})


def fractional_difference(values: np.ndarray, d: float, width: int = 64) -> np.ndarray:
    """Fixed-width causal fractional difference, including current completed bar."""
    weights = np.ones(width)
    for k in range(1, width):
        weights[k] = -weights[k - 1] * (d - k + 1) / k
    out = np.full(len(values), np.nan)
    if len(values) >= width:
        out[width - 1:] = sliding_window_view(values, width) @ weights[::-1]
    return out


def _rolling(values: np.ndarray, width: int, op: str) -> np.ndarray:
    out = np.full(len(values), np.nan)
    if len(values) >= width:
        out[width - 1:] = getattr(np, op)(sliding_window_view(values, width), axis=1)
    return out


def validate_bars(bars: np.ndarray) -> None:
    if bars.ndim != 2 or bars.shape[1] != 8 or len(bars) < 2:
        raise ValueError("Expected at least two complete bars with eight columns")
    delta = np.diff(bars[:, 0])
    if not np.isfinite(bars).all() or np.any(delta != delta[0]) or delta[0] <= 0:
        raise ValueError("Bars must be finite, chronological and contiguous")
    if (np.any(bars[:, 1:5] <= 0) or np.any(bars[:, 5:] < 0)
            or np.any(bars[:, 2] < np.maximum(bars[:, 1], bars[:, 4]))
            or np.any(bars[:, 3] > np.minimum(bars[:, 1], bars[:, 4]))):
        raise ValueError("Invalid OHLCV")


def feature_matrix(bars: np.ndarray) -> tuple[np.ndarray, tuple[str, ...]]:
    validate_bars(bars)
    o, hi, lo, c, v, q, trades = bars[:, 1:].T
    lc, lv = np.log(c), np.log1p(v)
    cols: list[np.ndarray] = []
    names: list[str] = []
    def add(name: str, value: np.ndarray) -> None:
        names.append(name)
        cols.append(value)
    def change(x: np.ndarray, h: int) -> np.ndarray:
        out = np.full(len(x), np.nan)
        out[h:] = x[h:] - x[:-h]
        return out
    ret = change(lc, 1)
    for h in (1, 2, 4, 8, 16, 48):
        add(f"log_return_{h}", change(lc, h))
    for h in (4, 16, 48):
        add(f"volatility_{h}", _rolling(ret, h, "std"))
    for name, x in (("body", c-o), ("range", hi-lo),
                    ("upper_wick", hi-np.maximum(o, c)),
                    ("lower_wick", np.minimum(o, c)-lo)):
        add(name, x / c)
    for h in (1, 4, 16, 48):
        add(f"log_volume_change_{h}", change(lv, h))
    for h in (4, 16, 48):
        add(f"volume_z_{h}", (lv-_rolling(lv, h, "mean")) /
            np.maximum(_rolling(lv, h, "std"), 1e-12))
        add(f"relative_volume_{h}", v / np.maximum(_rolling(v, h, "mean"), 1e-12)-1)
    for h in (4, 16, 48):
        low, high = _rolling(lo, h, "min"), _rolling(hi, h, "max")
        add(f"location_{h}", (c-low) / np.maximum(high-low, 1e-12))
        add(f"trend_{h}", lc-_rolling(lc, h, "mean"))
    add("log_trades", np.log1p(trades))
    add("vwap_deviation", np.divide(q, v, out=c.copy(), where=v > 0) / c-1)
    for source, values in (("close", lc), ("volume", lv)):
        for d in FD_GRID:
            add(f"fd_{source}_{d}", fractional_difference(values, d))
    return np.column_stack(cols), tuple(names)


@dataclass
class FoldTransform:
    indices: np.ndarray
    mean: np.ndarray
    scale: np.ndarray
    names: tuple[str, ...]
    fractional_d: dict[str, float]

    @classmethod
    def fit(cls, x: np.ndarray, names: tuple[str, ...]) -> FoldTransform:
        if len(x) < 10 or not np.isfinite(x).all():
            raise ValueError("Transform needs finite training rows")
        selected = [i for i, n in enumerate(names) if not n.startswith("fd_")]
        fractional_d = {}
        for source in ("close", "volume"):
            chosen = 1.0
            for d in FD_GRID:
                a = x[:, names.index(f"fd_{source}_{d}")]
                if np.std(a) > 1e-12 and abs(np.corrcoef(a[:-1], a[1:])[0, 1]) <= .8:
                    chosen = d
                    break
            fractional_d[source] = chosen
            selected.append(names.index(f"fd_{source}_{chosen}"))
        selected = [i for i in selected if np.std(x[:, i]) > 1e-12]
        corr = np.corrcoef(x[:, selected], rowvar=False)
        keep: list[int] = []
        for j in range(len(selected)):
            if all(abs(corr[j, k]) <= .98 for k in keep):
                keep.append(j)
        ix = np.asarray([selected[j] for j in keep], dtype=int)
        if not len(ix):
            raise ValueError("No nonconstant features")
        return cls(ix, x[:, ix].mean(0), x[:, ix].std(0),
                   tuple(names[i] for i in ix), fractional_d)

    def apply(self, x: np.ndarray) -> np.ndarray:
        values = x[:, self.indices]
        if not np.isfinite(values).all():
            raise ValueError("Missing required feature; no inference permitted")
        return np.clip((values-self.mean)/self.scale, -10, 10).astype(np.float32)


def triple_barrier_labels(bars: np.ndarray, horizon: int, barrier: float
                          ) -> tuple[np.ndarray, np.ndarray]:
    """Classes 0=negative,1=neutral,2=positive; both barriers => negative.

    Threshold uses only trailing 16-bar log-return standard deviation at t.
    Label expiry is conservatively t+horizon even if barrier touched earlier.
    Barriers define labels only; they never presume executable barrier fills.
    """
    validate_bars(bars)
    n = len(bars)
    y, end = np.full(n, np.nan), np.arange(n)+horizon
    r = np.r_[np.nan, np.diff(np.log(bars[:, 4]))]
    vol = _rolling(r, 16, "std")
    for t in range(16, n-horizon):
        width = barrier * vol[t] * np.sqrt(horizon)
        if not np.isfinite(width) or width <= 0:
            continue
        lower, upper = bars[t, 4]*np.exp(-width), bars[t, 4]*np.exp(width)
        y[t] = 1
        for k in range(t+1, t+horizon+1):
            if bars[k, 3] <= lower:
                y[t] = 0
                break
            if bars[k, 2] >= upper:
                y[t] = 2
                break
    return y, end


def hourly_targets(bars: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Next completed hourly close / current completed close minus one."""
    validate_bars(bars)
    return np.r_[bars[1:, 4]/bars[:-1, 4]-1, np.nan], np.arange(len(bars))+1


def purged_indices(mask: np.ndarray, end: np.ndarray, x: np.ndarray,
                   y: np.ndarray) -> np.ndarray:
    """Require label's full future window inside its own contiguous split."""
    indices = np.flatnonzero(mask)
    if not len(indices) or np.any(np.diff(indices) != 1):
        raise ValueError("Split must be nonempty and contiguous")
    return indices[(end[indices] <= indices[-1]) & np.isfinite(y[indices]) &
                   np.isfinite(x[indices]).all(axis=1)]


def _network(n_inputs: int, bottleneck: int) -> Any:
    import torch
    nn = torch.nn
    class SAE(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.encoder = nn.Sequential(nn.Linear(n_inputs, 32), nn.ReLU(),
                                         nn.Linear(32, bottleneck), nn.ReLU())
            self.decoder = nn.Sequential(nn.Linear(bottleneck, 32), nn.ReLU(),
                                         nn.Linear(32, n_inputs))
            self.classifier = nn.Linear(bottleneck, 3)
        def forward(self, inputs: Any) -> tuple[Any, Any]:
            z = self.encoder(inputs)
            return self.classifier(z), self.decoder(z)
    return SAE()


@dataclass
class FittedModel:
    family: str
    config: dict[str, Any]
    transform: FoldTransform
    model: Any
    validation_loss: float
    metadata: dict[str, Any]

    def predict(self, x: np.ndarray) -> np.ndarray:
        inputs = self.transform.apply(x)
        if self.family == "B":
            from xgboost import DMatrix
            return self.model.predict(DMatrix(inputs, nthread=1))
        import torch
        self.model.eval()
        with torch.no_grad():
            logits, _ = self.model(torch.from_numpy(inputs))
            return torch.softmax(logits, dim=1).numpy()


def fit_fold(bars: np.ndarray, train_mask: np.ndarray, validation_mask: np.ndarray,
             family: str, *, features: tuple[np.ndarray, tuple[str, ...]] | None = None
             ) -> FittedModel:
    """Fit two preregistered configurations; choose validation predictive loss.

    No refitting on validation. No economic results are accepted by this API.
    """
    if family not in ("A", "B"):
        raise ValueError("Only two registered families")
    tr, va = np.flatnonzero(train_mask), np.flatnonzero(validation_mask)
    if not len(tr) or not len(va) or tr[-1] >= va[0]:
        raise ValueError("Train must strictly precede validation")
    x, names = features if features is not None else feature_matrix(bars)
    fitted = []
    for config in A_CONFIGS if family == "A" else B_CONFIGS:
        y, end = (triple_barrier_labels(bars, config["horizon"], config["barrier"])
                  if family == "A" else hourly_targets(bars))
        ti = purged_indices(train_mask, end, x, y)
        vi = purged_indices(validation_mask, end, x, y)
        if len(ti) < 100 or len(vi) < 10:
            raise ValueError("Insufficient purged training/validation data")
        transform = FoldTransform.fit(x[ti], names)
        xt, xv = transform.apply(x[ti]), transform.apply(x[vi])
        if family == "B":
            from xgboost import DMatrix, train
            parameters = dict(max_depth=config["max_depth"], eta=.03, min_child_weight=20,
                subsample=1, colsample_bytree=1, alpha=0, reg_lambda=10,
                objective="reg:squarederror", tree_method="hist", nthread=1, seed=SEED)
            model = train(parameters, DMatrix(xt, label=y[ti], nthread=1),
                          num_boost_round=config["n_estimators"])
            loss = float(np.mean((model.predict(DMatrix(xv, nthread=1))-y[vi])**2))
            gains = model.get_score(importance_type="gain")
            importance = [float(gains.get(f"f{i}", 0)) for i in range(xt.shape[1])]
        else:
            import torch
            torch.set_num_threads(1)
            torch.manual_seed(SEED)
            torch.use_deterministic_algorithms(True)
            model = _network(xt.shape[1], config["bottleneck"])
            optimizer = torch.optim.Adam(model.parameters(), lr=.001, weight_decay=.0001)
            tx, ty = torch.from_numpy(xt), torch.tensor(y[ti], dtype=torch.long)
            model.train()
            for _ in range(15):
                for start in range(0, len(ti), 256):
                    clean, target = tx[start:start+256], ty[start:start+256]
                    noisy = clean + config["noise"] * torch.randn_like(clean)
                    logits, reconstructed = model(noisy)
                    objective = torch.nn.functional.cross_entropy(logits, target)
                    objective += .1*torch.nn.functional.mse_loss(reconstructed, clean)
                    optimizer.zero_grad()
                    objective.backward()
                    optimizer.step()
            model.eval()
            with torch.no_grad():
                logits, _ = model(torch.from_numpy(xv))
                loss = float(torch.nn.functional.cross_entropy(
                    logits, torch.tensor(y[vi], dtype=torch.long)))
                importance = model.encoder[0].weight.abs().mean(0).numpy().tolist()
        metadata = {"seed": SEED, "train_rows": len(ti), "validation_rows": len(vi),
            "last_train_label_index": int(end[ti].max()),
            "first_validation_index": int(va[0]), "feature_names": list(transform.names),
            "fractional_d": transform.fractional_d, "importance": importance,
            "importance_kind": "gain" if family == "B" else "mean_abs_encoder_weight",
            "scaler_mean": transform.mean.tolist(), "scaler_scale": transform.scale.tolist()}
        fitted.append(FittedModel(family, dict(config), transform, model, loss, metadata))
    winner = min(fitted, key=lambda item: item.validation_loss)
    winner.metadata["configuration_losses"] = [item.validation_loss for item in fitted]
    return winner
