"""Chat tool dispatcher fragments, one module per tool or family (RETRO-FRAG-1).

Each module holds ``DISPATCHERS`` (and optionally ``PRE_DISPATCH_VALIDATORS``)
for the tools of the same-stem module in ``harness/catalogue_tools/``.
``services/chat_tools.py`` merges them before its gates wrap DISPATCHERS. See
``harness/tool_fragments.py``.
"""
