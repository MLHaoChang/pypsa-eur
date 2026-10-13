---
id: open-project
title: Open or create a project
intent: Find an existing project or start a new one from a template, a file or a blank network.
when: [unbound]
order: 10
opening_request: I want to open or create a project. List my projects and the available templates, then ask me which to open.
steps:
  - id: choose
    title: Choose a project
    done_when: A project is the active Context (load_project or create_project_from_template succeeded).
---

## Step: choose

Call `list_projects`. If the user named a project, call `load_project` with
it. Otherwise present the choice with `ask_user`: the existing projects (most
recently saved first, at most eight), "New from a template" and "New blank
project". For a template, call `get_eh_template` to describe it in one
sentence each before the user picks, then `create_project_from_template`.
For a blank project, send the user to the New project wizard with
`ui_open_panel` rather than creating files yourself. Finish by saying which
project is open and offering the start menu again.
