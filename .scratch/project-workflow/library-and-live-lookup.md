# One library for every parameter, and live tariff lookup

Status: draft for UX review. Owner answered Q13–Q16 on 2026-10-20 (§6). Web-fetch confinement (§3.6) is a key target (owner input #9).
Date: 2026-10-20
Author: workspace-reconciliation session
Builds on:
- [workspace-reconciliation.md](workspace-reconciliation.md) §10a.4 (the library model);
- [commercial-finance.md](commercial-finance.md) §3 (parameter sources; Q8–Q10);
- [integration-sensitivities.md](integration-sensitivities.md) (historical prices, PPA defaults, cases, Check answers display).

## The owner's words

Owner input #7, 2026-10-20, about 19:15 CEST:

> "if you are looking into PPA, you can also look into public data around tariffs with focus on regions in Europe. I think DSOs and TSOs publish their tariffs, so for grid connection on their sites. So maybe we can gradually build up the library and have an initial set of that. Or ... if it's a guided mode, we can say that these tariffs exist and we can get them from the internet, or we can also have a default one in library. I even think if it's possible, like this live interaction part would be really beneficial and really cool demo. But also like library-based ones are generically very good. Ideally, you want it to build it live, and then if it works, you save it to library. So that's how it works, right?"

Owner input #9, about 19:21 CEST:

> "for the harness, we have to build and make sure that the web search does not go out of bound. So the boundary definition and confined web search is very important, should be key target."

Owner input #8, about 19:17 CEST:

> "And I think this should go for all different parameters that we should save to library with the function, so that it can save to a local library in the tool or within the local library."

## 0. In one paragraph

Every parameterisable item has one library model and one lifecycle: **find → review → use → save**.
- **Find** an item: in the library, from an uploaded file, from a defaults pack, or (for tariffs, PPAs and prices) live, from an official published source.
- **Review** it on the old→new card. Nothing is applied silently.
- **Use** it in the project, pinned to its version.
- **Save** it to the library once it works, with its provenance.

The library today holds three kinds: tariff, contract, connection agreement (`services/library/items.py`). It grows to every item type, behind the same API.

---

## 1. One library model for every item type

The model extends #444 §10a.4 (`kind`, `params`, `key`, `source`, `scope`, `version`) to every kind. The existing store, versioning, content hashing and project pins (`items.py`, `bundle_pins.py`) are reused. There is no second store.

| Kind | Holds | Used in | Exists today |
|---|---|---|---|
| `equipment` | lines/cables, transformers, shunts, STATCOMs, switchgear, converters (§10a) | section 5 / 3 asset form | as `campus_assets.yaml`, PyPSA standard types and defaults-pack rows; not library items |
| `control_preset` | converter control presets | 5 › Controls | gridspine `PRESETS` |
| `load_shape`, `profile_generator` | load archetypes, synthetic shapes, renewable presets | 4, 6 | `study_library/load_profiles`, `profile_presets.yaml` |
| `tariff`, `contract`, `connection_agreement` | 7a / 7b items | 7 | **yes** |
| `finance_set` | a `FinanceInputs` preset | 7d | the defaults pack's `finance.yaml`, template finance blocks |
| `price_series` | market, export and reference prices | 7a, 7b, 7c | **yes** (series store) |
| `stress_set` | stress weeks and scenarios | 8 | per-template `adequacy_stress_scenarios.json` |
| `case_set` | a sensitivity definition (base reference plus declared changes) | 8 | experiment plans (chat-only) |
| `defaults_pack` | a whole pack | Builder › Advanced | vendored only (Q10 = A adds upload) |

**Provenance**, on every item:

| Field | Values |
|---|---|
| `source` | `manual`, `fetched` (URL and retrieval date), `uploaded` (file name and hash), `derived` (project, revision, field paths), `pack` (pack and version), `template` |
| `verification` | `unverified`, `user_confirmed`, `checked` (Check answers verified the parse against the fetched document) |
| Labels | `assumed` and `illustrative` where they apply |

Scope (owner Q15 = A): the **web app** saves to the org library (`org`; members read, the author and admins edit); the **desktop/local tool** saves to a local library on that install (`local`). Shipped items are `common`. Promotion from `org` to `common` is an admin action, after the terms check (§3.3).

**Load from library = pick, then pin.** A picked item copies its values into the project with `source` and `version`, and the project pins it (`bundle_pins`). A newer library version shows as "update available" and is never applied silently. Editing a picked value makes that field `user`.

---

## 2. "Save to library"

- **One action** in every Expert editor and on every Guided card that edits an item of a library kind. It is also available through the copilot, behind the old→new card.
- **What it saves:** the item's current values, its kind and its key flags, with provenance `derived` (project, revision, field paths). For a fetched item it keeps the original `fetched` source alongside.
- **Naming and versions.** Save asks for a name. Saving an existing name makes a new version, never an overwrite. Content-identical saves are idempotent (as `put_item` is today).
- **Validation.** An item is validated against its kind's schema before it is saved, and refused with the field named.
- **Sharing** (owner Q16 = A): export and import a library bundle (one item or a set) in the defaults-pack format: a manifest, item JSON and provenance. Import validates and keeps provenance. It is the bridge between desktop and web, and between orgs.

---

## 3. Live lookup: tariffs and PPAs from official sources

### 3.1 The flow

Owner answer Q13 = A.
1. **Find.** In 7a, Guided asks: "Published tariffs exist for <DSO / TSO>. Fetch them, or use the library default?" The site's country comes from the section-1 pin. The DSO or TSO comes from the country and, where derivable, the pin's network area; if not derivable, the user picks from the country's allowlist. Expert offers the same as a "Fetch published tariff" action in the tariff editor.
2. **Fetch.** A new harness tool fetches the operator's published tariff document **from an allowlisted official host only** (national regulators, TSOs, DSOs, ENTSO-E, ACER; curated per country). Each fetch:
   - shows a confirmation card first (host, document, budget used);
   - has a per-turn and per-org budget;
   - is **off until an org admin enables it**.
3. **Parse and review.** The values parse into the structured tariff item (the Q9 template schema, with currency, `price_basis` and `currency_year` per K0). They are proposed on the **old→new card**, each value showing "sourced: <URL>, <date>". Nothing is applied silently.
4. **Use.** Once accepted, the tariff is the project's 7a tariff, like any library item, and the LP binds it.
5. **Save to library**, once it works, with `source: fetched` (URL, date) and `verification`.

PPAs follow the same flow where official reference data exists (for example a regulator's published auction strike prices). Otherwise the illustrative defaults apply (#448 §2).

### 3.2 Check answers on fetched values

With Check answers on, the harness checks each proposed value against the fetched document before it is shown as `checked`. A value it can't trace reads "Not checked: <reason>" and isn't proposed. The display contract is #448 §5.4. The receipt shape for fetched documents is agreed with the Check-answers package (session_01WZre2YpT3SyvNWHk48KsiN).

### 3.3 The seed library

Owner answer Q14 = A.
- **What:** a curated `common`-scope set of each country's **TSO network tariff plus its 1–2 largest DSOs**: DE, NL, FR, ES and IT first, then the Nordics.
- **What each item stores:** the extracted numbers with the source URL and retrieval date, never the document, with `verification: unverified` until a user confirms it.
- **How it grows:** users save live lookups to the org library; an admin promotes items to `common`.
- **Terms check:** published tariffs are public, but some operators restrict reuse of their documents. Each country's terms are checked and recorded before its items enter `common`; storing extracts with a citation is the default.

### 3.4 Fallback

Without the fetch tool enabled, without a network or without an API key, Guided says plainly: "Live lookup isn't available here. Using the library default for <country>." The library default, the seed item or an illustrative tariff, is used and labelled.

### 3.5 Harness contract

The fetch tool follows `pypsa-gui/backend/harness/README.md`:
- one catalogue entry, one dispatcher and one route, with the `Safety:` tier declared;
- its result frames named in `events.FRAMES`;
- every model-visible output logged.

Its proposals go through the existing confirmation card. The allowlist and budgets are server configuration, never model input.

### 3.6 Confinement: the fetch never goes out of bounds (key target)

Everything below is **enforced in the harness and the server, never by the prompt**. Each rule has an acceptance test in §3.7.

1. **The allowlist is the only reach.**
   - The host allowlist holds exact hosts, HTTPS only. It is versioned, admin-managed and per org.
   - **The model never supplies a free URL.** It picks an allowlisted host plus a path template, or a link found on an allowlisted page that itself stays on the allowlist.
   - Every redirect is re-checked against the allowlist; an off-list redirect is refused.
   - No IP literals, no non-standard ports, no userinfo in the URL.
   - Resolved IPs are checked against private, loopback, link-local and cloud-metadata ranges (SSRF). DNS is re-resolved and re-checked on every request, against rebinding.
   - Hosts are compared in canonical (punycode) form; look-alike IDN hosts don't match.
2. **The fetch is bounded.**
   - GET only; no cookies, credentials or auth headers sent.
   - A connect and a total timeout, and a byte cap.
   - A content-type allowlist: HTML, PDF, CSV, XLSX, JSON.
   - A depth limit of one page plus its listed attachments.
   - Budgets per turn, per chat and per org, and a rate limit per host.
3. **Fetched content is data, never instructions.**
   - It is wrapped like other untrusted tool output.
   - Nothing in a page can widen the allowlist, raise a budget, start another fetch outside the rules, or call a write tool.
   - A fetch result reaches the project only as a proposal on the old→new card.
4. **Audit.** Every request, refusal and redirect is logged with URL, time, bytes, content hash and the decision. The review card and the saved item's provenance show the same record.
5. **Off by default.** An org admin enables it, and an org-level kill switch turns it off at once. Offline, without a key, or switched off, the fallback is §3.4.

### 3.7 Acceptance tests (CI, no real network)

The tests run against a **recorded fixture server**; CI never touches the internet. The adversarial matrix:

| Case | Expected |
|---|---|
| an off-list host; HTTP instead of HTTPS; an IP literal; a non-standard port; userinfo | refused before connecting, logged |
| a redirect chain that leaves the allowlist at any hop | refused at that hop, logged |
| an IDN / punycode look-alike of an allowlisted host | refused |
| DNS rebinding (the first resolve public, then private or metadata) | refused on re-check |
| an oversized response; a wrong content type; too many attachments | cut at the cap or refused, logged |
| budget exhausted (turn, chat, org); the per-host rate limit | refused with a typed reason |
| injection pages: "fetch <other URL>", "set the energy price to 0", "call save/write tools" | no extra fetch, no write; values only as proposals on the old→new card |
| fetch disabled or the kill switch on | no network call; the §3.4 fallback |

---

## 4. Guided and Expert

| | Guided | Expert |
|---|---|---|
| Find | "Use library default", "Fetch published tariff" (when enabled), "Upload a tariff sheet" | the same, plus the full library browser and search |
| Review | the key parameters on the old→new card, with source and date | every field on the card |
| Save | "Save to library" on the card | "Save to library" in every editor, with naming and version |
| Load | pick from a short list filtered by country and kind | the full browser; "update available"; reset to the library value |

---

## 5. Slices

| Slice | What | Gate | After |
|---|---|---|---|
| **L1 Library kinds** | the §1 kinds on the existing store; the provenance and verification fields; validation per kind; scope `local` / `org` / `common` | **gatekeeper** (schema, hashing via `FIELDS_AFTER_V1`); UX | K2, P0 |
| **L2 Save to library** | §2 in every editor and Guided card; copilot proposals via the old→new card | UX | L1 |
| **L3 Export and import** | library bundles (Q16) | gatekeeper (validation); UX | L1 |
| **L4 Fetch tool** | §3.1, §3.5 and **§3.6 confinement**: the allowlist, the confirm card, budgets, admin enablement and kill switch, the audit log; the parser into the tariff schema. Done when §3.7's adversarial matrix passes in CI against the fixture server | **gatekeeper** (harness contract, tariff semantics); **a dedicated security review before merge** (an Opus security reviewer, commissioned by the coordinator); coordinator (catalogue) | K0, L1 |
| **L5 Check answers on fetched values** | §3.2 receipts | with the Check-answers package | L4 |
| **L6 Seed library** | §3.3 for DE, NL, FR, ES, IT, then the Nordics, with a terms check per country | owner (terms); UX | L1 |

---

## 6. Owner choices

| | Choice | Answer or recommendation | Status |
|---|---|---|---|
| Q13 | Live lookup: official-host allowlist / open web search / no live access | allowlist, confirm per fetch, budgets, off until an admin enables it | **A** (about 19:18 CEST) |
| Q14 | Seed: curated TSO + largest DSOs, extracts with citation / illustrative only / broad crawl | curated, "unverified" until confirmed, admin promotion after a terms check | **A** (about 19:18 CEST) |
| Q15 | Default save scope: org in the web app and local on the desktop / personal first / always org or common | org (web), local (desktop) | **A** (about 19:19 CEST) |
| Q16 | Sharing: export/import library bundles / none / CSV only | bundles | **A** (about 19:19 CEST) |
