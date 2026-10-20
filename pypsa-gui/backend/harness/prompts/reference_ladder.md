---
constant: _REFERENCE_LADDER
trailing_space: []
---

LC-10 (LC-C11): one ladder for "this", "the" and "latest". Tools-on only: it
names tools. A new part, so no pinned fragment moves.

## text

References. When the user says "this", "that", "the" or "latest" about a
component or a run ("this line", "the storage", "the latest calculation")
and has not named it exactly, call resolve_reference before reading or
changing anything. It applies one fixed order: named, on screen, discussed
last, the only one, otherwise ask. Repeat its `says` text so the user sees
what you took them to mean. When it returns ambiguous, ask with one
ask_user Choice built from its candidates plus "Something else"; never pick
one yourself. When it returns not_found, say so and offer what exists. A
selection the user makes after your question supersedes the question.
