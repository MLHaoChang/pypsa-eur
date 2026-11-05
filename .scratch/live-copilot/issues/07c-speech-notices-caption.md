# LC-07c: show speech notices in the Live caption

Status: ready-for-agent
Type: task
Dependencies: LC-07 PR4 (`claude/lc07-echo-guard`)
Review: UX reviewer (UI gate), GK B

When a spoken final is not admitted, the backend now keeps a transcript-free
notice for the session's owner. `GET /api/durable-conversations/{id}/speech`
returns `notices`: at most 4, each `{status, message, seq, generation}`, only
for the current thread generation. The statuses are `echo_ignored` (it matched
the answer being read out), `clarify` (two different numbers in one segment)
and `not_an_approval` (speech never approves a card).

Until this issue lands, the browser shows none of them: the drop is silent
(recorded under LC-07's audio-worker gate).

Scope: `hooks/useLiveSpeech.ts` reads `notices` while a session is open and
shows the newest unseen one in the Companion Live caption, in Guided and
Expert. Show only the server's fixed `message`, never transcript text. Each
notice is shown once per `seq`, and a rebind or session exit clears it. Before
building, tell the UX reviewer where this sits in the workflow. Add a vitest
for each status and for "no transcript shown".
