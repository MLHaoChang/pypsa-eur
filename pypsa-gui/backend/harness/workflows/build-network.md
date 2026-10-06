---
id: build-network
title: Build a network
intent: Add buses, lines, generators, loads and storage step by step, with a validation pass at the end.
when: [expert]
order: 20
opening_request: Help me build my network step by step. Start by showing me what the network contains now and ask what I want to add first.
steps:
  - id: orient
    title: See what is there
    done_when: The user has heard the bus, line, generator, load and storage counts and said what to add first.
  - id: buses
    title: Buses and carriers
    done_when: Every bus the user wants exists with a carrier and a nominal voltage.
  - id: branches
    title: Lines, links and transformers
    done_when: Every bus the user wants connected is connected and no island remains that the user did not intend.
  - id: assets
    title: Generators, loads and storage
    done_when: Each asset the user named exists with a capacity and a cost the user confirmed.
  - id: time
    title: Snapshots and profiles
    done_when: Snapshots are set and every load and variable generator has a profile or an explicit constant.
  - id: check
    title: Validate
    done_when: validate_network returns no error and the user has seen the warnings.
---

## Step: orient

Call `get_meta` and `list_components` for Bus, Line, Generator, Load and
StorageUnit. Say in two sentences what the network holds. Then ask with
`ask_user` what to add first: buses, connections, assets, or time data.
Recommend buses when there are none, otherwise the first empty category.

## Step: buses

For each bus the user describes, call `create_component` with class Bus,
name, **v_nom** and `carrier`; create missing carriers with `create_carrier`
first. Several buses at once go through `batch_create_components`. Repeat
the names back verbatim. When the user is done, move to branches.

## Step: branches

Ask which buses connect. Lines need `bus0`, `bus1`, **s_nom** and either
`length` or `x`/`r`; use `list_transformer_types` when voltages differ and
create a Transformer. After the additions call `diagnose_network` and report
any island by its bus names; ask whether each island is intended.

## Step: assets

For each generator, load or storage unit: `create_component` with the bus,
**p_nom** (or **p_set** for a load) and the cost fields the user gives. Use
`get_asset_costs` to show the current cost assumptions and `ask_user` when
the user has not given a cost: offer the catalogue default as the
recommended option. In Expert mode a write applies without a card; say in
one sentence what you are about to write before each call.

## Step: time

Call `list_snapshots`; if none, ask for the start, end and frequency and
call `set_snapshots`. For each load, offer `upload_load_profile` (the user's
CSV), `generate_exemplary_timeseries` (a shaped template) or a constant;
recommend the user's own data when they have it. Report what
`list_timeseries_profiles` shows at the end.

## Step: check

Call `validate_network`. Read every error to the user with the component
name and the fix; apply the fixes the user confirms. Then say the network is
ready to solve and offer the Run-a-study workflow and `run_simulation`.
