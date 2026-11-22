* Fix(pypsa-gui): A decision-study run, tornado, supply-architecture comparison, investment case or business case started by an editor who is made a viewer, or removed, while it runs now fails with `project_view_only` and saves nothing into the project.

* Fix(pypsa-gui): When the requester's role cannot be read from the database, the solve queue and these runs now refuse (`project_view_only`, logged) instead of failing open or crashing the worker.
