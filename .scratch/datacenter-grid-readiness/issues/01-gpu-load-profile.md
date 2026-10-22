# GPU / AI-training load profile in the data-centre template

Status: done (#421, 6196cb93)
Type: task
Blocked by: none
Gated: yes (adequacy reads the profile)
Model tier: opus
Size: M

Read [the spec](../spec.md) first. Owner decision D1: this lives in `DataCentreLoadSpec`.

## What to build

- Extend `DataCentreLoadSpec` with a parametrised AI-workload model: job-synchronised ramps, checkpoint dips, idle-to-full swing, and an oscillation band (amplitude, period) as parameters, not simulated sub-hourly.
- Output 1: an hourly `p_set` profile for PyPSA (energy, peaks, PUE incl. temperature slope).
- Output 2: a **dynamic envelope** record: max ΔP/Δt, swing in MW, oscillation band. 02 and gridspine read it. It is not used in the LP.
- A roadmap knob: IT MW and rack power density per COD phase (`DcPhase`), so later phases can carry denser racks.
- Defaults are labelled "assumed", with sources.

## Done when

- The DC template solves with the new profile, and existing DC seeds keep their numbers unless the user opts in.
- The envelope is stored per project, and the copilot can read it.
