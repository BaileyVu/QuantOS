# TimesFM 2.5 research harness

This is research-only infrastructure. It does not add a QuantOS runtime
dependency, download weights during installation, train a model, access an
exchange, or promote a signal. The implementation and tests did not load weights
or run inference. `timesfm_experiment.py` is a pure-Python injected-predictor
adapter with an optional lazy model adapter; ordinary QuantOS installations do
not import torch or TimesFM.

## Pinned environment and model

Use a separate Python 3.11 virtual environment and install
`requirements-timesfm25.txt`. Before any later model run, set
`HF_HUB_DISABLE_TELEMETRY=1`. On this Mac, set `CUDA_VISIBLE_DEVICES=""` and
verify the model's selected device is CPU before inference. The upstream 2.5
module selects CUDA only when CUDA is available, otherwise CPU; the adapter
rejects a non-CPU device. The approved model id is
`google/timesfm-2.5-200m-pytorch`; do not substitute TimesFM 3.0. Pin the model
repository revision in any run manifest. The inspected 2.5 checkpoint revision
is `2d1b32066b7f3950c45913d4a3849f3c75cb0312` (model card license Apache-2.0).
No weights were downloaded during harness implementation.

TimesFM upstream's signed `v2.0.2` release exposes the 2.5 PyTorch API; its
package declares Python >=3.10, NumPy >=1.26.4, Hugging Face Hub >=0.23.0,
Safetensors >=0.5.3, and optional torch >=2.0.0. The requirement file pins those
runtime choices. Hub is pinned at 0.30.2 because the tagged TimesFM class
declares `paper_url`, which older Hub versions (including 0.28.1) reject at
import time. PyTorch 2.7.1 has an official macOS wheel. All experiment
outputs must record exact dependency versions, model revision, device, source
commit, and data manifest hash.

`timesfm_model.load_model(cache_dir=...)` is the explicit loader used by offline
research runners. It requires a caller-supplied cache directory, sets
`HF_HUB_DISABLE_TELEMETRY=1` before importing Hub/model packages, disables CUDA,
uses `token=False`, bounds torch to two CPU threads, checks the pinned weights
SHA-256 before constructing the model, compiles the documented context/horizon
configuration, and returns `(model, metadata)`. Loading the artifact is a
separate explicit action; importing the module and running synthetic tests do
not load weights or perform model inference.

Primary source references:

- [TimesFM 2.0.2 signed release](https://github.com/google-research/timesfm/releases/tag/v2.0.2)
- [TimesFM v2.0.2 package metadata](https://raw.githubusercontent.com/google-research/timesfm/v2.0.2/pyproject.toml)
- [TimesFM v2.0.2 2.5 PyTorch implementation](https://github.com/google-research/timesfm/blob/v2.0.2/src/timesfm/timesfm_2p5/timesfm_2p5_torch.py)
- [TimesFM v2.0.2 forecast API and quantile order](https://github.com/google-research/timesfm/blob/v2.0.2/src/timesfm/timesfm_2p5/timesfm_2p5_base.py)
- [Hugging Face Hub 0.30.2 `paper_url` mixin API](https://github.com/huggingface/huggingface_hub/blob/v0.30.2/src/huggingface_hub/hub_mixin.py)
- [Official TimesFM 2.5 model card and license](https://huggingface.co/google/timesfm-2.5-200m-pytorch)
- [Official PyTorch previous versions, including macOS 2.7.1](https://pytorch.org/get-started/previous-versions/)

## Causal data contract

Input rows are completed UTC hourly closes, strictly increasing and contiguous.
The predictor receives only the prefix through the current completed close. Its
forecast origin must equal that close's timestamp and its origin price must
match the close. Features are median drift at 4/12/24 hours, 24-hour
`(q90-q10)/price`, and surprise against the immediately prior forecast for the
just-completed bar. Missing prior forecasts yield `None`; a forecast originating
at or after the completed bar, a missing target horizon, an invalid quantile
interval, or a date outside the requested period is rejected.

The optional `timesfm25_predictor` adapter accepts an already loaded and
compiled upstream model and calls its `forecast(horizon=24, inputs=[...])` API.
The adapter itself does not load weights; each forecast receives only the
historical prefix through its origin. It maps the official quantile tensor's
median column 5 and q10/q90 columns 1/9.

BUILD is `[2020-01-01, 2023-07-01)` UTC. HELD is `[2023-07-01, 2025-01-01)` UTC.
The data adapter must not read or use 2025+ observations for any feature,
threshold, normalization, feature selection, or model selection. A future live
shadow evaluation requires separate explicit authorization and is outside this
task. Do not pass sealed 2025+ bars into `walk_forward_features`.

## Planned comparisons (not run here)

1. Add the features to ml1h in walk-forward evaluation and pass the existing L2
   gates. Keep per-fold fitting and transforms limited to each training prefix.
2. Compare the forecast width as a volatility forecast against persistence,
   EWMA, and ATR using matched realized-volatility targets and identical periods.
   The baseline helpers emit `VolatilityForecast` records with origin index and
   future target interval `[start, end)`. EWMA uses the square root of a causal
   EWMA return variance, initialized from the first squared return; its state at
   origin `i` uses observations only through `i`. For an `h`-bar target, score
   against realized RMS return over exactly `[i+1, i+h+1)`. ATR estimates can be
   aligned with `align_volatility_forecasts`; pass the correct first source
   index after its warmup. Compare only identical target intervals.
3. Evaluate crash/dip filters only with the existing L3 shadow protocol. Do not
   infer acceptance from the small number of episodes or alter strategy/risk
   code here; no filter thresholds are fit on HELD.

The kill criterion is no material improvement over persistence/EWMA under the
existing costs and validation gates. Register every feature/model/filter
variant in `ResearchRegistry` with a verified dataset manifest hash before
outcomes are inspected. `register_variants` registers the three planned families.
The TimesFM model id is fixed to the pinned 2.5 checkpoint; a conflicting
`model` in supplied configuration is rejected.

## Evidence needed before a model run

- Verified, immutable, checksummed hourly OHLCV per symbol from BUILD and HELD,
  with complete coverage, UTC completed-bar timestamps, explicit gaps, and
  source/filter provenance; no 2025+ selection data.
- A manifest hash and code/dependency/model revision metadata bound to each
  run, plus reproducible split/fold boundaries and normalization fit history.
- For the volatility comparison, a preregistered realized-volatility target
  horizon and identical bar coverage for TimesFM, persistence, EWMA, and ATR.
- For L3, the preexisting shadow logging path and enough episodes to report
  uncertainty honestly. No thresholds may be fitted on HELD.

No data manifest was supplied in this PR, so there are no experiments or
performance claims. The research harness is not a candidate for deployment.

## Executable BUILD volatility comparison

`python -m quantos.paper.research.timesfm_volatility --manifest MANIFEST
--manifest-hash SHA256 --registry REGISTRY --cache-dir MODEL_CACHE
--output-dir NEW_OUTPUT` consumes checksummed JSONL with completed UTC hourly
`time_ms`, `high`, `low`, `close` fields. It checks the manifest's BUILD range
before opening data, rejects gaps, registers the plan before inference, then
uses fixed 512-hour contexts and non-overlapping 24-hour targets. HELD and
2025+ are deliberately unavailable in this runner.

The target is the square root of summed next-24h squared log returns. The
predeclared comparison maps the model's 80% price interval to a volatility
proxy by dividing relative width by 2.563103131; persistence uses prior-24h
quadratic variation, EWMA uses alpha 0.06, and 14-hour ATR is divided by
sqrt(8/pi) then scaled by sqrt(24). These Gaussian/range assumptions are explicit,
with no outcome-fitted rescaling. MAE is descriptive forecast error, not P&L.
A favorable result alone cannot pass L2 or L3. The foundation model's training
corpus overlap with historical crypto targets is unverified; historical tests
must not be described as proof of unseen-data generalization.

The runner writes its plan before loading weights and a result containing
individual origins, targets, forecasts and model provenance. An interrupted run
leaves the plan, not a fabricated report. No real comparison has passed yet.
