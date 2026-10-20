---
id: renewable-profile
title: Generate a renewable profile
intent: Create a wind or solar capacity-factor time series for a site or an existing generator — from coordinates the network already carries — and apply it to the generator.
when: [guided, expert]
order: 35
opening_request: I want to generate a wind or solar profile for a site or one of my generators. Ask me the key questions and set it up with me.
steps:
  - id: scope
    title: Pick the sites
    done_when: The sites are known — either generator names on the open network or explicit coordinates.
  - id: technology
    title: Wind or solar
    done_when: The technology is chosen and matches the target assets' carriers.
  - id: parameters
    title: Turbine or panel parameters
    done_when: A preset is chosen; any overrides the user asked for are named.
  - id: weather
    title: Weather source and year
    done_when: The user has picked synthetic or ERA5 and a weather year; ERA5 credentials are confirmed present.
  - id: resolution
    title: Time resolution
    done_when: The user has picked hourly, daily or weekly and knows the trade-off.
  - id: preview
    title: Preview the estimate
    done_when: The user has seen the synthetic estimate and confirms the shape looks sane.
  - id: generate
    title: Queue the generation
    done_when: The job completed and the profile is listed as pending.
  - id: apply
    title: Apply to the generator
    done_when: The profile is written onto the generator and the summary has been reported.
---

## Step: scope

Ask with `ask_user` which sites the profile is for. Two ways to name them:

- existing generators — `list_components` with component class "generators"
  shows what the open network holds; the tool argument "from_generators"
  then takes the names directly and each generator's bus coordinates seed
  its site. The point id IS the generator name, so the apply step needs no
  mapping. This is the common case: "the wind park I already added".
- new sites — the user gives coordinates (name, longitude, latitude in
  WGS-84 degrees) and you pass them as "points".

Never invent coordinates. If the user names a generator, resolve it through
"from_generators"; if the network holds no such generator the tool refuses
with the names it could not find — list the candidates and ask again.
A generator whose bus has no coordinates (unset, or the 0/0 default) is
refused with error_kind "missing_coordinates" listing those generators:
name them and offer both ways out — set the bus coordinates (an edit,
confirmed like any other) or upload a profile for them instead.

## Step: technology

Wind or solar — ask if the user has not said. The technology must match the
carrier of every generator named in the scope step (a wind profile onto a
gas plant is refused at apply time; catch it here instead). For new sites,
ask which technology the site is for.

## Step: parameters

`list_profile_presets` for the chosen technology shows the parameter
bundles; `get_profile_parameters` shows one preset's resolved knobs —
turbine model and hub height for wind, panel and orientation for solar.
Propose the default preset unless the user named hardware ("a Vestas V90",
"single-axis tracking"). If the user wants to tune a knob, pass a "params"
override dict — get the preset's resolved params first so an override
extends the bundle instead of replacing it blindly. Read the values back
to the user in plain words before moving on.

## Step: weather

Ask which weather the profile should follow:

- synthetic — built in, works offline, deterministic; right for testing a
  workflow or a first look. Say plainly that it is NOT measured weather.
- ERA5 via the atlite source — real reanalysis weather for the sites'
  bounding box. It downloads from the Copernicus Climate Data Store and
  needs a free CDS API key; the download takes minutes and is cached.

Also ask for the weather year (the calendar year whose weather shapes the
profile; default is last year). If the user picks ERA5, just try it — a
missing key is a typed refusal, not a crash. When that happens, coach the
setup: create a free account at https://cds.climate.copernicus.eu/, accept
the dataset licence, then set CDSAPI_URL=https://cds.climate.copernicus.eu/api
and CDSAPI_KEY to the token in the backend's environment (or write url and
key lines to ~/.cdsapirc), and restart the backend. The old UID:KEY key
format is rejected by CDS — a new-style token is required.

## Step: resolution

Ask with `ask_user`: hourly, daily or weekly.

- hourly — the native axis; the full year of hours. Pick this when the
  profile feeds dispatch or power-flow studies (the usual case).
- daily — day means, 365/366 values; lighter artifacts for screening.
- weekly — week means, about 52 values; for long-run seasonal scans.
  Weekly profiles apply only onto a network of the SAME calendar year and
  cannot tile multi-investment-period snapshots — say so when relevant.

Coarser resolutions are aggregated at generation and upsampled back onto
the network's hourly snapshots at apply, so the choice never blocks the
apply step on an hourly network. There is no annual resolution — an annual
mean is a scalar, not a profile; if the user asks for one, offer the
estimate's mean instead.

## Step: preview

Call `preview_renewable_profile` with the agreed scope, technology, preset,
overrides, weather year and resolution. It answers a synthetic,
labelled estimate in seconds and never enqueues anything. Report the mean
capacity factor in percent and the rough shape (nightly zeros for solar,
stormy spells for wind) and ask whether to go ahead. If the shape surprises
the user, revisit the parameters or weather-year step — previewing again is
free.

## Step: generate

Call `generate_renewable_profile` with the same arguments. It answers a
queued job — generation runs in the one solve queue, behind any running
solve, and does NOT complete inline. Poll `solve_queue_list`, and when the
job is completed call `list_pending_profiles` and confirm the artifact is
there (technology, weather year, resolution, points). A failed job carries
its refusal — read it to the user and fix the cause (an ERA5 job without
CDS credentials fails exactly the way the weather step describes).

## Step: apply

One pending profile feeds one generator per call: `apply_generated_profile`
with the job id and the component name. The write is undoable and goes
through a confirmation card — tell the user that before calling. The point
feeding the component defaults to the component's own name, which is why
the scope step named points after generators. Report the summary: hours
written, weather year, mean and peak capacity factor, whether Feb 29 was
dropped, and any bus-moved warning verbatim — a moved bus means the weather
was generated for the old location. Offer to regenerate with different
parameters when the user is not happy: regeneration overwrites the same
slot, changelog-stamped.
