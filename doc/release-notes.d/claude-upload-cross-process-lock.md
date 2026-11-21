* Fix(pypsa-gui): Uploading the same bytes to one project from two API processes at once now stores one copy; a busy upload lock returns a stated 409 `upload_busy` error.
