# LV08 B-campus: content cards for the grid-limit and external-dispatch tools

Status: done

`campus_set_grid_limit`, `campus_reset_grid_limit` and `gridspine_upload_external_dispatch` are settings
edits with no `edit_card` adapter. `tests/test_edit_card.py::_NO_ADAPTER_YET` lists them (accepted by GK B
on #800). Give each its own old -> new adapter, as #784 did for the settings tools, then remove them from
the set. #795 empties that list, so whichever of #795 and #800 merges second keeps exactly these three.
