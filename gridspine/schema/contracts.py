"""Stage-boundary contracts. Every artifact crossing a gridspine stage
boundary is validated here; stages never import each other's internals."""


class ContractError(ValueError):
    """An artifact violates its stage-boundary contract."""
