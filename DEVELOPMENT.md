# Developing VimCommander

How the source is laid out, how keys and commands are wired, and how to test. [CLAUDE.md](CLAUDE.md) explains the architecture in more depth; the [README](README.md) covers what the app does.

## Source layout

```text
main.py                  entry point
vimcommander/
  vim.py                 modal key engine (pure Python, no Qt)
  app.py                 main window, key dispatch, ':' commands, context menu, status bar
  pane.py                tabs, drive button, directory model, file view, drag and drop
  fileops.py             copy / move / trash / delete / undo in a background thread
  dialogs.py             progress, name clash and zoxide dialogs
  results.py             result lists: the :find search, searches in a thread
  preview.py             quick view
  tabular.py             CSV parsing: the table, and every numeric column for the plot (pure Python)
  plotview.py            the plot (zp, .raw): strips, min-max bars per pixel, scatter, hover, zoom
  ltspice.py             LTspice .raw waveforms: binary / text, real / AC, operating points (pure Python)
  mdrender.py            Markdown to Qt rich text: md2doc callouts, shaded tables, figures (pure Python)
  notebook.py            Jupyter notebooks as Markdown and images, for quick view (pure Python)
  cubemx.py              STM32CubeMX .ioc projects: chip, clocks, pins, peripherals (pure Python)
  archive.py             zip files as read-only folders (pure Python)
  trash.py               the system trash: listing, restoring, deleting for good (pure Python)
  rename.py              multi-rename patterns and :s (pure Python)
  winshell.py            the Windows Explorer context menu (ctypes, Windows only)
  zoxide.py              zoxide calls and its keyword matching (pure Python)
  graph.py               folder, note and code graphs: scanning, imports, layouts (pure Python)
  graphview.py           the graph widget: tree, sunburst, notes and code (drawing, zoom, hover, clicks)
  actions.py             descriptions of every action and command (pure Python)
  whichkey.py            the which-key panel
  fuzzy.py               fuzzy matching (pure Python)
  palette.py             command palette and fuzzy file finder
  bulkrename.py          :bulkrename dialog
  compare.py             folder tree comparison (pure Python)
  sync.py                :synchronize dialog
  grep.py                search inside files (pure Python)
  grepdialog.py          the :grep window
  textdiff.py            side-by-side line alignment (pure Python)
  diffview.py            the diff window
  shelf.py               the shelf panel
  music.py               the player's playlists: audio files, album order, .m3u, shuffle (pure Python)
  player.py              the music player: QtMultimedia, the bottom bar, the playlist pane
  sizing.py              tool windows sized to fit the screen
  cleanup.py             :reclaim and :dupes (pure Python)
  memmap.py              firmware memory maps: GNU ld / Keil .map and ELF parsing (pure Python)
  memmapview.py          the memory map quick view: region bars and treemap
  step.py                STEP 3D models: entities, B-rep faces, curves, assemblies, colours (pure Python)
  stepview.py            the 3D model quick view: shaded faces back to front, rotate, pan, zoom, measure, keys
  stl.py                 STL meshes, binary and text, with their sharp edges, as a 3D model (pure Python)
  termview.py            the terminal pane: pyte screen, drawing, keys, scrollback, selection
  termshell.py           which shell the terminal pane runs; OSC 7 folders, cd lines, sshfs (pure Python)
  ptyproc.py             a program in a pseudo-terminal: pty or ConPTY (pywinpty) (pure Python)
  wsl.py                 WSL paths both ways and the installed distros (pure Python)
  pdftools.py            :pdfmerge, :pdfextract, and the lock every PDFium call holds (pure Python)
  gitstatus.py           git status, branch, file at HEAD (pure Python)
  rules.py               folder rules: parsing and planning (pure Python)
  rulesdialog.py         the :rules preview
  grid.py                thumbnail loading and the grid's tiles
  terminal.py            the terminal panel
  share.py               the :share web server (pure Python)
  sharedialog.py         the :share window with the QR code
  remote.py              SSH / SFTP folders: connections, listing, copying (pure Python, paramiko)
  activity.py            activity scan, calendar heatmap and recent files
  clipboard.py           files on the system clipboard (copy / cut, Explorer, KDE, GNOME)
  bar.py                 launch bar, button editor, program launching
  config.py              settings file: location, defaults, load / save
build.py                 standalone build with PyInstaller
tools/screenshots.py     renders the README screenshots from a demo folder
tests/
  test_vim.py            key engine tests, langmap
  test_config.py         config file, user key maps, command lines
  test_archive.py        zip browsing and extraction
  test_rename.py         rename patterns and :s
  test_zoxide.py         zoxide keyword matching
  test_graph.py          graph scanning, sizes, layouts, note links and code imports
  test_find.py           :find, :grep, zip, text diff, PDF merge and page extraction
  test_tabular.py        CSV tables, the plot's columns, number labels
  test_ltspice.py        LTspice .raw formats
  test_mdrender.py       Markdown rendering: callouts, shaded tables, lists, math, md2doc figures
  test_preview.py        natural sort, notebooks, .ioc projects, the quick view's plot and 3D model
  test_memmap.py         GNU ld / Keil map and ELF parsing
  test_step.py           STEP parsing, faces, colours, assembly placement and part names, curves; STL
  test_termpane.py       WSL paths, shells, OSC 7, cd lines, sshfs, the terminal screen
  test_space.py          :reclaim, :dupes, git status parsing, folder rules
  test_share.py          :share downloads, uploads and safety; terminal commands
  test_remote.py         sftp:// paths, listing and copying against a test SSH server
  sshserver.py           the small SSH server those tests use
  test_tools.py          fuzzy matching, descriptions, bulk rename, compare, activity
  test_trash.py          the trash on Linux and Windows, restoring, the saved undo history
  test_music.py          playlists, .m3u, the player's order, repeat, shuffle and removing tracks
```

## Keys and commands

Each key press in a pane becomes a vim-style key name (`"j"`, `"<C-d>"`). `KeyEngine` in `vim.py` collects counts and sequences and returns an action, such as `yank` with count 3. The main window then calls the method `cmd_<action>`. Commands typed after `:` call `ex_<command>`.

To add a key, add a binding to the tables in `vim.py` and a `cmd_<name>` method in `app.py`. Describe it in `actions.py` (a test fails otherwise), and update the `HELP` text in `app.py` and the README's [key reference](README.md#key-reference). A `:` command is an `ex_<name>` method; list it in the README's grouped [command tables](README.md#commands).

## Tests

Run the tests:

```bash
python -m unittest discover -s tests                         # all tests
python -m unittest tests.test_vim.KeyEngineTest.test_count   # one test
```

For scripted GUI testing without a display, set `QT_QPA_PLATFORM=offscreen`, and set `VIMCOMMANDER_CONFIG` to a temporary file so your own settings aren't touched.

Building the standalone app is described in the README: [Building a standalone app](README.md#building-a-standalone-app).
