"""Qlib model inference strategy: score the cross-section, hold the top k.

Loads artifacts persisted by finora.strategy.train (model.pkl,
dataset_config.json, meta.json). qlib itself is imported lazily so
`import finora.strategy` never requires the qlib extra.
"""
from __future__ import annotations

import json
import pickle
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from finora.core.config import Settings
from finora.core.errors import ConfigError
from finora.core.log import get_logger
from finora.core.models import Signal

log = get_logger(__name__)

ARTIFACT_FILES = ("model.pkl", "dataset_config.json", "meta.json")

# Calendar days of predictions to request; only the last cross-section
# at or before as_of is used.
PREDICTION_WINDOW_DAYS = 30

_qlib_initialized = False


def ensure_qlib_init(settings: Settings) -> None:
    """qlib.init against the local provider dir, exactly once per process."""
    global _qlib_initialized
    if _qlib_initialized:
        return
    try:
        import qlib

        try:
            from qlib.constant import REG_US
        except ImportError:  # pragma: no cover - older qlib layouts
            from qlib.config import REG_US
    except ImportError as exc:
        raise ConfigError(
            "qlib is required for this operation; install it with `uv sync --extra qlib`"
        ) from exc
    qlib.init(provider_uri=str(settings.data.qlib_dir), region=REG_US)
    _qlib_initialized = True
    log.info("qlib_initialized", provider_uri=str(settings.data.qlib_dir))


def require_artifacts(model_dir: Path, strategy_name: str) -> None:
    """Raise ConfigError if any training artifact is missing (pure filesystem check)."""
    missing = [name for name in ARTIFACT_FILES if not (model_dir / name).exists()]
    if missing:
        raise ConfigError(
            f"strategy '{strategy_name}': missing model artifacts {missing} under "
            f"{model_dir}; run `finora train {strategy_name}` first"
        )


def load_artifacts(model_dir: Path, strategy_name: str) -> tuple[object, dict, dict]:
    """Return (model, dataset_config, meta) persisted by finora.strategy.train."""
    require_artifacts(model_dir, strategy_name)
    with (model_dir / "model.pkl").open("rb") as fh:
        model = pickle.load(fh)
    dataset_config = json.loads((model_dir / "dataset_config.json").read_text())
    meta = json.loads((model_dir / "meta.json").read_text())
    return model, dataset_config, meta


class QlibStrategy:
    """Top-k equal-weight strategy over a trained qlib model's scores.

    params:
        model_dir: directory holding model.pkl / dataset_config.json / meta.json
        top_k: number of names to hold (default 30)
        n_drop: unused at signal time; kept for backtest parity (default 0)
    """

    def __init__(self, name: str, params: dict, settings: Settings) -> None:
        self.name = name
        self.params = dict(params)
        self.top_k = int(params.get("top_k", 30))
        self.n_drop = int(params.get("n_drop", 0))
        self.model_dir = Path(params.get("model_dir") or Path("artifacts/models") / name)
        self._settings = settings

    def generate_signals(self, as_of: date) -> list[Signal]:
        # Cheap filesystem check first so a missing model fails fast without
        # touching qlib at all.
        require_artifacts(self.model_dir, self.name)
        ensure_qlib_init(self._settings)
        model, dataset_config, _meta = load_artifacts(self.model_dir, self.name)

        from qlib.utils import init_instance_by_config

        end_ts = pd.Timestamp(as_of)
        end_str = end_ts.strftime("%Y-%m-%d")
        pred_start = (end_ts - pd.Timedelta(days=PREDICTION_WINDOW_DAYS)).strftime("%Y-%m-%d")

        handler_cfg = json.loads(json.dumps(dataset_config["handler"]))  # deep copy
        handler_kwargs = handler_cfg.setdefault("kwargs", {})
        handler_kwargs["end_time"] = end_str

        dataset = init_instance_by_config(
            {
                "class": "DatasetH",
                "module_path": "qlib.data.dataset",
                "kwargs": {"handler": handler_cfg, "segments": {"test": (pred_start, end_str)}},
            }
        )
        pred = model.predict(dataset, segment="test")
        if isinstance(pred, pd.DataFrame):
            pred = pred.iloc[:, 0]
        if pred is None or len(pred) == 0:
            log.warning("qlib_no_predictions", strategy=self.name, as_of=str(as_of))
            return []

        dates = pred.index.get_level_values(0)
        usable = dates[dates <= end_ts]
        if len(usable) == 0:
            log.warning("qlib_no_predictions_at_or_before", strategy=self.name, as_of=str(as_of))
            return []
        cross = pred.xs(usable.max(), level=0).dropna()
        selected = cross.nlargest(self.top_k)
        if selected.empty:
            log.warning("qlib_empty_cross_section", strategy=self.name, as_of=str(as_of))
            return []

        # Confidence: softmax over the score ranks of the selected names,
        # so the best-ranked name gets the highest confidence in (0, 1).
        ranks = selected.rank(method="first", ascending=True).to_numpy(dtype=float)
        z = np.exp(ranks - ranks.max())
        confidence = z / z.sum()

        weight = 1.0 / self.top_k
        signals = [
            Signal(
                instrument=str(symbol),
                target_weight=weight,
                confidence=float(conf),
                as_of=as_of,
                source=self.name,
            )
            for symbol, conf in zip(selected.index, confidence)
        ]
        log.info(
            "qlib_signals",
            strategy=self.name,
            as_of=str(as_of),
            n_scored=len(cross),
            n_selected=len(signals),
        )
        return signals
