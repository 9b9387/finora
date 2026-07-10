"""Train a qlib LightGBM model on Alpha158 features and persist inference artifacts."""
from __future__ import annotations

import json
import pickle
from pathlib import Path

from finora.core.config import Settings, StrategyConfig
from finora.core.errors import ConfigError, DataError
from finora.core.log import get_logger
from finora.core.models import utc_now

log = get_logger(__name__)

_SEGMENT_NAMES = ("train", "valid", "test")


def _provider_has_data(qlib_dir: Path) -> bool:
    calendars = qlib_dir / "calendars"
    features = qlib_dir / "features"
    return (
        calendars.is_dir()
        and any(calendars.iterdir())
        and features.is_dir()
        and any(features.iterdir())
    )


def _segments_from_params(cfg: StrategyConfig) -> dict[str, tuple[str, str]]:
    segments: dict[str, tuple[str, str]] = {}
    for seg in _SEGMENT_NAMES:
        pair = cfg.params.get(seg)
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ConfigError(
                f"strategy '{cfg.name}': params.{seg} must be a [start, end] date pair, "
                f"got {pair!r}"
            )
        segments[seg] = (str(pair[0]), str(pair[1]))
    return segments


def train_qlib_model(settings: Settings, cfg: StrategyConfig) -> Path:
    """Fit an LGBModel on Alpha158 and write model.pkl / dataset_config.json /
    meta.json to cfg.params['model_dir']. Returns the model directory."""
    segments = _segments_from_params(cfg)

    qlib_dir = settings.data.qlib_dir
    if not _provider_has_data(qlib_dir):
        raise DataError(
            f"qlib provider directory {qlib_dir} is empty or missing; "
            "run `finora etl` to build the local data store first"
        )

    from finora.strategy.qlib_strategy import ensure_qlib_init

    ensure_qlib_init(settings)

    import qlib
    from qlib.contrib.data.handler import Alpha158
    from qlib.contrib.model.gbdt import LGBModel
    from qlib.data.dataset import DatasetH

    handler_kwargs = {
        "instruments": "all",
        "start_time": segments["train"][0],
        "end_time": segments["test"][1],
        "fit_start_time": segments["train"][0],
        "fit_end_time": segments["train"][1],
    }
    log.info("train_start", strategy=cfg.name, segments=segments)
    handler = Alpha158(**handler_kwargs)
    dataset = DatasetH(handler=handler, segments=segments)
    model = LGBModel(loss="mse")
    model.fit(dataset)

    universe_size = -1
    try:
        labels = dataset.prepare("train", col_set="label")
        universe_size = int(labels.index.get_level_values("instrument").nunique())
    except Exception:  # noqa: BLE001 - meta only, never fail training over it
        log.warning("train_universe_size_unavailable", strategy=cfg.name)

    model_dir = Path(cfg.params.get("model_dir") or Path("artifacts/models") / cfg.name)
    model_dir.mkdir(parents=True, exist_ok=True)
    with (model_dir / "model.pkl").open("wb") as fh:
        pickle.dump(model, fh)
    dataset_config = {
        "handler": {
            "class": "Alpha158",
            "module_path": "qlib.contrib.data.handler",
            "kwargs": handler_kwargs,
        },
        "segments": {name: list(pair) for name, pair in segments.items()},
    }
    (model_dir / "dataset_config.json").write_text(json.dumps(dataset_config, indent=2))
    meta = {
        "trained_at": utc_now().isoformat(),
        "strategy": cfg.name,
        "universe": settings.universe.name,
        "universe_size": universe_size,
        "qlib_version": getattr(qlib, "__version__", "unknown"),
    }
    (model_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    log.info("train_done", strategy=cfg.name, model_dir=str(model_dir))
    return model_dir
