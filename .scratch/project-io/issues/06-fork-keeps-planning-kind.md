# 06 A fork of a planning project keeps its kind

Status: needs-triage

A scenario fork (`create_scenario`) and the study option forks (`services/study/forks.py`) do not copy `project_kind`, so a fork of a planning → dynamics project comes out as a plain capacity-expansion project, and issue 01 leaves `gridspine/` out of forks for that reason. With this issue a fork keeps its base's kind and carries `gridspine/` without `run/` (`project_contents.TABLE`: add `FORK` to the `gridspine` entry with `skip={FORK: ("run",)}`). The forked study's dispatch-source paths name the base project; decide whether the fork keeps or clears them.

Journey fit: decide/compare (scenario forks) for planning projects, Expert.
