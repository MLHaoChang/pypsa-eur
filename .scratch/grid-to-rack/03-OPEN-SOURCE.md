# Open-source and public-reference assessment

Primary repository metadata, pinned README/licence files and selected source files were retrieved read-only on 21 October 2026. Exact commits/hashes and failed URLs are retained in [source-observations.json](source-observations.json). Selected source inspection is not successful installation, benchmark reproduction, independent validation or permission to deploy. No external examples, pretrained models, FMUs or equipment-control code were executed.

## Recommended evaluation order

| Candidate | Observed licence / source | What to reuse or evaluate | What is not established |
| --- | --- | --- | --- |
| [OpenG2G](https://github.com/gpu2grid/openg2g) | Apache 2.0; README, dependency file and six implementation files inspected at pinned commit | Optional workload/grid coordination adapter: independent-rate coordinator, offline workload state, synthetic training overlays and OpenDSS power-flow backend | Not a rack/UPS EMT model, calibrated campus training trace or proof of stability. Example inference data is gated; no access request/download. |
| [DPsim](https://github.com/sogno-platform/dpsim) | MPL2.0; pinned README/licence and two averaged inverter headers inspected | Bounded electrical-engine spike using EMT/dynamic-phasor and averaged converter building blocks | A suitable solver library does not automatically supply a validated grid-to-rack chain, physical storage coupling, protection or vendor control model. |
| [Three-mode UPS research implementation](https://github.com/mshams8/Three-Mode-Supervisory-Control-Framework-for-Centralized-UPS) | MIT; pinned README/licence inspected | A small Python averaged-dq/switching reference to assess as an independent prototype/benchmark candidate | Author's study, not independently reproduced here; do not import its assumed gains or claim hardware validation. |
| [Zeus](https://github.com/ml-energy/zeus) | Apache 2.0; source measurement paths inspected | Optional GPU energy/power acquisition and workload-side control/performance characterization | Measurement bandwidth, board/server/rack planes, device support and actual control delay require calibration. Never assume 1 ms polling creates 1 ms electrical telemetry. |
| [HELICS](https://github.com/GMLC-TDC/HELICS) | BSD3-Clause; pinned README/licence | Later cross-process coordination when actual concurrent solvers require it | No electrical equations, stable coupling or hardware real-time guarantee simply by selecting this framework. |
| [Vessim](https://github.com/dos-group/vessim) | MIT; current README, project dependencies and coupling documentation inspected | Optional software-in-the-loop energy/control experimentation | Not an EMT engine. Current Mosaik integration is optional; adding both orchestrators by default duplicates responsibilities. |
| [Modelica Buildings](https://simulationresearch.lbl.gov/modelica/) | Official open-source library documentation; exact component/tool terms still need deployment review | Later thermal/cooling/control refinement and explicit auxiliary loads | No automatic rack converter model, measured liquid-cooling performance or loss calibration. |
| [PyBaMM](https://github.com/pybamm-team/PyBaMM) | BSD3-Clause; pinned repository README/licence | Optional offline cell-aging/thermal study to build a bounded surrogate | Not a ready campus PCS/UPS electrical model; cell parameterization and pack/service-duty calibration required. |

These are evaluation candidates, not eight mandatory integrations. The initial product can use native trace/analytical screening with no new engine dependency. Recommend **OpenG2G as an optional workload/grid adapter and DPsim as the first detailed electrical spike**, with the MIT UPS study as a comparison candidate. Stop the spike if the required model/initialisation/current-limit tests cannot be demonstrated.

### OpenG2G: particularly relevant, with important scope limits

The inspected source includes `Coordinator`, `OfflineDatacenter`, `TrainingTraceParams` and an OpenDSS backend. The coordinator uses rational-rate scheduling; training source explicitly generates synthetic training-like power; the grid backend calls a power-flow solver. This is a close conceptual match for workload/grid coordination, not proof of transient rack dynamics. Keep it optional behind native trace/controller/grid interfaces.

Its README example uses a 0.1 s configuration; that is not a claim of measured millisecond fidelity. Example inference traces require access to ML.ENERGY Benchmark v 3. The dependency manifest includes additional data/measurement libraries, and example dependencies include an OpenAI SDK. No example was run and no paid/model/media calls were made. A first integration spike must support a local synthetic/authorized trace path and never initiate gated downloads or service calls automatically. Software/data licences are separate.

### DPsim: solver building blocks, not a finished campus model

The pinned source advertises EMT/dynamic-phasor support and includes averaged inverter classes with electrical/control parameters. Evaluate an explicit source/rectifier/DC-link/storage/inverter/load chain before committing to a large adapter. Document what is a physical power terminal versus an ideal source/control input; a component that treats its DC supply as ideal cannot prove DC-link ride-through.

Choose a supported release/platform and inspect transitive/native dependencies and applicable MPL obligations. The observed development HEAD is evidence, not a production version recommendation. Benchmark initialization, balanced/unbalanced disturbances, power balance, limit behavior and timestep convergence. Only use switching fidelity when the required metric needs it; otherwise validated average-value models are cheaper.

### Telemetry: use the actual API and hardware bandwidth

The inspected Zeus code tries instantaneous power where supported and contains fallback/period-inference logic. Its NVIDIA device path distinguishes explicit instantaneous and average NVML fields. NVIDIA's [official NVML query documentation](https://docs.nvidia.com/deploy/nvml-api/latest/api/group__nvmlDeviceQueries.html) says the common power-usage call averages over 1 s on Ampere except GA100 and on newer architectures, with exceptions/other fields documented. Therefore neither every call nor every device has the same effective bandwidth.

GPU values exclude many server/rack/UPS losses and loads. Store field/API/device, scope, effective sample/averaging window and synchronized clock. DCGM is another Apache 2.0 telemetry candidate, not a complete high-speed PoC measurement system. Do not issue hardware power-cap or live site commands without the separately approved experiment/control scope.

## Alternatives and existing engine decisions

| Candidate | Source finding | Recommendation |
| --- | --- | --- |
| [Dynawo](https://github.com/dynawo/dynawo) | MPL2.0; C++/Modelica simulation stack | Alternative detailed dynamics route if model/solver fit beats the first spike; evaluate concrete components, toolchain and models rather than promise switching EMT for every case. |
| [OpenIPSL](https://github.com/OpenIPSL/OpenIPSL) | BSD3-Clause model library | Alternative phasor stability/control models; needs a compatible Modelica runtime and model-fidelity assessment. Library licence does not settle runtime/tool licensing. |
| [PowerDynamics.jl](https://github.com/JuliaEnergy/PowerDynamics.jl) | Root licence says MIT by default, with identified MPL2.0 files adapted from Dynawo | Research alternative with additional Julia deployment/runtime. Do not reduce the mixed licence to a single permissive badge. |
| [ANDES](https://github.com/CURENT/andes) | Existing repo plan explicitly parks this path under Q4; GPL-related review remains | Preserve that decision. No assumption that an external process removes licence obligations. |
| [pandapower](https://github.com/e2nIEE/pandapower) | Already used by native campus studies | Reuse for appropriate load-flow/short-circuit/static studies; do not rebrand repeated load flow as converter EMT. |
| [DSS-Python](https://github.com/dss-extensions/DSS-Python) / OpenDSS | BSD3-Clause wrapper source observed; OpenG2G actually selects `opendssdirect.py` | Optional distribution adapter when it adds value beyond native pandapower. Review actual wrapper/engine dependencies; no automatic detailed DC-link model. |
| [OpenModelica](https://github.com/OpenModelica/OpenModelica) | OSMC runtime licensing file observed; not a simple GitHub SPDX badge | Possible Modelica/FMI execution route after full tool/model/dependency review; not part of the first implementation scope. |

An attempted Mosaik GitHub URL and an attempted FMI-wrapper repository URL returned 404. The [official Mosaik documentation](https://mosaik.readthedocs.io/en/latest/) was retrieved and current Vessim source links its actual API. Failed guessed URLs are not missing-project conclusions or successful integrations. No FMI wrapper was admitted from that failed lookup.

## Public models and engineering references

| Reference | Useful evidence | Boundary |
| --- | --- | --- |
| [Power Stabilization for AI Training Datacenters](https://arxiv.org/html/2508.14318v2) | Production-motivated synchronized load variation and cross-stack mitigation concepts | Reference for model categories, not a freely redistributable calibrated trace or universal controller setting. Paper version carries noncommercial/share-alike terms. |
| [PNNL data-centre EMT model library report](https://www.pnnl.gov/publications/electromagnetic-transient-modeling-large-data-centers-grid-level-studies) | UPS/PFC and centralized/distributed architecture examples, average/switching component distinctions | Actual report describes PSCAD implementation. Documentation availability is not a free open-source runtime or commercial reuse clearance for model files. |
| [Generic EMT data-centre model](https://github.com/claysiusd/generic-emt-modeling-of-data-center-load) | Public PSCAD component/model hierarchy, UPS/DC-DC/PFC/rack concepts | No root licence was retrieved via the licence endpoint. Do not copy or distribute as cleared OSS; PSCAD execution remains separate. |
| [TAMU/ERCOT PSCAD model record](https://zenodo.org/records/21004140) | Public conceptual double-conversion UPS model and documented intended use | Record explicitly CC BY-NC-SA4.0 and says it is not hardware validated. Not a commercially admitted code library or actual site digital twin. |
| [OCP Open Rack V3 BBU shelf specification](https://www.opencompute.org/documents/open-rack-v3-bbu-shelf-spec-rev1-1-pdf-1/) | Physical rack-level voltage/transient/interface specifications to inform parameter requirements | This is a specific architecture, not a universal 800 VDC/server requirement. OWFa specification terms and appendix exceptions apply; no blanket software/data licence. |
| [NERC large-load white paper](https://www.nerc.com/globalassets/who-we-are/standing-committees/rstc/whitepaper-characteristics-and-risks-of-emerging-large-loads.pdf) | Primary engineering motivation for explicit large-load models and study scope | Guidance/research context does not establish every jurisdiction's connection limits or a compliance certificate. |
| [LBL data-centre performance modeling](https://datacenters.lbl.gov/facility-performance-modeling-simulation) | Coupled thermal/electrical/cooling model context | Optional later thermal detail, not a measured campus trace or fast converter validation. |

All derived implementation defaults require their own documented basis and uncertainty. Do not copy vendor figures/specification limits into an unrelated topology, equate a published waveform with measured site data, or reuse noncommercial assets in the common commercial library without the required rights.
