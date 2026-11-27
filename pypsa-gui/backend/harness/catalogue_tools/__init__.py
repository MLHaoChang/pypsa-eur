"""Chat tool schema fragments, one module per tool or family (RETRO-FRAG-1).

Each module holds ``TOOLS`` and ``ROUTES`` (plus optional ``NETWORK_WRITES``,
``NON_NETWORK_WRITES`` and ``ORDER``) and imports only
``harness.catalogue_base``. Its dispatchers live in the same-stem module of
``services/chat_dispatchers/``. See ``harness/tool_fragments.py``.
"""
