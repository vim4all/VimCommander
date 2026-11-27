# Contributing to VimCommander

Thanks for helping! Bug reports, sample files and pull requests are all welcome.

## Questions and ideas

Ask in [Discussions](https://github.com/vim4all/VimCommander/discussions). Ideas for new keys, commands or previews go in the **Ideas** category, where a 👍 helps decide what comes next. Check the [Roadmap](README.md#roadmap) and [Limitations](README.md#limitations) first: they may already cover it.

## Reporting a bug

Open an [issue](https://github.com/vim4all/VimCommander/issues/new/choose) and pick a template:

- **Bug report**: something crashes or does the wrong thing.
- **Quick view shows a file wrong**: a `.map`, `.elf`, `.ioc`, LTspice `.raw`, CSV, STEP, STL or other file that previews badly. **A sample file helps more than anything else.** Strip or replace anything confidential first; a small project that shows the same problem is just as good.
- **Feature request**: a key, command or preview you'd like.

Please include how you run VimCommander (the Windows exe, or from source with your Python and PySide6 versions) and the exact keys you pressed. Run `:messages` to see recent status messages.

## Pull requests

[DEVELOPMENT.md](DEVELOPMENT.md) explains the source layout and how keys and commands are wired. In short:

1. **Discuss bigger changes first** in an issue or discussion, so nobody spends a weekend on something that won't fit.
2. **Keep the vim feel**: a new key should do what a vim or vifm user would expect. If vim already has a key for the idea, use that one.
3. **A new key or command** needs:
   - the binding in `vim.py` and a `cmd_<name>` method in `app.py`, or an `ex_<name>` method for a `:` command
   - its description in `actions.py` (a test fails otherwise)
   - the `HELP` text in `app.py`
   - the README's [key reference](README.md#key-reference) or [command tables](README.md#commands)
4. **Add tests** in `tests/` and run them all:

   ```bash
   QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests
   ```

   Set `VIMCOMMANDER_CONFIG` to a temporary file when you try things out, so your own settings aren't touched.
5. **Match the surrounding code**: plain Python with type hints, logic kept out of the Qt widgets where it can be (the `pure Python` modules in DEVELOPMENT.md), and short docstrings that say what something is for.
6. **Python 3.10 must keep working**: it's what Ubuntu 22.04 ships.
7. If your change alters what the screenshots show, re-render them with `python tools/screenshots.py` (Linux, headless).

By contributing, you agree that your contribution is licensed under the [MIT License](LICENSE).
