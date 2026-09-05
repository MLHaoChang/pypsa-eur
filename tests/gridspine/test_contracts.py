from gridspine.schema.contracts import ContractError


def test_contract_error_is_exception():
    assert issubclass(ContractError, Exception)


def test_pandapower_importable():
    import pandapower  # noqa: F401  — env wiring check (test code, not an engine-cage violation)


def test_lightsim2grid_importable_at_the_pinned_version():
    """Env wiring for increment 3 (test code, not an engine-cage violation).

    lightsim2grid is pinned exactly in pixi.toml for the same reason pandapower
    is: a range resolves differently per platform. The pin is the version
    pandapower 3.1.2 names in its own `performance` extra, so the pair is the
    one pandapower tested against — not the newest release (1.0.0 is a major).
    """
    import lightsim2grid  # noqa: F401

    assert lightsim2grid.__version__ == "0.10.1"
