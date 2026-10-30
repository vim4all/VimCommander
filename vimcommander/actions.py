"""Descriptions of every action and ':' command, for the which-key popup and the
command palette. Pure Python, no Qt.

When adding an action (cmd_<name>) or a command (ex_<name>), describe it here.
"""

ACTIONS = {
    # Moving
    "down": "down", "up": "up", "first": "first row", "last": "last row",
    "half_down": "half a page down", "half_up": "half a page up",
    "page_down": "page down", "page_up": "page up",
    "screen_top": "top of the screen", "screen_middle": "middle of the screen",
    "screen_bottom": "bottom of the screen",
    "scroll_center": "scroll: cursor to the middle", "scroll_top": "scroll: cursor to the top",
    "scroll_bottom": "scroll: cursor to the bottom",
    "scroll_line_down": "scroll one row down", "scroll_line_up": "scroll one row up",
    "group_next": "next group (folders, each extension)", "group_prev": "previous group",
    "item_next": "next item (in the grid: right)", "item_prev": "previous item (in the grid: left)",
    "percent": "go to N% of the list",
    "find_char": "next name starting with…", "find_char_back": "previous name starting with…",
    "repeat_find": "repeat f / F", "repeat_find_back": "repeat f / F backwards",
    "search": "search forward", "search_back": "search backward",
    "search_next": "next match", "search_prev": "previous match",
    "search_like": "next name with this extension", "search_like_back": "previous name with this extension",
    # Folders, panes, tabs
    "parent": "parent folder", "open": "open folder or file", "home": "home folder",
    "back": "back in folder history", "forward": "forward in folder history",
    "alternate": "previous folder", "switch_pane": "other pane", "left_pane": "left pane",
    "vterm": "terminal in the other pane",
    "only_pane": "only this pane / both panes", "swap_panes": "swap the panes",
    "equal_panes": "panes of equal width", "maximize_pane": "make this pane as wide as possible",
    "resize_pane": "this pane wider / narrower (N × 5%)",
    "close_pane": "close this pane: only the other one", "split_pane": "both panes, the other one in this folder",
    "right_pane": "right pane", "reload": "reload both panes", "toggle_hidden": "show / hide hidden files",
    "show_hidden": "show (zo) / hide (zm) hidden files",
    "drives": "drive menu", "new_tab": "new tab", "next_tab": "next tab", "prev_tab": "previous tab",
    "set_mark": "set a mark…", "goto_mark": "go to a mark…", "bar": "launch bar button",
    "zoxide_pick": "jump to a folder (zoxide)", "files": "find files (fuzzy)",
    "goto_file": "go to the file's folder (from a result list)", "grep": "search inside files",
    "repeat_substitute": "repeat the last :s",
    "qf_next": "next result, in its folder", "qf_prev": "previous result, in its folder",
    # Selecting
    "tag": "tag / untag", "invert_tags": "invert tags", "select_prompt": "tag by pattern",
    "visual": "visual mode", "reselect": "reselect the last visual range",
    "swap_anchor": "other end of the range", "escape": "clear tags, leave visual mode",
    "filter": "filter the list",
    # Files
    "yank": "yank (copy)", "cut": "cut", "paste": "paste here", "yank_motion": "yank to the motion",
    "cut_motion": "cut to the motion", "yank_path": "copy path(s) to the clipboard",
    "yank_dir": "copy folder path to the clipboard", "yank_name": "copy name(s) to the clipboard",
    "copy_other": "copy to the other pane", "move_other": "move to the other pane",
    "pack": "pack into a zip in the other pane (options dialog)",
    "unpack": "unpack zips into the other pane",
    "branch": "branch view: every file below this folder",
    "trash": "move to trash", "delete": "delete permanently", "rename": "rename",
    "rename_stem": "rename, keep the extension",
    "rename_at": "rename: cursor at the start (I) / end (A), new name (cc), new name keeping the extension (C)",
    "upper": "names to UPPER case", "lower": "names to lower case", "toggle_case": "switch the case of the names",
    "upper_motion": "UPPER case to the motion", "lower_motion": "lower case to the motion",
    "toggle_case_motion": "switch the case to the motion",
    "keys": "run keys (a :noremap)", "remap": "run keys through the maps (a :map)",
    "increment": "add N to the number in the name (g: N, 2N, 3N…)",
    "decrement": "subtract N from the number in the name (g: N, 2N, 3N…)", "mkdir": "new folder", "view": "view",
    "edit": "edit", "open_system": "open with the system app", "shell": "terminal here",
    "info": "file details", "dir_size": "add up folder size", "all_dir_sizes": "add up all folder sizes",
    "undo": "undo", "redo": "redo", "repeat": "repeat the last change",
    "shelf_add": "put on the shelf", "shelf": "show / hide the shelf", "diff": "diff two files side by side",
    "play": "play music: the folder, tagged files or .m3u (from the file under the cursor)",
    "play_pause": "music: pause / resume", "play_next": "music: next track",
    "play_prev": "music: start of the track / previous track", "play_stop": "music: stop",
    "playlist": "music: the playlist in the other pane",
    "record": "record a macro into…", "at": "play a macro… (@: last command)",
    # Views and windows
    "quickview": "quick view", "plot": "CSV in quick view: plot / table", "model_keys": "keys to the 3D model in quick view", "grid": "thumbnails / list", "terminal": "terminal panel", "graph_window": "graph window", "activity": "activity heatmap",
    "context_menu": "context menu", "command": "command line", "palette": "command palette",
    "help": "key reference", "quit": "close tab / quit",
}

# ':' commands: canonical name -> (usage, description). Aliases are listed in ALIASES.
COMMANDS = {
    "activity": ("activity", "calendar of changes below this folder, and recent files"),
    "bar": ("bar [add PATH]", "show / hide the launch bar, or add a button"),
    "bulkrename": ("bulkrename", "rename tagged (or all) items in an editor"),
    "cd": ("cd PATH", "change folder (- = previous)"),
    "compare": ("compare", "tag the differences between the two panes"),
    "config": ("config", "edit the config file"),
    "delmarks": ("delmarks [LETTERS]", "delete marks"),
    "delsession": ("delsession NAME", "delete a saved session"),
    "dupes": ("dupes [MINSIZE] | tag [old]", "duplicate files as a list; tag all but the newest of each set"),
    "diffthis": ("diffthis [FILE]", "two files side by side: tagged, both cursors, or with FILE"),
    "drives": ("drives", "drive menu"),
    "edit": ("edit [FILE]", "open a file in the editor"),
    "files": ("files", "find files below this folder (fuzzy)"),
    "filter": ("filter [PATTERN]", "show only matching names"),
    "find": ("find [PATTERN] [size>1M] [newer:7d] [type:f]", "search names below this folder, as a result list; size, age and type conditions"),
    "ftp": ("ftp [user@]host[:port][/path] | close", "open a folder on an FTP server (ftps://host = over TLS)"),
    "global": ("g/PATTERN/[CMD]", "tag matching names and run CMD on them: d, d!, y, normal KEYS or a : command; "
                                  ":g! or :v = the others"),
    "hex": ("hex [on|off]", "quick view shows every file in hex (binary files always)"),
    "jobs": ("jobs [pause|resume|cancel]", "the jobs panel: running and queued copies, pause, cancel"),
    "graph": ("graph [1|2|3] [size|count] [tree|sunburst|treemap|timeline|types|notes|code]", "folder graph and its options"),
    "git": ("git [changed|diff]", "git branch and counts, changed files as a list, or file vs last commit"),
    "grep": ("grep [PATTERN]", "search inside the files below this folder"),
    "grid": ("grid [on|off|SIZE]", "thumbnail grid for this pane, or the thumbnail size"),
    "mirror": ("mirror", "make the other pane's folder a copy of this one (preview first)"),
    "help": ("help [TOPIC]", "key reference (only the lines about TOPIC)"),
    "linkpanes": ("linkpanes [on|off]", "both panes go into the same subfolders; mark what one side lacks"),
    "marks": ("marks", "list marks"),
    "mksession": ("mksession NAME", "save both panes' tabs and the shelf as a session"),
    "mkdir": ("mkdir NAME", "new folder"),
    "nohlsearch": ("nohlsearch", "clear the search highlight"),
    "obsidian": ("obsidian", "open the note or folder under the cursor in Obsidian"),
    "palette": ("palette", "command palette"),
    "play": ("play [PATH|pause|next|prev|stop|off|list|shuffle|repeat [all|one|off]|volume N|speed RATE|seek S|save NAME]",
             "the music player: play a folder or the files under the cursor, control it, save the playlist"),
    "q": ("q", "close tab, or quit"),
    "qa": ("qa", "quit"),
    "reclaim": ("reclaim [SIZE] [DAYS]", "caches and big old files to free space, biggest first"),
    "recent": ("recent [changed] [DAYS] | clear", "files you opened, or files changed here in the last DAYS days"),
    "redo": ("redo", "redo"),
    "registers": ("registers", "list registers and macros"),
    "rename": ("rename NAME|PATTERN", "rename (patterns: {name} {ext} {n})"),
    "rules": ("rules", "preview and apply the folder rules from the config"),
    "results": ("results", "show the last result list again"),
    "select": ("select PATTERN", "tag matching items"),
    "session": ("session [NAME]", "open a saved session, or list them"),
    "set": ("set OPTION", "options: nu rnu ic scs hls ws hidden columns git natural clipboard= "
                          "(saved); hex plot grid quickview linkpanes bar (this session)"),
    "share": ("share [stop]", "send the tagged files to a phone by QR code, and receive files back"),
    "shelf": ("shelf [add|copy|move|zip|list|clear]", "the shelf: collect files, act on them all"),
    "ssh": ("ssh [user@]host[:path] | close", "open a folder on an SSH server in this pane"),
    "shell": ("shell", "terminal here"),
    "sort": ("sort[!] name|ext|size|time", "sort this pane (! or -size = reverse)"),
    "sync": ("sync", "open this folder in the other pane too"),
    "synchronize": ("synchronize", "compare the panes' folder trees and copy what differs"),
    "tabclose": ("tabclose", "close this tab"),
    "tabmove": ("tabmove [N]", "move this tab"),
    "tabnew": ("tabnew [PATH]", "new tab"),
    "tabs": ("tabs", "list tabs"),
    "terminal": ("terminal [COMMAND]", "the terminal panel; it follows the active pane"),
    "touch": ("touch NAME", "new empty file"),
    "branch": ("branch", "list every file below this folder (branch view)"),
    "pdfmerge": ("pdfmerge [NAME]", "join the tagged PDFs, in the pane's order, into NAME.pdf here"),
    "pdfextract": ("pdfextract PAGES [NAME]", "copy pages (3, 2-5, 1,4,7-) of the PDF(s) into a new PDF next to it"),
    "cdo": ("cdo CMD", "run CMD for each item of the result list (tagged, or all): !shell %f or a : command"),
    "cnext": ("cnext [N]", "the next item of the result list, in its folder (]q)"),
    "cprevious": ("cprevious [N]", "the previous item of the result list, in its folder ([q)"),
    "copen": ("copen", "show the pane's result list (same as :results)"),
    "cclose": ("cclose", "leave the result list: back to the folder it was made in"),
    "clist": ("clist", "list the items of the result list, numbered"),
    "colder": ("colder [N]", "the result list before this one (the pane keeps the last 10)"),
    "cnewer": ("cnewer [N]", "the result list after this one"),
    "delete": ("delete[!]", "trash the tagged items (or the cursor's); :delete! deletes them for good"),
    "yank": ("yank [x]", "yank the tagged items (or the cursor's), into register x"),
    "put": ("put [x]", "paste here (from register x)"),
    "normal": ("normal KEYS", "run KEYS as if typed in the pane, e.g. :normal 3jyy"),
    "pwd": ("pwd", "show this pane's folder"),
    "messages": ("messages", "the last messages from the status bar"),
    "tabnext": ("tabnext [N]", "the next tab, or tab N"),
    "tabprevious": ("tabprevious [N]", "the previous tab (N tabs back)"),
    "tabfirst": ("tabfirst", "the first tab"),
    "tablast": ("tablast", "the last tab"),
    "tabonly": ("tabonly", "close the pane's other tabs"),
    "hash": ("hash [md5|sha1|sha256|sha512|check]", "write a checksum file for the tagged items, or verify one"),
    "undo": ("undo", "undo"),
    "trash": ("trash [WORDS]", "the system trash: restore items or delete them for good"),
    "undolist": ("undolist", "list what can be undone and redone"),
    "unselect": ("unselect PATTERN", "untag matching items"),
    "z": ("z KEYWORDS", "jump with zoxide"),
    "zi": ("zi [KEYWORDS]", "pick a folder from zoxide's list"),
    "zip": ("zip [NAME]", "pack the tagged items into NAME.zip here"),
    "vterm": ("vterm [SHELL|off]", "a terminal in the other pane: wsl, wsl:NAME, pwsh, cmd, bash…; off closes it"),
    "map": ("map [LHS [RHS]]", "map keys to keys in normal and visual mode (:nmap / :vmap: one mode); "
                               "without RHS shows the maps"),
    "noremap": ("noremap LHS RHS", "map keys to the built-in keys, not through other maps (:nnoremap, :vnoremap)"),
    "unmap": ("unmap LHS", "remove a map (:nunmap, :vunmap)"),
    "mapclear": ("mapclear", "remove every map made with :map"),
    "source": ("source [FILE]", "run the : commands in FILE (default: the vimcommanderrc next to the config)"),
    "let": ('let mapleader = "X"', "set <leader> for the maps made after it"),
    "only": ("only", "only this pane (Ctrl-w o)"),
    "vsplit": ("vsplit [PATH]", "both panes, the other one in this folder or PATH (Ctrl-w v; :split too)"),
    "close": ("close", "close this pane: only the other one (Ctrl-w c)"),
    "command": ("command [NAME REPLACEMENT]", "a : command for this session (the config's \"commands\" last)"),
    "delcommand": ("delcommand NAME", "remove a :command"),
    "mount": ("mount", "mount this SSH folder with sshfs in the terminal pane (WSL on Windows)"),
    "umount": ("umount", "unmount the SSH folder that :mount mounted"),
}
ALIASES = {
    "e": "edit", "noh": "nohlsearch", "quit": "q", "x": "q", "wq": "q", "qall": "qa",
    "quitall": "qa", "wqa": "qa", "xa": "qa", "reg": "registers", "di": "registers",
    "display": "registers", "tabc": "tabclose", "br": "bulkrename", "diff": "compare",
    "res": "results", "link": "linkpanes", "mks": "mksession", "term": "terminal",
    "cn": "cnext", "cp": "cprevious", "cN": "cprevious", "cprev": "cprevious",
    "d": "delete", "y": "yank", "pu": "put", "norm": "normal", "mes": "messages", "pw": "pwd",
    "nmap": "map", "vmap": "map", "xmap": "map", "nnoremap": "noremap", "vnoremap": "noremap",
    "xnoremap": "noremap", "nunmap": "unmap", "vunmap": "unmap", "xunmap": "unmap", "so": "source",
    "split": "vsplit",
}


def needs_argument(name: str) -> bool:
    usage = COMMANDS.get(name, ("", ""))[0]
    return any(part.isupper() and "[" not in part for part in usage.split()[1:])


# Vim's shortest forms: a command may be typed as any part of its name that starts
# with these letters (:se = :set, :tabn = :tabnext); they win over other commands
# starting the same way. Others need as many letters as make them unique.
SHORTEST = {
    "set": "se", "shell": "sh", "edit": "e", "help": "h", "quit": "q", "qall": "qa",
    "delete": "d", "yank": "y", "put": "pu", "normal": "norm", "messages": "mes", "pwd": "pw",
    "nohlsearch": "noh", "registers": "reg", "display": "di", "marks": "marks", "delmarks": "delm",
    "undo": "u", "redo": "red", "undolist": "undol", "sort": "sor", "filter": "filt",
    "grep": "gr", "find": "fin", "terminal": "ter", "mksession": "mks", "cd": "cd",
    "tabnew": "tabnew", "tabnext": "tabn", "tabprevious": "tabp", "tabclose": "tabc", "tabonly": "tabo",
    "tabfirst": "tabfir", "tablast": "tabl", "tabmove": "tabm", "tabs": "tabs",
    "cnext": "cn", "cprevious": "cp", "copen": "cope", "cclose": "ccl", "clist": "cl",
    "colder": "col", "cnewer": "cnew", "cdo": "cdo",
    "noremap": "no", "nnoremap": "nn", "vnoremap": "vn", "xnoremap": "xn", "nmap": "nm", "vmap": "vm",
    "xmap": "xm", "unmap": "unm", "nunmap": "nun", "vunmap": "vu", "xunmap": "xu", "mapclear": "mapc",
    "source": "so", "let": "let", "only": "on", "vsplit": "vs", "split": "sp", "close": "clo",
    "command": "com", "delcommand": "delc",
}


def resolve(typed: str, names) -> tuple[str | None, list[str]]:
    """The command meant by `typed` among `names`, like vim: the exact name, else
    one whose shortest form it reaches (SHORTEST), else the only name starting with
    it. Returns (name, []) or (None, the names it could mean)."""
    if typed in names:
        return typed, []
    for name, short in SHORTEST.items():
        if name in names and typed.startswith(short) and name.startswith(typed):
            return name, []
    found = sorted(n for n in names if n.startswith(typed))
    return (found[0], []) if len(found) == 1 else (None, found)
