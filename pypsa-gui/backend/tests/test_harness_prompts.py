"""
The system-prompt fragments live in `harness/prompts/*.md` (chat harness
issue 02). Moving them out of `chat_service.py` must not move a byte: the
prompt is cached by prefix and the default prompt is pinned by hash
(`test_chat_profile_binding.test_default_prompt_bytes_unchanged`). These
hashes were captured from the constants BEFORE the move, on 2026-10-05
(commit 43b36fb), for every fragment and every half — not just the six the
older test pins.
"""
from __future__ import annotations

import hashlib

import pytest

EXPECTED_SHA256 = {
    "_BASE_IDENTITY_FACTS": "bfcb3cf8f7f5eeb6b857aab7f1f89a9998239fbcbadaa7571c377ab32959ff32",
    "_BASE_IDENTITY_CHAINING": "4c8f6574926078a5fb9639e88d19eb510a9b6445e6c6b59f9925de50965ca1f7",
    "_BASE_IDENTITY": "aa4112c6f2a69e05304e044f4ed4c413545bd32ca9f76acd784282a1c10b12d2",
    "_CONFIRMATION_CARD_CONTRACT_TEMPLATE": "b471392537b952c84d95d7af7b379c938ac290c933138e693a145a1fbd007eb9",
    "_STYLE_GUIDANCE": "c31b98f1ea587f1d77e644a5fe721afce4316155b950d6c479c002f120a6912b",
    "_ASSISTANT_STANCE_FACTS": "18137511a7024922e0c648edc3110c39d09621d8fcf3ad1b458ee8374b320cb0",
    "_ASSISTANT_STANCE_CHAINING": "ec026429da8c202c36d81a4f5343cf5bc0e1653d10e572b107f3f6420d66d354",
    "_ASSISTANT_STANCE": "1dd77952d0cdec42606c4d269adf31aae9d11ae8ef22cf83103ac0659028889b",
    "_DOMAIN_GUIDE_FACTS": "8ce3002dae7b4b8897d07f876eb661c700ddfd74707fb7b56208f16190bfd7c8",
    "_DOMAIN_GUIDE_CHAINING": "9cab8c228ba6e980e26d747cb0047d3b515850c8e3f157d3d3b4d79e2700ca50",
    "_DOMAIN_GUIDE": "3e6f420d74fea27240186cc520718dd401fd7b18a6e0fef1262630f053256f8f",
    "_SOLVER_ERROR_DECODER": "bd4de84083da126945d36c23a0e10bb0823d157ce8befb9aecf7c1b899c029db",
    "_SOLVER_ERROR_DECODER_FACTS": "66a66dd1cfc4f25113aac2128b29b37c781362f8fe16636a81b03398a281d8ae",
    "_SOLVER_ERROR_DECODER_CHAINING": "456e4a7243f53aa2c162e0988b8e96eb0823d8054bf6317a684622d2ba929dc4",
    "_PRICE_CONGESTION_GUIDE_FACTS": "49a3ea00147d9c9a7d86a2abd1a67c525b065d19a354db5d80d9d95028ac3c1f",
    "_PRICE_CONGESTION_GUIDE_CHAINING": "b7fc611146ea2e0afe60d9d4c54f17d84e9e21e283f9fa38a0d1ab292f6f2360",
    "_PRICE_CONGESTION_GUIDE": "a65668e11eee7e3f30007ffc91cc7f3197e2a6938c7e8ea6e1fd647b5aa25129",
    "_NEXT_STEP_RUBRIC": "991709d96f9cb8b42db7b03d0b28cb5bc3aeeab7e6de57ab63dff962cc79fe1b",
    "_NEXT_STEP_RUBRIC_FACTS": "ab90f78e639bc63ee74e88d9b062166feca0e69a3a1dda3438a9f8f526b70e43",
    "_NEXT_STEP_RUBRIC_CHAINING": "c1715cc5982b23ad9c6cfa5434350f6db0b6375bd2a85a63eac5af7dd6ba38b6",
    "_ADEQUACY_GUIDE_FACTS": "38ef34134e9036e787d12718a5a86dc1f46d42be743f25a689c991480f9f1219",
    "_ADEQUACY_GUIDE_CHAINING": "ff4e22f1f7e360f8f21a833b10a3cb6ff918161dfb63f171ff64c3c1c29ff622",
    "_ADEQUACY_GUIDE": "84604d385100a9c9956e20c622e7de159763ceb0a4d4b88778ae696cf24478ea",
    "_EH_GUIDE_FACTS": "ca495a300a1bd8700025a50c27d49cb325d6655ee04215ea6fd561d64773f510",
    "_EH_GUIDE_CHAINING": "1e693728cf7d7fe3a9a5a0d12c59bc75fef1cf21587bfe1b1c977644f89dd052",
    "_EH_GUIDE": "1b9fc45244265b489e37c3d8420b389e842dfd9b2871c130ff13415563d20a27",
    "_UNTRUSTED_DATA_CLAUSE": "4909db27d7388788b3457f74a9f927a19173f0d5903eda36d55a88c8f81ba867",
}


@pytest.mark.parametrize("name", sorted(EXPECTED_SHA256))
def test_prompt_fragment_bytes_unchanged(name):
    from services import chat_service

    value = getattr(chat_service, name)
    assert isinstance(value, str)
    assert hashlib.sha256(value.encode()).hexdigest() == EXPECTED_SHA256[name], (
        f"{name} changed bytes; the system prompt is pinned (see module docstring)"
    )


def test_the_golden_is_red_on_a_planted_byte():
    value = "x"
    assert hashlib.sha256(value.encode()).hexdigest() != EXPECTED_SHA256["_STYLE_GUIDANCE"]
