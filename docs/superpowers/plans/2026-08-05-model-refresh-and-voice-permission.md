# Model Refresh and Voice Permission Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the chat model list to Claude 5, replace the stale-by-construction EUR cost meter with exact token counts, and stop the packaged app from rendering a microphone button that always fails.

**Architecture:** Four independent tasks. Two are renames plus a deletion; one adds a permission dimension to a capability check that currently tests only for API presence; one adds bundle permissions and a repeatable WKWebView probe. Nothing depends on anything else, so a reviewer can reject any one without blocking the rest.

**Tech Stack:** FastAPI + PyPSA backend (pixi, Python 3.13), React + TypeScript frontend (vitest 4.1.10, jsdom), PyInstaller for the macOS bundle.

## Global Constraints

- Models offered: **`claude-sonnet-5`** (default) and **`claude-opus-5`** (escalation). No Haiku, no Fable.
- The EUR cost estimate is **deleted, not updated**. No invented pricing numbers anywhere.
- `ALLOWED_MODELS` must stay a **deny-by-default** allow-list. A change that makes it permissive is a defect even if every test passes.
- Adding the Info.plist keys is a **diagnosis, not a proven fix**. Never claim it fixed voice input on the strength of unit tests.
- Backend tests: `pixi run -e test python -m pytest` from `pypsa-gui/backend`. `ruff` lives in the **dev** feature: `pixi run -e dev ruff check <file>`.
- Frontend: `npx` is not on PATH. Use `pixi run bash -c 'cd pypsa-gui/frontend && <cmd>'`.
- Run `git diff --cached --name-only` immediately before every commit and abort if it lists anything you did not personally add.
- Never use `git stash` — the stash ref is shared between worktrees. Revert with `git checkout <path>`.
- Never use `pkill` or any pattern-based process kill.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `backend/services/chat_service.py:116-118` | the two model constants and the allow-list derived from them | 1 |
| `backend/services/chat_tools.py:2346` | vision sub-call model — must become a reference, not a literal | 1 |
| `backend/tests/e2e_chat_service.sh:217` | shell harness pinning a model in a request body | 1 |
| `backend/tests/test_chat_models.py` (new) | pins the allow-list and the vision reference | 1 |
| `frontend/src/api/chat.ts:24` | the `ChatModel` union | 2 |
| `frontend/src/store/chatStore.ts:64-76` | delete pricing table, `USD_PER_EUR`, `deriveCostEur` | 2 |
| `frontend/src/components/ChatPanel.tsx:40,278-290` | `CostMeter` → `UsageMeter` | 2 |
| `frontend/src/utils/speechToText.ts:94-111` | classify a permission error | 3 |
| `frontend/src/hooks/useSpeechToText.ts:47` | the bug: `supported = Ctor != null` | 3 |
| `frontend/src/components/ChatPanel.tsx:1854-1860` | mic button reflects usability, not presence | 3 |
| `pypsa-gui.spec:275-288` | `info_plist` microphone + speech usage strings | 4 |
| `backend/smoke/probe_webview_speech.py` (new) | repeatable WKWebView capability measurement | 4 |

---

## Task 1: Backend model constants and the vision sub-call

**Files:**
- Modify: `pypsa-gui/backend/services/chat_service.py:116-118`
- Modify: `pypsa-gui/backend/services/chat_tools.py:2346`
- Modify: `pypsa-gui/backend/tests/e2e_chat_service.sh:217`
- Modify: `pypsa-gui/CHATBOT.md`
- Test: `pypsa-gui/backend/tests/test_chat_models.py` (create)

**Interfaces:**
- Produces: `chat_service.DEFAULT_MODEL == "claude-sonnet-5"`, `chat_service.OPUS_MODEL == "claude-opus-5"`, `chat_service.ALLOWED_MODELS` — Task 2 mirrors these strings in the TypeScript union.

**Context the implementer needs:** `chat_tools.py:2346` currently reads `model="claude-sonnet-4-6"` — a **literal**, for the vision sub-call. It does not follow `DEFAULT_MODEL`, so a model bump silently strands the vision path on the old generation. Making it a reference is the point of this task, not a cosmetic tidy.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/backend/tests/test_chat_models.py`:

```python
"""
The model list, and the one place that historically did not follow it.

`chat_tools.py` hardcoded `model="claude-sonnet-4-6"` for the vision sub-call
rather than referencing `DEFAULT_MODEL`, so it did not move when the constants
did. The literal-scan test below is the one that catches that class of defect;
asserting the constants alone would pass against a stranded vision path.
"""
from __future__ import annotations

import inspect
import re

from services import chat_service, chat_tools


def test_models_are_the_current_generation():
    assert chat_service.DEFAULT_MODEL == "claude-sonnet-5"
    assert chat_service.OPUS_MODEL == "claude-opus-5"


def test_allowed_models_is_exactly_those_two():
    assert chat_service.ALLOWED_MODELS == frozenset(
        {"claude-sonnet-5", "claude-opus-5"}
    )


def test_an_unknown_model_is_still_refused():
    """Deny-by-default. A permissive allow-list passes every other test here."""
    assert "claude-sonnet-4-6" not in chat_service.ALLOWED_MODELS
    assert "gpt-4" not in chat_service.ALLOWED_MODELS


def test_no_module_hardcodes_a_model_literal():
    """
    The vision sub-call is the known offender. Scanning the source is
    deliberate: a test that called the vision path would need an API key, and
    the defect is visible statically.
    """
    offenders = []
    for module in (chat_tools, chat_service):
        source = inspect.getsource(module)
        for lineno, line in enumerate(source.splitlines(), start=1):
            if re.search(r'model\s*=\s*["\']claude-', line):
                offenders.append(f"{module.__name__}:{lineno}: {line.strip()}")
    assert offenders == [], (
        "model= must reference DEFAULT_MODEL/OPUS_MODEL, not a literal:\n"
        + "\n".join(offenders)
    )
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd pypsa-gui/backend && pixi run -e test python -m pytest tests/test_chat_models.py -v`

Expected: `test_models_are_the_current_generation` fails on `claude-sonnet-4-6`, and `test_no_module_hardcodes_a_model_literal` fails naming `chat_tools:2346`. If the literal-scan test passes at this point, stop — it is not looking where you think.

- [ ] **Step 3: Update the constants**

In `pypsa-gui/backend/services/chat_service.py`, replace lines 116-118:

```python
# No "latest" comment here on purpose. The previous pair carried
# `# latest Sonnet` / `# latest Opus`, which read as verified and was wrong
# for a full generation — a comment that asserts currency is how this went
# unnoticed. The model list is checked by tests/test_chat_models.py instead.
DEFAULT_MODEL: str = "claude-sonnet-5"
OPUS_MODEL: str = "claude-opus-5"
ALLOWED_MODELS: frozenset[str] = frozenset([DEFAULT_MODEL, OPUS_MODEL])
```

- [ ] **Step 4: Make the vision sub-call follow the constant**

In `pypsa-gui/backend/services/chat_tools.py`, at the vision sub-call (line ~2346), replace the literal with a reference. The import must be function-local to match the surrounding file's convention:

```python
            from services.chat_service import DEFAULT_MODEL  # noqa: PLC0415

            ...
            model=DEFAULT_MODEL,
```

- [ ] **Step 5: Update the shell harness**

In `pypsa-gui/backend/tests/e2e_chat_service.sh:217`, change `"model":"claude-sonnet-4-6"` to `"model":"claude-sonnet-5"`.

- [ ] **Step 6: Run the tests**

Run: `cd pypsa-gui/backend && pixi run -e test python -m pytest tests/test_chat_models.py -v`
Expected: 4 passed.

Then the full suite: `pixi run -e test python -m pytest -q`
Expected: exit 0.

- [ ] **Step 7: Update CHATBOT.md**

Replace every `claude-sonnet-4-6` / `claude-opus-4-8` occurrence with the new names. Do not add a "latest" claim.

- [ ] **Step 8: Lint and commit**

```bash
pixi run -e dev ruff check ./pypsa-gui/backend/services/chat_service.py ./pypsa-gui/backend/services/chat_tools.py ./pypsa-gui/backend/tests/test_chat_models.py
git add pypsa-gui/backend/services/chat_service.py pypsa-gui/backend/services/chat_tools.py pypsa-gui/backend/tests/e2e_chat_service.sh pypsa-gui/backend/tests/test_chat_models.py pypsa-gui/CHATBOT.md
git diff --cached --name-only
git commit -m "feat(gui): move the chat models to Claude 5, and make the vision sub-call follow the constant"
```

---

## Task 2: Frontend model union, and delete the currency estimate

**Files:**
- Modify: `pypsa-gui/frontend/src/api/chat.ts:24`
- Modify: `pypsa-gui/frontend/src/store/chatStore.ts:64-76`
- Modify: `pypsa-gui/frontend/src/components/ChatPanel.tsx:40,278-290`
- Test: `pypsa-gui/frontend/src/components/ChatPanel.usage.test.tsx` (create)

**Interfaces:**
- Consumes: the model strings from Task 1.
- Produces: `ChatModel = 'claude-sonnet-5' | 'claude-opus-5'`. `deriveCostEur`, `PRICING_USD_PER_MTOK` and `USD_PER_EUR` **cease to exist** — any later task importing them is wrong.

**Context:** `deriveCostEur` has exactly one consumer: `ChatPanel.tsx:40` (import) and `:281` (call). `ChatUsageAcc` already carries `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_create_tokens` and stays.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/frontend/src/components/ChatPanel.usage.test.tsx`:

```tsx
// The meter shows exact token counts, not a currency estimate.
//
// The EUR figure was derived from a hardcoded price table plus
// USD_PER_EUR = 1.08. We have no verified pricing for the Claude 5 models,
// and the alternatives were inventing numbers or displaying known-wrong ones.
// Token counts are already accumulated and cannot go stale.
//
// This asserts REAL accumulated values, not merely that a number renders — a
// meter hardwired to zero would satisfy the weaker check.
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { useChatStore } from '../store/chatStore'
import ChatPanel from './ChatPanel'

vi.mock('../api/chat', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/chat')>()
  return {
    ...actual,
    createChatStream: vi.fn(),
    getChatHistory: vi.fn().mockResolvedValue({ turns: [], last_session_id: null, bound_project: null }),
    postChatAbort: vi.fn(),
    postChatConfirm: vi.fn(),
    getApiKeySettings: vi.fn().mockResolvedValue({
      configured: true, source: 'settings', hint: '…wxyz',
      overridden_by_environment: false, storage_path: '/tmp/user.env',
    }),
    putApiKeySettings: vi.fn(),
    deleteApiKeySettings: vi.fn(),
  }
})

vi.mock('../api/uploads', () => ({
  deleteUpload: vi.fn(), getUploadBlobUrl: vi.fn(),
  listUploads: vi.fn().mockResolvedValue([]), uploadFile: vi.fn(),
  UploadError: class UploadError extends Error {},
}))

afterEach(() => cleanup())
beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  useChatStore.setState({
    sessionId: null, pending: null, messages: [],
    usage: {
      input_tokens: 12_345, output_tokens: 678,
      cache_read_tokens: 9_000, cache_create_tokens: 0,
    },
  })
})

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}><ChatPanel /></QueryClientProvider>,
  )
}

it('shows exact input, output and cache-read token counts', async () => {
  renderPanel()
  const meter = await screen.findByTestId('chat-usage-meter')
  expect(meter.textContent).toContain('12,345')
  expect(meter.textContent).toContain('678')
  expect(meter.textContent).toContain('9,000')
})

it('shows no currency figure', async () => {
  renderPanel()
  const meter = await screen.findByTestId('chat-usage-meter')
  expect(meter.textContent).not.toMatch(/[€$]/)
})
```

- [ ] **Step 2: Run it and watch it fail**

Run: `pixi run bash -c 'cd pypsa-gui/frontend && npx vitest run src/components/ChatPanel.usage.test.tsx'`
Expected: FAIL — `chat-usage-meter` not found (the current testid is `chat-cost-meter`).

- [ ] **Step 3: Update the model union**

`pypsa-gui/frontend/src/api/chat.ts:24`:

```ts
// Keep in sync with chat_service.DEFAULT_MODEL / OPUS_MODEL, which
// tests/test_chat_models.py pins.
export type ChatModel = 'claude-sonnet-5' | 'claude-opus-5'
```

- [ ] **Step 4: Delete the pricing machinery**

In `pypsa-gui/frontend/src/store/chatStore.ts`, delete `PRICING_USD_PER_MTOK`, `USD_PER_EUR` and `deriveCostEur` entirely (lines 64-76). Keep `ChatUsageAcc`. Leave a note where they were:

```ts
// The EUR cost estimate lived here and was deleted deliberately: it derived
// from a hardcoded price table and a hardcoded USD_PER_EUR, both of which go
// stale silently and cannot be caught by a test. The meter now shows token
// counts, which are exact. Do not reintroduce a price table without a live
// rate source.
```

- [ ] **Step 5: Replace the meter**

In `pypsa-gui/frontend/src/components/ChatPanel.tsx`, change the import on line 40 to drop `deriveCostEur`:

```tsx
import { useChatStore, type UploadMetaUI } from '../store/chatStore'
```

and replace `CostMeter` with:

```tsx
function UsageMeter() {
  const usage = useChatStore((s) => s.usage)
  return (
    <span
      className="font-mono text-[10px] text-muted whitespace-nowrap"
      data-testid="chat-usage-meter"
      title="Tokens this session: input / output / read from cache"
    >
      {usage.input_tokens.toLocaleString()} in / {usage.output_tokens.toLocaleString()} out
      {' · '}{usage.cache_read_tokens.toLocaleString()} cached
    </span>
  )
}
```

Update the single `<CostMeter />` render site to `<UsageMeter />`.

- [ ] **Step 6: Run tests and typecheck**

```bash
pixi run bash -c 'cd pypsa-gui/frontend && npx vitest run src/components/ChatPanel.usage.test.tsx'
pixi run bash -c 'cd pypsa-gui/frontend && npx vitest run'
pixi run bash -c 'cd pypsa-gui/frontend && npx tsc -b'
```
Expected: 2 passed on the new file; full suite passes; `tsc` exit 0. The union change is a compile-time break by design — `tsc` finds every stale model string.

- [ ] **Step 7: Commit**

```bash
git add pypsa-gui/frontend/src/api/chat.ts pypsa-gui/frontend/src/store/chatStore.ts pypsa-gui/frontend/src/components/ChatPanel.tsx pypsa-gui/frontend/src/components/ChatPanel.usage.test.tsx
git diff --cached --name-only
git commit -m "feat(gui): Claude 5 in the picker, and replace the EUR estimate with exact token counts"
```

---

## Task 3: The microphone button must reflect usability, not presence

**Files:**
- Modify: `pypsa-gui/frontend/src/utils/speechToText.ts:94-111`
- Modify: `pypsa-gui/frontend/src/hooks/useSpeechToText.ts:47`
- Modify: `pypsa-gui/frontend/src/components/ChatPanel.tsx:1854-1860`
- Test: `pypsa-gui/frontend/src/utils/speechToText.permission.test.ts` (create)

**Interfaces:**
- Produces: `isPermissionError(code: string): boolean` from `speechToText.ts`; `useSpeechToText()` gains `available: boolean` and `permissionDenied: boolean` alongside the existing `supported`.

**Context — this is the actual bug.** `useSpeechToText.ts:47` reads `const supported = Ctor != null`, and `ChatPanel.tsx:1857` disables the button on `!speech.supported`. Measured on a real cocoa WKWebView: `webkitSpeechRecognition` **is** a function, `.start()` runs, and it fires `error: not-allowed`. So in the packaged app the constructor exists, the button renders **enabled**, and it fails on click. Presence is the wrong signal; permission is what varies.

Denial is **not persisted** across reloads. Persisting would leave the button permanently dead if permission were later granted, and one failed click per session is self-correcting.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/frontend/src/utils/speechToText.permission.test.ts`:

```ts
// Measured in a real cocoa WKWebView (backend/smoke/probe_webview_speech.py):
//   webkitSpeechRecognition: "function"   ← constructor IS present
//   .start()                → error: not-allowed
// So a presence check reports "supported" in the exact environment where voice
// cannot work, which is how the packaged app came to show an enabled mic
// button that always fails.
import { describe, expect, it } from 'vitest'
import { getSpeechRecognitionCtor, isPermissionError } from './speechToText'

describe('isPermissionError', () => {
  it('classifies the two denial codes', () => {
    expect(isPermissionError('not-allowed')).toBe(true)
    expect(isPermissionError('service-not-allowed')).toBe(true)
  })

  it('does not classify unrelated failures as denial', () => {
    // Negative control: a predicate returning true for everything would pass
    // the test above on its own.
    for (const code of ['no-speech', 'audio-capture', 'network', 'aborted', '']) {
      expect(isPermissionError(code)).toBe(false)
    }
  })
})

describe('getSpeechRecognitionCtor', () => {
  it('still reports the constructor when only the webkit prefix exists', () => {
    // This is WKWebView's shape. The function is correct; the CALLER was wrong
    // to treat this as "voice works".
    const win = { webkitSpeechRecognition: function () {} } as never
    expect(getSpeechRecognitionCtor(win)).not.toBeNull()
  })
})
```

- [ ] **Step 2: Run it and watch it fail**

Run: `pixi run bash -c 'cd pypsa-gui/frontend && npx vitest run src/utils/speechToText.permission.test.ts'`
Expected: FAIL — `isPermissionError` is not exported.

- [ ] **Step 3: Add the classifier**

In `pypsa-gui/frontend/src/utils/speechToText.ts`, above `speechErrorMessage`:

```ts
/**
 * Is this error code a permission denial rather than a transient failure?
 *
 * Measured: in a cocoa WKWebView the `webkitSpeechRecognition` constructor
 * exists and `.start()` fires `not-allowed`, because the app bundle declares
 * no NSMicrophoneUsageDescription. Callers must distinguish this from
 * `no-speech` or `network`, which are worth retrying.
 */
export function isPermissionError(errorCode: string): boolean {
  return errorCode === 'not-allowed' || errorCode === 'service-not-allowed'
}
```

Rewrite `speechErrorMessage`'s denial branch so it names the cause and a next step:

```ts
    case 'not-allowed':
    case 'service-not-allowed':
      return 'Microphone access was denied. Allow it in System Settings → '
        + 'Privacy & Security → Microphone, then try again.'
```

- [ ] **Step 4: Add the permission dimension to the hook**

In `pypsa-gui/frontend/src/hooks/useSpeechToText.ts`, keep `supported` as-is and add:

```ts
  const [permissionDenied, setPermissionDenied] = useState(false)
  // `supported` answers "does the API exist"; `available` answers "can it
  // actually be used". They differ in WKWebView, which is the packaged app.
  const available = supported && !permissionDenied
```

In the recognition `onerror` handler, set it:

```ts
      if (isPermissionError(e.error)) setPermissionDenied(true)
```

Return it: `return { supported, available, permissionDenied, listening, interim, toggle, stop }`.

- [ ] **Step 5: Make the button reflect availability**

In `pypsa-gui/frontend/src/components/ChatPanel.tsx` (lines ~1854-1860), replace `speech.supported` with `speech.available` in both the className and the `disabled` prop, and give the three states distinct titles:

```tsx
              title={
                !speech.supported
                  ? 'Voice input is not supported in this browser'
                  : speech.permissionDenied
                    ? 'Microphone access denied — allow it in System Settings → Privacy & Security → Microphone'
                    : 'Hold to dictate'
              }
```

- [ ] **Step 6: Run tests and typecheck**

```bash
pixi run bash -c 'cd pypsa-gui/frontend && npx vitest run'
pixi run bash -c 'cd pypsa-gui/frontend && npx tsc -b'
```
Expected: full suite passes, `tsc` exit 0.

- [ ] **Step 7: Commit**

```bash
git add pypsa-gui/frontend/src/utils/speechToText.ts pypsa-gui/frontend/src/utils/speechToText.permission.test.ts pypsa-gui/frontend/src/hooks/useSpeechToText.ts pypsa-gui/frontend/src/components/ChatPanel.tsx
git diff --cached --name-only
git commit -m "fix(gui): the mic button reflects permission, not merely API presence"
```

---

## Task 4: Bundle permissions and a repeatable WKWebView probe

**Files:**
- Modify: `pypsa-gui/pypsa-gui.spec:275-288`
- Create: `pypsa-gui/backend/smoke/probe_webview_speech.py`
- Modify: `pypsa-gui/CHATBOT.md`

**Context:** `info_plist` declares neither `NSMicrophoneUsageDescription` nor `NSSpeechRecognitionUsageDescription`. macOS denies microphone access outright without them, which is consistent with the measured `not-allowed`. **This is a diagnosis, not a proven fix** — confirming it requires launching a built bundle carrying the keys.

- [ ] **Step 1: Add the usage strings**

In `pypsa-gui/pypsa-gui.spec`, inside the existing `info_plist={...}` dict:

```python
        # macOS denies the microphone outright to a bundle that does not
        # declare why it wants one. Measured before adding these: a WKWebView
        # exposes `webkitSpeechRecognition`, `.start()` runs, and it fires
        # `not-allowed` — the signature of a permission refusal rather than a
        # missing API. The strings are shown verbatim in the OS prompt.
        "NSMicrophoneUsageDescription":
            "PyPSA Studio uses the microphone only while you hold the "
            "dictate button in the assistant, to turn speech into text.",
        "NSSpeechRecognitionUsageDescription":
            "PyPSA Studio uses speech recognition to transcribe what you "
            "dictate to the assistant.",
```

- [ ] **Step 2: Land the probe as a repeatable harness**

Create `pypsa-gui/backend/smoke/probe_webview_speech.py` with the loopback-served probe (the inline-HTML variant is wrong — inline content is not a secure context, and `navigator.mediaDevices` is gated on that, so it reports a false negative):

```python
"""
Measure what a REAL cocoa WKWebView exposes for speech.

Same treatment `smoke/audit_downloads.py` gives downloads. Serves the probe
over loopback HTTP because the packaged app serves its SPA from
http://127.0.0.1:<port>, which WebKit treats as a secure context — an earlier
version of this probe loaded inline HTML, was NOT a secure context, and
reported a false `getUserMedia: false`.

    pixi run -e test python pypsa-gui/backend/smoke/probe_webview_speech.py

Measured 2026-08-05 on macOS 15 / arm64:
    webkitSpeechRecognition: "function"   SpeechRecognition: undefined
    speechSynthesis: object, 219 voices   mediaDevices: false
    secureContext: true                   .start() -> error: not-allowed
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import webview

PAGE = b"""<html><body style="font:13px system-ui">probing...
<script>
window.__probe = {phase: "init", events: []};
try {
  var Ctor = window.SpeechRecognition || window.webkitSpeechRecognition;
  window.__probe.caps = {
    SpeechRecognition: typeof window.SpeechRecognition,
    webkitSpeechRecognition: typeof window.webkitSpeechRecognition,
    speechSynthesis: typeof window.speechSynthesis,
    voices_now: window.speechSynthesis ? window.speechSynthesis.getVoices().length : -1,
    mediaDevices: !!navigator.mediaDevices,
    secureContext: window.isSecureContext,
    origin: location.origin
  };
  var r = new Ctor();
  r.onstart = function(){ window.__probe.events.push("start"); };
  r.onerror = function(e){ window.__probe.events.push("error:" + (e.error || "?")); };
  r.start();
  window.__probe.phase = "started";
} catch (e) {
  window.__probe.phase = "threw";
  window.__probe.events.push("throw:" + (e && e.name ? e.name : String(e)));
}
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *args):
        pass


def _probe(window):
    import time

    try:
        time.sleep(5)  # let onstart / onerror fire
        print("PROBE_JSON:" + json.dumps(json.loads(
            window.evaluate_js("JSON.stringify(window.__probe)")
        )))
    except Exception as exc:  # noqa: BLE001 — a probe that dies must say why
        print("PROBE_ERROR:" + repr(exc))
    finally:
        window.destroy()


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _win = webview.create_window(
        "speech capability probe",
        url=f"http://127.0.0.1:{server.server_address[1]}/",
        width=420,
        height=160,
    )
    webview.start(_probe, _win)
```

- [ ] **Step 3: Run it**

Run: `pixi run -e test python pypsa-gui/backend/smoke/probe_webview_speech.py`
Expected: a `PROBE_JSON:` line. Record it in the commit message.

- [ ] **Step 4: Lint**

Run: `pixi run -e dev ruff check ./pypsa-gui/backend/smoke/probe_webview_speech.py`
Expected: clean.

- [ ] **Step 5: Document the state honestly in CHATBOT.md**

Add to the voice section:

```markdown
Voice input requires microphone permission, which the packaged app requests
via `NSMicrophoneUsageDescription`. If macOS has denied it, the mic button is
disabled and its tooltip says so — it is not a missing feature.

Voice OUTPUT (the assistant speaking) needs no permission and is available in
the packaged app: measured at 219 voices in a real WKWebView. It is not wired
up yet; that is part of the assistant redesign, not this change.
```

- [ ] **Step 6: Commit**

```bash
git add pypsa-gui/pypsa-gui.spec pypsa-gui/backend/smoke/probe_webview_speech.py pypsa-gui/CHATBOT.md
git diff --cached --name-only
git commit -m "fix(gui): declare microphone permission in the bundle, and land the WKWebView probe"
```

- [ ] **Step 7: MANUAL VERIFICATION — required before this task is called done**

This is the only step that can confirm the plist fix, and it cannot be automated here.

1. Build: `SKIP_DMG=1 BUILD_PYTHON=<a non-conda python3.13> bash pypsa-gui/build-macos.sh`
2. Install and launch the built app.
3. Click the mic button in the assistant.
4. Record which happens: the macOS permission prompt appears and `onstart` fires (**fix confirmed**), or `not-allowed` fires again (**fix insufficient — the plist keys were necessary but not sufficient, and the remaining blocker is WKWebView media-capture delegation in the pywebview shell**).

Write the outcome into the commit message or the plan's ledger. Do **not** report voice input as fixed without this step.

---

## Self-Review

**Spec coverage.** Model constants → Task 1. Vision literal → Task 1. `ChatModel` union → Task 2. Pricing deletion → Task 2. Three-state mic → Task 3. Plist keys → Task 4. Probe harness → Task 4. Docs → folded into 1 and 4. Manual build verification → Task 4 Step 7. No spec requirement is unassigned.

**Placeholders.** None: every code step carries the actual code.

**Type consistency.** `isPermissionError` is defined in Task 3 Step 3 and used in Task 3 Step 4 under the same name. `available` / `permissionDenied` are introduced in Step 4 and consumed in Step 5. `UsageMeter` replaces `CostMeter` at its single render site. `deriveCostEur` is deleted in Task 2 and referenced by no later task.

**One deliberate ordering note.** Task 2 deletes `deriveCostEur` while Task 1 changes the model strings it was keyed on. Running Task 2 before Task 1 would leave the pricing table keyed on a union that no longer includes its keys — a `tsc` error, not a silent failure, but the stated order avoids it.
