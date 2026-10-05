---
name: grill
description: Interview the user relentlessly about a plan, a design or a modelling decision until the two of you share one understanding. Use when the user asks to be grilled, to stress-test a plan, or says "ask me what you need to know".
---

Interview the user until you reach a shared understanding. Map the plan as a
**design tree**: every decision branches into the decisions that hang off it.

Work the tree in **rounds**. The **frontier** is every decision whose
prerequisites are already settled: the questions you can ask now without
guessing at answers you have not heard. Ask the whole frontier in one round.

Ask each question with the `ask_user` tool: the title is the decision, the
question body says why it matters in one or two sentences, the options are
the real alternatives, and exactly one option is marked `recommended` with
the reason in its description. Allow free text. Number the titles
(Q1, Q2, …) so the user can refer to them. Present every question of the
round, then end your turn and wait; the user's picks arrive as their next
messages.

Each round the answers reshape the tree: settled decisions push the frontier
outward and unblock the questions that depended on them. Recompute the
frontier and ask the next round. A question whose answer depends on another
question still open in this round belongs to a later round.

Finding facts is your job, never the user's. When a frontier question needs
a fact from the network or the project, call the read tools (`get_meta`,
`list_components`, `get_simulation_status`, `get_project_results_summary`)
and state what you found; ask the user only for decisions.

The session is done when the frontier is empty. Close with the decisions as
a numbered list, each with the option chosen, and ask whether to act on
them. Do not act before the user confirms.
