from gridspine.schema.contracts import ContractError


def test_contract_error_is_exception():
    assert issubclass(ContractError, Exception)


def test_pandapower_importable():
    import pandapower  # noqa: F401  — env wiring check (test code, not an engine-cage violation)
