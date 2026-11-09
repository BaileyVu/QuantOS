"""gbm.py - small histogram gradient-boosted trees (binary logloss) in pure numpy.

Exists so the research needs no package installs. Depth-wise trees, 255 quantile bins + 1 missing bin,
L2 leaf regularisation, row/column subsampling. Deterministic given seed.
"""
import numpy as np

NB = 256  # bins 0..254 values, 255 = missing


class Binner:
    def fit(self, X, max_rows=200000, seed=0):
        rng = np.random.default_rng(seed)
        idx = rng.choice(len(X), min(len(X), max_rows), replace=False)
        self.edges = []
        for j in range(X.shape[1]):
            v = X[idx, j]
            v = v[np.isfinite(v)]
            if len(v) == 0:
                self.edges.append(np.array([0.0]))
                continue
            e = np.unique(np.quantile(v, np.linspace(0, 1, NB)[1:-1]))
            self.edges.append(e)
        return self

    def transform(self, X):
        B = np.empty(X.shape, dtype=np.uint8)
        for j, e in enumerate(self.edges):
            col = X[:, j]
            b = np.searchsorted(e, col, side="right").astype(np.int64)
            b[~np.isfinite(col)] = NB - 1
            B[:, j] = np.minimum(b, NB - 2) * np.isfinite(col) + (NB - 1) * (~np.isfinite(col))
        return B


class GBM:
    def __init__(self, n_trees=200, depth=4, lr=0.05, lam=1.0, min_child=200, subsample=0.7, colsample=0.8, seed=0):
        self.n_trees, self.depth, self.lr, self.lam = n_trees, depth, lr, lam
        self.min_child, self.subsample, self.colsample, self.seed = min_child, subsample, colsample, seed

    def fit(self, B, y, w=None):
        rng = np.random.default_rng(self.seed)
        n, F = B.shape
        y = y.astype(np.float64)
        w = np.ones(n) if w is None else w.astype(np.float64)
        p0 = np.clip(np.average(y, weights=w), 1e-4, 1 - 1e-4)
        self.base = np.log(p0 / (1 - p0))
        raw = np.full(n, self.base)
        self.trees = []
        Bi = B.astype(np.int64)
        for t in range(self.n_trees):
            p = 1 / (1 + np.exp(-raw))
            g = (p - y) * w
            h = np.maximum(p * (1 - p), 1e-6) * w
            rows = np.flatnonzero(rng.random(n) < self.subsample)
            cols = np.flatnonzero(rng.random(F) < self.colsample)
            if len(cols) == 0:
                cols = np.array([rng.integers(F)])
            tree = self._grow(Bi, g, h, rows, cols)
            self.trees.append(tree)
            raw += self.lr * self._predict_tree(tree, Bi)
        return self

    def _grow(self, Bi, g, h, rows, cols):
        # tree arrays for a complete binary tree of given depth: feat, thr per internal node; leaf values
        D = self.depth
        n_int = 2 ** D - 1
        feat = np.full(n_int, -1, dtype=np.int64)
        thr = np.zeros(n_int, dtype=np.int64)
        node = np.zeros(len(rows), dtype=np.int64)  # node index within current level
        gr, hr = g[rows], h[rows]
        for d in range(D):
            nn = 2 ** d
            best_gain = np.zeros(nn)
            best_f = np.full(nn, -1)
            best_t = np.zeros(nn, dtype=np.int64)
            G = np.bincount(node, weights=gr, minlength=nn)
            Hs = np.bincount(node, weights=hr, minlength=nn)
            C = np.bincount(node, minlength=nn)
            parent = G ** 2 / (Hs + self.lam)
            for f in cols:
                key = node * NB + Bi[rows, f]
                hg = np.bincount(key, weights=gr, minlength=nn * NB).reshape(nn, NB)
                hh = np.bincount(key, weights=hr, minlength=nn * NB).reshape(nn, NB)
                hc = np.bincount(key, minlength=nn * NB).reshape(nn, NB)
                GL, HL, CL = np.cumsum(hg, 1)[:, :-1], np.cumsum(hh, 1)[:, :-1], np.cumsum(hc, 1)[:, :-1]
                GR, HR, CR = G[:, None] - GL, Hs[:, None] - HL, C[:, None] - CL
                gain = GL ** 2 / (HL + self.lam) + GR ** 2 / (HR + self.lam) - parent[:, None]
                gain[(CL < self.min_child) | (CR < self.min_child)] = -np.inf
                k = np.argmax(gain, 1)
                gk = gain[np.arange(nn), k]
                better = gk > best_gain
                best_gain[better], best_f[better], best_t[better] = gk[better], f, k[better]
            base = nn - 1
            feat[base:base + nn] = best_f
            thr[base:base + nn] = best_t
            # route rows; nodes with no split send everything left
            fsel = best_f[node]
            go_right = np.zeros(len(rows), dtype=bool)
            ok = fsel >= 0
            go_right[ok] = Bi[rows[ok], fsel[ok]] > best_t[node[ok]]
            node = node * 2 + go_right
        nl = 2 ** D
        G = np.bincount(node, weights=gr, minlength=nl)
        Hs = np.bincount(node, weights=hr, minlength=nl)
        leaf = -G / (Hs + self.lam)
        return feat, thr, leaf

    def _predict_tree(self, tree, Bi):
        feat, thr, leaf = tree
        node = np.zeros(len(Bi), dtype=np.int64)
        for d in range(self.depth):
            idx = (2 ** d - 1) + node
            f = feat[idx]
            ok = f >= 0
            right = np.zeros(len(Bi), dtype=bool)
            right[ok] = Bi[np.flatnonzero(ok), f[ok]] > thr[idx[ok]]
            node = node * 2 + right
        return leaf[node]

    def predict_proba(self, B):
        Bi = B.astype(np.int64)
        raw = np.full(len(B), self.base)
        for tree in self.trees:
            raw += self.lr * self._predict_tree(tree, Bi)
        return 1 / (1 + np.exp(-raw))
