"""
Replay one recorded turn on another provider (chat harness issue 13).

    python smoke/replay_session.py --events <session events.jsonl> --at <seq> --profile <id>

Forks the session just before the `turn/start` at `<seq>`, runs that turn
again in-process on the named LLM profile (as `llm_config` holds it), and
prints the original and the replayed trajectories side by side: tools
called, final text, usage. The original log is never written; the fork gets
its own log beside it, naming the parent.

Run it where the app's data directory and profiles are (the backend's
environment); a profile with a key it cannot reach fails as it would in a
chat.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def provider_for(profile_id: str):
    """The profile's provider and the model it asks for."""
    from harness.providers.wiring import _provider_for_profile
    from services import llm_config

    profiles, _active = llm_config.load_profiles()
    profile = next((p for p in profiles if p.id == profile_id), None)
    if profile is None:
        raise SystemExit(f"no LLM profile named {profile_id!r}")
    provider, error = _provider_for_profile(profile)
    if error:
        raise SystemExit(f"profile {profile_id!r} is not usable: {error}")
    return provider, profile.model


def _column(label: str, t: dict) -> list[str]:
    return [f"{label}:",
            f"  tools  {', '.join(t['tools']) or '(none)'}",
            f"  usage  {json.dumps(t['usage'], sort_keys=True)}",
            f"  end    {t['reason']}",
            f"  text   {t['text'][:300]}"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--at", required=True, type=int, help="seq of a turn/start")
    parser.add_argument("--profile", required=True)
    args = parser.parse_args(argv)

    from harness import replay

    provider, model = provider_for(args.profile)
    out = replay.replay(args.events, args.at, provider, model=model, profile_id=args.profile)
    print(f"turn: {out['user']!r}  (fork {out['fork_session'][:6]})")
    for line in _column("original", out["original"]) + _column(f"replayed on {args.profile}", out["replayed"]):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
