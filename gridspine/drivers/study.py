"""The year study as a JOB: one config in, one `StudyResult` out.

Increment 4, task 1. `year_study` already has the stages (`dispatch_year`,
`study_dispatch`, `resume_from_dispatch` — follow-ups F3); what a job runner
needs on top is a serialisable configuration it can store beside the project,
a progress callback, and a stop. That is all this module adds — it holds no
modelling logic, and the backend's action layer calls nothing else.

`StudyConfig` validates on construction, so an impossible run is refused
before a directory is created, let alone a solver started. The same object is
written into the manifest (`config`), which makes a finished run reproducible
from its own artifacts.
"""
import dataclasses
from pathlib import Path

from gridspine.drivers.progress import Progress
from gridspine.drivers.year_study import (
    dispatch_from_network,
    dispatch_year,
    resume_from_dispatch,
    study_dispatch,
)
from gridspine.schema.contracts import ContractError


@dataclasses.dataclass(frozen=True)
class StudyConfig:
    """Everything the CLI takes, as data. Validated here, not at the solver."""

    #: where the artifacts go; created by the run, not by this object
    outdir: Path
    hours: int = 8760
    k: int = 5
    window: int = 168
    overlap: int = 24
    screen: bool = True
    n2_prune_threshold_pct: float = 0.0
    #: a finished run's directory to study instead of solving (F3); when set,
    #: `hours`, `window` and `overlap` describe that run, not this one
    from_dispatch: Path = None
    #: a SOLVED PyPSA network (a project's saved `network.nc` whose generators
    #: are the detailed grid's units) to study instead of solving (increment
    #: 5, D3); exclusive with `from_dispatch`, and `hours` then describes it
    from_network: Path = None
    #: per-study template edits applied on top of the shipped library
    #: (`templates.unit_params.load_unit_templates(overlay=...)`); the library
    #: itself is never written, so two studies can disagree about a machine and
    #: each keeps its own provenance
    templates_overlay: Path = None

    def __post_init__(self):
        object.__setattr__(self, "outdir", Path(self.outdir))
        for name in ("from_dispatch", "from_network", "templates_overlay"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, Path(value))
        if self.from_dispatch is not None and self.from_network is not None:
            raise ContractError(
                "from_dispatch and from_network are exclusive: a study has one dispatch source"
            )
        for name in ("hours", "k"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ContractError(f"{name} must be a positive integer, got {value!r}")
        if isinstance(self.window, bool) or not isinstance(self.window, int) or self.window <= 0 or self.window % 24:
            raise ContractError(
                f"window must be a positive whole number of days, got {self.window!r}"
            )
        if isinstance(self.overlap, bool) or not isinstance(self.overlap, int) or not 0 <= self.overlap < self.window:
            raise ContractError(
                f"overlap must satisfy 0 <= overlap < window={self.window}, got {self.overlap!r}"
            )
        if not isinstance(self.screen, bool):
            raise ContractError(f"screen must be a bool, got {self.screen!r}")
        if not isinstance(self.n2_prune_threshold_pct, (int, float)) or self.n2_prune_threshold_pct < 0:
            raise ContractError(
                f"n2_prune_threshold_pct must be >= 0, got {self.n2_prune_threshold_pct!r}"
            )

    def to_json(self) -> dict:
        d = dataclasses.asdict(self)
        d["outdir"] = str(self.outdir)
        d["from_dispatch"] = None if self.from_dispatch is None else str(self.from_dispatch)
        d["from_network"] = None if self.from_network is None else str(self.from_network)
        d["templates_overlay"] = None if self.templates_overlay is None else str(self.templates_overlay)
        d["n2_prune_threshold_pct"] = float(self.n2_prune_threshold_pct)
        return d

    @classmethod
    def from_json(cls, data: dict) -> "StudyConfig":
        known = {f.name for f in dataclasses.fields(cls)}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ContractError(f"StudyConfig has no field(s) {unknown}")
        return cls(**data)


def run_study(config: StudyConfig, progress=None, stop_event=None):
    """Run `config` to a `StudyResult`, reporting progress and honouring a stop.

    `progress(stage, done, total)` is called at every stage boundary and inside
    the three loops that take real time (UC windows, the AC year pass, the
    per-hour screening); `stop_event` needs only `is_set()`. A set stop raises
    `StudyAborted` after the stage's `StageError` artifact is written, so the
    caller sees where it stopped and a run past the dispatch stage can be
    finished later with `from_dispatch`.

    Three dispatch sources, in precedence order: `from_dispatch` (another
    study's tables), `from_network` (a solved PyPSA network — D3), else the
    rolling unit commitment on case39.
    """
    if not isinstance(config, StudyConfig):
        raise ContractError(f"run_study takes a StudyConfig, got {type(config).__name__}")
    bus = Progress(progress, stop_event)
    common = dict(
        k=config.k, screen=config.screen,
        n2_prune_threshold_pct=config.n2_prune_threshold_pct,
        progress=bus, config_json=config.to_json(),
        templates_overlay=config.templates_overlay,
    )
    if config.from_dispatch is not None:
        return resume_from_dispatch(config.from_dispatch, config.outdir, **common)
    if config.from_network is not None:
        net, registry, dispatch, loads, source = dispatch_from_network(
            config.from_network, config.outdir, progress=bus,
        )
        return study_dispatch(
            config.outdir, net, registry, dispatch, loads, dispatch_source=source, **common,
        )
    net, registry, dispatch, loads = dispatch_year(
        config.outdir, hours=config.hours, window=config.window,
        overlap=config.overlap, progress=bus,
    )
    return study_dispatch(
        config.outdir, net, registry, dispatch, loads,
        window=config.window, overlap=config.overlap, **common,
    )
