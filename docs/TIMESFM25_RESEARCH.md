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
runtime choices. PyTorch 2.7.1 has an official macOS wheel. All experiment
outputs must record exact dependency versions, model revision, device, source
commit, and data manifest hash.

Primary source references:

- [TimesFM 2.0.2 signed release](https://github.com/google-research/timesfm/releases/tag/v2.0.2)
- [TimesFM v2.0.2 package metadata](https://raw.githubusercontent.com/google-research/timesfm/v2.0.2/pyproject.toml)
- [TimesFM v2.0.2 2.5 PyTorch implementation](https://github.com/google-research/timesfm/blob/v2.0.2/src/timesfm/timesfm_2p5/timesfm_2p5_torch.py)
- [TimesFM v2.0.2 forecast API and quantile order](https://github.com/google-research/timesfm/blob/v2.0.2/src/timesfm/timesfm_2p5/timesfm_2p5_base.py)
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
3. Evaluate crash/dip filters only with the existing L3 shadow protocol. Do not
   infer acceptance from the small number of episodes or alter strategy/risk
   code here.

The kill criterion is no material improvement over persistence/EWMA under the
existing costs and validation gates. Register every feature/model/filter
variant in `ResearchRegistry` with a verified dataset manifest hash before
outcomes are inspected. `register_variants` registers the three planned families.

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
