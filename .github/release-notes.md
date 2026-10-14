## Download (Windows)

`VimCommander-v0.2.0-windows-x64.zip` (below, under Assets): unzip anywhere and run `VimCommander.exe`. No Python needed; settings live in `config.json` next to the exe (unzipping over an older version keeps them). The exe isn't signed, so SmartScreen may warn the first time: *More info* → *Run anyway*.

On Linux and macOS, run from source (see the [README](https://github.com/vim4all/VimCommander#installation)).

## New

- **Markdown notes in quick view**: rendered, with Obsidian `[[links]]` (click to follow) and embedded images; `:obsidian` opens a note in Obsidian. PDFs preview too.
- **More graph views**: treemap, timeline (when each subfolder changed) and types (what each subfolder is made of).
- **Color themes** (the config's `theme` option).
- More file kinds colored: design files (Ansys, KiCad, CAD) and build output.

## Fixed

- The exe and window now have an icon (it was blank in Explorer and the taskbar).
- In a narrow pane, the Size and Modified columns no longer cover the names: the date gets shorter, then the columns hide.
