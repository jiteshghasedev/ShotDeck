Rhino has no Python menu API -- on purpose, this folder stays empty.

Rhino builds its menus from a .rui workspace file that is edited in the UI and
loaded per user. Python cannot add to it, and writing into an artist's .rui at
startup would edit a file they own, on every launch, to add a menu. Not worth
it for a menu.

So in Rhino the Flow actions are run by name. In the Python editor
(EditPythonScript), or from a one-line script bound to an alias:

    import flow_dcc
    adapter = flow_dcc.adapter("rhino")

    adapter.action_load_file()             # open one of the task's files
    adapter.action_save()                  # save over the open model
    adapter.action_save_as()               # save somewhere you pick
    adapter.action_version_up()            # next version in the work folder
    adapter.action_publish()               # publish + register in ShotGrid
    adapter.action_open_work_folder()
    adapter.action_open_publish_folder()
    adapter.action_context()               # which shot/task/step this is

To get them onto buttons, add aliases (Tools > Options > Aliases), each
running _-RunPythonScript with the two lines above. That is a per-workstation
setting, which is why it is documented rather than deployed.

Everything else -- the file names, the versions, the publish folder, the
ShotGrid record -- is the same as in Maya and Nuke.
