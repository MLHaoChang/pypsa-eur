# Model refresh and voice permission — design

**Status:** approved in brainstorming, 2026-08-05 · **Sequence:** step (a) of (a)→(b)→(c)

Two unrelated small changes, specced together because both are self-contained,
both land on `master` without depending on anything else, and both are
prerequisites for the assistant redesign being worth using.

## Problem 1 — the model list is a generation stale

`services/chat_service.py:116-118`:

```python
DEFAULT_MODEL: str = "claude-sonnet-4-6"   # latest Sonnet
OPUS_MODEL: str = "claude-opus-4-8"        # latest Opus
ALLOWED_MODELS: frozenset[str] = frozenset([DEFAULT_MODEL, OPUS_MODEL])
```

Both comments assert currency, and both are wrong — the current family is
Claude 5. A comment that claims to be up to date is how this went unnoticed:
it reads as verified rather than as a guess someone made once.

The frontend mirrors the list in three places: the `ChatModel` union
(`api/chat.ts:24`), the pricing table and `deriveCostEur`
(`store/chatStore.ts:64-76`), and the picker in `ChatPanel.tsx`.

### Decision — offer Sonnet 5 and Opus 5

A like-for-like replacement of the existing two tiers. `claude-sonnet-5` is
the default; `claude-opus-5` is the escalation. Haiku and Fable were
considered and declined: more picker surface for a tool whose users mostly
want a sensible default.

### Decision — delete the currency estimate, show token counts

The cost meter renders EUR from `PRICING_USD_PER_MTOK` and a hardcoded
`USD_PER_EUR = 1.08`. We do not have verified per-token pricing for the
Claude 5 models, and the alternatives were to invent numbers or to display
knowingly wrong ones.

Instead the meter shows what the store already accumulates and what cannot go
stale: **input, output and cache-read token counts**. `PRICING_USD_PER_MTOK`,
`USD_PER_EUR` and `deriveCostEur` are deleted.

This removes a feature. That is the intended trade: an exact token count is
worth more than a currency figure that is wrong in a way no test can catch,
and it removes a second stale-by-construction table at the same time. It also
decouples the model list from a pricing table, so the next model change is a
two-line edit rather than a five-file one.

### Scope

| File | Change |
|---|---|
| `backend/services/chat_service.py` | `DEFAULT_MODEL`, `OPUS_MODEL`; delete the "latest X" comments |
| `frontend/src/api/chat.ts` | `ChatModel` union |
| `frontend/src/store/chatStore.ts` | delete pricing table, `USD_PER_EUR`, `deriveCostEur`; add a token-count selector |
| `frontend/src/components/ChatPanel.tsx` | picker labels; cost meter → usage readout |
| `pypsa-gui/CHATBOT.md` | model names |

`ALLOWED_MODELS` derives from the two constants and needs no separate edit.

### To determine during implementation, not assumed now

* Whether anything outside `ChatPanel.tsx` imports `deriveCostEur`. If it
  does, those call sites are in scope.
* Whether any test pins the literal model strings. `test_chat_e2e.py` and
  `test_chat_sse.py` both reference models; each hit must be read rather than
  find-and-replaced, because a test asserting "an unknown model is rejected"
  needs a string that stays unknown.

### Verification

* Backend pytest exits 0.
* A request naming a model outside `ALLOWED_MODELS` is still rejected — the
  allow-list must not become permissive as a side effect.
* The usage readout asserts real accumulated counts from a scripted turn, not
  merely that it renders. A test that only checks for a rendered number would
  pass against a component that always shows zero.

## Problem 2 — the microphone button lies in the packaged app

### What was measured

Three probes against a real cocoa WKWebView (`smoke/` will carry the harness;
this is the same treatment `audit_downloads.py` gives downloads, which voice
never received). The first probe loaded inline HTML and reported
`getUserMedia: false`; that was an artifact — inline content is not a secure
context. Re-run over loopback HTTP, matching what the app serves:

| Probe | Result |
|---|---|
| `secureContext` (loopback origin) | `true` |
| `window.SpeechRecognition` | `undefined` |
| `window.webkitSpeechRecognition` | **`function`** |
| `window.speechSynthesis` / voices | present, **219 voices** |
| `navigator.mediaDevices` | absent |
| `new webkitSpeechRecognition().start()` | ran, then fired **`error: not-allowed`** |

### What that means

`utils/speechToText.ts:59` resolves
`win.SpeechRecognition ?? win.webkitSpeechRecognition ?? null` and the UI
treats a non-null result as "voice is available". In WKWebView the constructor
**is** present, so the packaged app renders an **enabled** mic button that
fails the moment it is clicked.

The feature-detect tests presence. What varies is permission. That is the bug.

`.start()` returning `not-allowed` rather than throwing tells us the API is
implemented and blocked, not absent — and `pypsa-gui.spec`'s `info_plist`
(`:275-288`) declares neither `NSMicrophoneUsageDescription` nor
`NSSpeechRecognitionUsageDescription`, which is exactly the condition that
produces `not-allowed`.

`navigator.mediaDevices` being absent is not the blocker: Safari's
SpeechRecognition does not route through `getUserMedia`.

### Decisions

**The button reflects usability, not presence.** Three states, not two:
available, unavailable-because-unsupported, and unavailable-because-denied.
The denied state carries copy naming the cause and the remedy; today a denial
is indistinguishable from a missing API.

**Add both usage strings to the bundle.** `NSMicrophoneUsageDescription` and
`NSSpeechRecognitionUsageDescription` in `pypsa-gui.spec`'s `info_plist`.

**Voice output is out of scope here.** Synthesis works today with 219 voices
and needs no permission, but spoken replies are an assistant-behaviour
decision that belongs to step (c), not a bug fix.

### The honest limit on verification

Adding the plist keys is a **diagnosis, not a proven fix**. `not-allowed` in a
bare Python process with no bundle identity is what you would predict with or
without the keys. Confirming it requires launching a built bundle that carries
them and observing `.start()` reach `onstart`.

The spec therefore requires a **manual verification step against a real
build** before this is called done, and forbids claiming the plist change
fixed anything on the strength of unit tests. If the built app still reports
`not-allowed`, the three-state UI is still correct and still an improvement —
the button would then honestly say voice is unavailable instead of pretending.

### Verification

* Unit tests for the three-state resolver, including that a present
  constructor with a denied permission yields *unavailable*, which is the
  case that is wrong today.
* The WKWebView probe lands in `backend/smoke/` so the measurement is
  repeatable rather than a one-off in a transcript.
* Manual: build, launch, click the mic, record whether `onstart` fires.

## Out of scope

Wake-word activation. It needs continuous recognition, which WebKit handles
poorly, or a bundled on-device engine — its own project, and explicitly
deferred during brainstorming.
