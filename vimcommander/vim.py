"""Modal key-sequence engine.

Independent of Qt so it can be unit-tested on its own. Keys are fed in as
strings: printable characters as-is ("j", "G", ":") and special keys in vim
notation ("<CR>", "<Esc>", "<C-d>", "<F5>").

A binding value is an action name, optionally with a fixed argument after a
colon ("bar:3"). Bindings in the *_ARG_KEYS tables take the next typed key as
their argument, like vim's `ma` or `fx`.

User key maps (from the settings file) are merged in with user_bindings().
"""

from dataclasses import dataclass

NORMAL = "NORMAL"
VISUAL = "VISUAL"

# Keys that move the cursor or scroll; valid in both normal and visual mode.
MOTIONS = {
    "j": "down", "<Down>": "down",
    "k": "up", "<Up>": "up",
    "gg": "first", "<Home>": "first",
    "G": "last", "<End>": "last",
    "<C-d>": "half_down", "<C-u>": "half_up",
    "<C-f>": "page_down", "<PgDown>": "page_down",
    "<C-b>": "page_up", "<PgUp>": "page_up",
    "H": "screen_top", "M": "screen_middle", "L": "screen_bottom",
    "zz": "scroll_center", "zt": "scroll_top", "zb": "scroll_bottom",
    "n": "search_next", "N": "search_prev",
    ";": "repeat_find", ",": "repeat_find_back",
    "{": "group_prev", "}": "group_next",  # start of the previous / next group of names
    "%": "percent",  # 50% = halfway down
    "<C-e>": "scroll_line_down", "<C-y>": "scroll_line_up",
    "w": "item_next", "b": "item_prev",  # one item; in the thumbnail grid j / k move a whole row
}

MOTION_ARG_KEYS = {
    "f": "find_char", "F": "find_char_back",
}

# Operators: y / d followed by a motion act on the rows from the cursor to
# where the motion lands, like vim's yj, d3j, yG, ygg. A count may go before
# the operator, before the motion, or both (2d3j = 6 rows). n/N, ; and , are
# left out because yn / yf already mean "yank name" / "yank path".
OPERATORS = {"y": "yank", "d": "cut"}
OPERATOR_MOTIONS = {k: v for k, v in MOTIONS.items()
                    if k not in ("n", "N", ";", ",", "zz", "zt", "zb", "<C-e>", "<C-y>")}

NORMAL_KEYS = {
    **MOTIONS,
    "h": "parent", "<BS>": "parent", "<Left>": "parent", "-": "parent",
    "l": "open", "<CR>": "open", "<Right>": "open",
    "<C-o>": "back", "<C-i>": "forward",
    "gh": "home", "~": "home",
    "<Tab>": "switch_pane", "<S-Tab>": "switch_pane",
    "<C-w>w": "switch_pane", "<C-w><C-w>": "switch_pane",
    "<C-w>h": "left_pane", "<C-w>l": "right_pane",
    "<A-F1>": "drives:1", "<A-F2>": "drives:2",
    "<C-t>": "new_tab", "gt": "next_tab", "gT": "prev_tab", "gz": "zoxide_pick",
    "t": "tag", "<Space>": "tag", "T": "invert_tags", "+": "select_prompt",
    "v": "visual", "V": "visual", "gv": "reselect",
    "yy": "yank", "Y": "yank", "dd": "cut", "p": "paste", ".": "repeat",
    "yf": "yank_path", "yd": "yank_dir", "yn": "yank_name",
    "<F3>": "view", "<F4>": "edit", "S": "shell",
    "<F5>": "copy_other", "<F6>": "move_other", "<A-F5>": "pack", "<A-F9>": "unpack", "gb": "branch",
    "<Del>": "trash", "<F8>": "trash", "<S-Del>": "delete", "u": "undo", "<C-r>": "redo",
    "<C-^>": "alternate", "ZZ": "quit", "ZQ": "quit", "<C-g>": "info", "gx": "open_system",
    "cw": "rename", "<F2>": "rename", "cW": "rename_stem",
    "<F7>": "mkdir",
    "za": "toggle_hidden",
    "<C-q>": "quickview", "gs": "dir_size", "gS": "all_dir_sizes", "gG": "graph_window",
    "<C-S-p>": "palette", "<C-p>": "files", "gr": "activity",
    "g/": "grep", "gf": "goto_file", "gd": "diff", "zs": "shelf_add", "zS": "shelf",
    "zi": "grid", "<C-`>": "terminal",
    "<Menu>": "context_menu", "<S-F10>": "context_menu",
    "<C-l>": "reload",
    "/": "search", "?": "search_back", "*": "search_like", "#": "search_like_back",
    "=": "filter",
    ":": "command",
    "<F1>": "help",
    "<Esc>": "escape",
    **{f"g{i}": f"bar:{i}" for i in range(1, 10)},
}
NORMAL_KEYS.update({op + key: f"{name}_motion:{motion}"
                    for op, name in OPERATORS.items()
                    for key, motion in OPERATOR_MOTIONS.items()
                    if op + key not in NORMAL_KEYS})

NORMAL_ARG_KEYS = {
    **MOTION_ARG_KEYS,
    "m": "set_mark", "'": "goto_mark", "`": "goto_mark",
    "@": "at",  # @a plays macro a, @@ the last one, @: repeats the last ':' command
    "q": "record",  # qa records a macro into a; q stops
}

VISUAL_KEYS = {
    **MOTIONS,
    "y": "yank", "d": "cut",
    "o": "swap_anchor",
    "<F3>": "view", "<F4>": "edit",
    "<F5>": "copy_other", "<F6>": "move_other", "<A-F5>": "pack", "<A-F9>": "unpack",
    "<Del>": "trash", "<F8>": "trash", "<S-Del>": "delete",
    "gs": "dir_size", "<Menu>": "context_menu", "<S-F10>": "context_menu",
    "zs": "shelf_add", "gd": "diff",
    "<Tab>": "switch_pane",
    "v": "escape", "V": "escape", "<Esc>": "escape",
    ":": "command",
}

VISUAL_ARG_KEYS = MOTION_ARG_KEYS


def user_bindings(user: dict[str, dict[str, str]]):
    """Default (bindings, arg_bindings) with the user's key maps applied.

    `user` is {"normal": {...}, "visual": {...}}. A value is an action name
    ("edit", "bar:3"), a ':' command (":cd ~", ":!code %d") or "" to unbind.
    """
    bindings = {NORMAL: dict(NORMAL_KEYS), VISUAL: dict(VISUAL_KEYS)}
    arg_bindings = {NORMAL: dict(NORMAL_ARG_KEYS), VISUAL: dict(VISUAL_ARG_KEYS)}
    for mode in (NORMAL, VISUAL):
        for key, value in (user.get(mode.lower()) or {}).items():
            bindings[mode].pop(key, None)
            arg_bindings[mode].pop(key, None)
            if value:
                bindings[mode][key] = "ex:" + value[1:] if value.startswith(":") else value
    return bindings, arg_bindings


@dataclass
class Action:
    name: str
    count: int | None  # None when no count was typed
    arg: str | None = None
    register: str | None = None  # from a "x prefix, as in "ayy

    def n(self, default: int = 1) -> int:
        return self.count if self.count is not None else default


def _action(value: str, count: int | None, arg: str | None = None) -> Action:
    name, _, fixed = value.partition(":")
    return Action(name, count, arg if arg is not None else (fixed or None))


class KeyEngine:
    def __init__(self, bindings: dict[str, dict[str, str]] | None = None,
                 arg_bindings: dict[str, dict[str, str]] | None = None):
        self.bindings = bindings or {NORMAL: NORMAL_KEYS, VISUAL: VISUAL_KEYS}
        self.arg_bindings = arg_bindings or {NORMAL: NORMAL_ARG_KEYS, VISUAL: VISUAL_ARG_KEYS}
        self.mode = NORMAL
        self.count = ""
        self.inner = ""  # count typed after an operator: the 3 in d3j
        self.pending = ""
        self.awaiting: str | None = None  # binding waiting for its argument key
        self.register: str | None = None  # "x typed before the command
        self._register_next = False  # '"' typed, the register name comes next

    def reset(self) -> None:
        self.count = ""
        self.inner = ""
        self.pending = ""
        self.awaiting = None
        self.register = None
        self._register_next = False

    @property
    def pending_display(self) -> str:
        reg = '"' + (self.register or "") if self.register or self._register_next else ""
        return self.count + reg + self.pending + self.inner

    def _done(self, action: Action) -> Action:
        action.register = self.register
        self.reset()
        return action

    def _count(self) -> int | None:
        if not self.count and not self.inner:
            return None
        return int(self.count or 1) * int(self.inner or 1)

    def feed(self, key: str) -> Action | None:
        """Feed one key. Returns an Action once a full binding is typed."""
        if self._register_next:
            self._register_next = False
            if len(key) == 1 and (key.isalnum() or key in '"+*'):
                self.register = key
            else:
                self.reset()
            return None
        if key == '"' and not self.pending and not self.awaiting:
            self._register_next = True
            return None
        if self.awaiting:
            value, count = self.awaiting, self._count()
            if len(key) != 1:  # <Esc> or another special key cancels
                self.reset()
                return None
            return self._done(_action(value, count, key))

        table = self.bindings.get(self.mode, {})
        arg_table = self.arg_bindings.get(self.mode, {})
        if len(key) == 1 and key.isdigit():
            if not self.pending and (key != "0" or self.count):
                self.count += key
                return None
            if (self.mode == NORMAL and self.pending in OPERATORS
                    and (key != "0" or self.inner)):
                self.inner += key
                return None
        seq = self.pending + key
        if seq in table:
            return self._done(_action(table[seq], self._count()))
        if seq in arg_table:
            self.pending = seq
            self.awaiting = arg_table[seq]
            return None
        if any(k.startswith(seq) for k in (*table, *arg_table)):
            self.pending = seq
            return None
        self.reset()
        return None
