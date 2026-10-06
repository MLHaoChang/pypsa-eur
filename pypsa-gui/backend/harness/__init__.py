"""
The chat harness: everything a provider-agnostic assistant needs, in one place.

Layout (see README.md in this folder for the contract):

    protocol    the provider seam — LLMProvider, LLMRequest, LLMEvent, error kinds
    catalogue   the tool declarations, safety tiers and route map
    events      the closed vocabulary of frames the turn loop yields to the UI
    workflows   the start menu and the step-by-step flows the assistant leads
    skills      reusable procedures the model loads on demand

Dependency arrow: ``services/* <- harness <- harness/providers``. Nothing in
this package outside ``providers/`` names a provider, an SDK or a wire
format; ``tests/test_harness_layout.py`` greps for it.
"""
from __future__ import annotations
