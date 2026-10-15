## Download (Windows)

`VimCommander-v0.3.0-windows-x64.zip` (below, under Assets): unzip anywhere and run `VimCommander.exe`. No Python needed; settings live in `config.json` next to the exe (unzipping over an older version keeps them). The exe isn't signed, so SmartScreen may warn the first time: *More info* → *Run anyway*.

On Linux and macOS, run from source (see the [README](https://github.com/vim4all/VimCommander#installation)).

## New

- **`:g` / `:v` global commands**: tag the items matching a pattern, then delete, yank, run normal keys or a `:` command on them.
- **Copy queue** with a jobs panel: copies and moves wait for each other, can be paused and sent to the background.
- **FTP / FTPS folders**, next to the SSH ones.
- **Pack and unpack**: `Alt-F5` packs into a zip in the other pane (name, compression, move, one per item), `Alt-F9` unpacks.
- **Branch view** (`gb`, `:branch`): every file below the folder in one list.
- **Checksums** (`:hash`): write or verify `.sha256` / `.md5` etc. files.
- **Recent files** (`:recent`): files you opened, or files changed in the last days.
- **CSV / TSV preview** as a table, with column summaries on the headers.
- **Hex view** (`:hex`) for any file; binary files always show as hex.
- **New help window** (`F1`) with sections and searchable keys.
- **New app icon**.

## Fixed

- Status messages in a narrow pane no longer widen the window.
- The graph toolbar keeps the same buttons in every mode.
