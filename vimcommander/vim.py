"""Modal key-sequence engine.

Independent of Qt so it can be unit-tested on its own. Keys are fed in as
strings: printable characters as-is ("j", "G", ":") and special keys in vim
notation ("<CR>", "<Esc>", "<C-d>", "<F5>").

A binding value is an action name, optionally with a fixed argument after a
colon ("bar:3"). :map / :noremap bind keys to other keys: "remap:<rhs>" and
"keys:<rhs>". When one binding is the start of a longer one (a user map like
",x" next to ","), the engine waits; flush() runs the shorter one after the
'timeoutlen' delay, and a key that doesn't continue it runs it and is left in
`leftover` to be fed again. Bindings in the *_ARG_KEYS tables take the next typed key as
their argument, like vim's `ma` or `fx`.

User key maps (from the settings file) are merged in with user_bindings().
"""

import re
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
# left out because yn / yf already mean "yank name" / "yank path". gU / gu / g~
# change the case of the names (gUU, gU3j, g~G).
OPERATORS = {"y": "yank", "d": "cut", "gU": "upper", "gu": "lower", "g~": "toggle_case"}
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
    "<C-w>t": "vterm",  # a terminal in the other pane
    "<C-w>m": "playlist",  # the music player's playlist in the other pane
    "<C-w>o": "only_pane", "<C-w><C-o>": "only_pane", "<C-w>x": "swap_panes", "<C-w><C-x>": "swap_panes",
    "<C-w>=": "equal_panes", "<C-w>|": "maximize_pane",
    "<C-w>>": "resize_pane:1", "<C-w><": "resize_pane:-1",  # 3 Ctrl-w > = 15% wider
    "<C-w>p": "switch_pane", "<C-w><C-p>": "switch_pane", "<C-w>r": "swap_panes", "<C-w>R": "swap_panes",
    "<C-w>c": "close_pane", "<C-w>q": "close_pane",  # only the other pane, like closing this window
    "<C-w>s": "split_pane", "<C-w>v": "split_pane",  # both panes, the other one in this folder
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
    "I": "rename_at:start", "A": "rename_at:end", "cc": "rename_at:clear", "C": "rename_at:stem",
    "gUU": "upper", "gUgU": "upper", "guu": "lower", "gugu": "lower", "g~~": "toggle_case", "g~g~": "toggle_case",
    "<C-a>": "increment", "<C-x>": "decrement",  # the number in the name; g: 1, 2, 3... times
    "g<C-a>": "increment:g", "g<C-x>": "decrement:g",
    "<F7>": "mkdir",
    "za": "toggle_hidden", "zo": "show_hidden:on", "zm": "show_hidden:off",
    "<C-q>": "quickview", "gs": "dir_size", "gS": "all_dir_sizes", "gG": "graph_window",
    "ga": "dir_size", "gA": "all_dir_sizes", "DD": "delete",  # vifm's keys
    "<C-S-p>": "palette", "<C-p>": "files", "gr": "activity",
    "g/": "grep", "gf": "goto_file", "gd": "diff", "zs": "shelf_add", "zS": "shelf",
    "zi": "grid", "zp": "plot", "zv": "model_keys", "<C-`>": "terminal",
    "gm": "play", "gM": "play_pause", "]m": "play_next", "[m": "play_prev",  # music
    "<MediaPlay>": "play_pause", "<MediaNext>": "play_next", "<MediaPrev>": "play_prev", "<MediaStop>": "play_stop",
    "]q": "qf_next", "[q": "qf_prev", "&": "repeat_substitute",  # the next / previous item of the result list, in its folder
    "<Menu>": "context_menu", "<S-F10>": "context_menu",
    "<C-l>": "reload",
    "/": "search", "?": "search_back", "*": "search_like", "#": "search_like_back",
    "=": "filter",
    ":": "command",
    "<F1>": "help",
    "<Esc>": "escape", "<C-c>": "escape",
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
    "zs": "shelf_add", "gd": "diff", "gm": "play",
    "<C-a>": "increment", "<C-x>": "decrement", "g<C-a>": "increment:g", "g<C-x>": "decrement:g",
    "x": "cut", "c": "rename", "s": "rename",
    "U": "upper", "u": "lower", "~": "toggle_case", "gU": "upper", "gu": "lower", "g~": "toggle_case",
    "<Tab>": "switch_pane",
    "v": "escape", "V": "escape", "<Esc>": "escape", "<C-c>": "escape",
    ":": "command",
}

VISUAL_ARG_KEYS = MOTION_ARG_KEYS


# Like vim's 'langmap': with a Ukrainian or Russian keyboard layout, the keys
# still mean what they say on a US keyboard (о = j, пп = gg, Ж = :). Only
# non-ASCII characters are mapped, so the Latin layout is never affected.
LANGMAPS = {
    "cyrillic": dict(zip("йцукенгшщзхїъфіывапролджєэячсмитьбюёґ"
                         "ЙЦУКЕНГШЩЗХЇЪФІЫВАПРОЛДЖЄЭЯЧСМИТЬБЮЁҐ№",
                         "qwertyuiop[]]assdfghjkl;''zxcvbnm,.``"
                         "QWERTYUIOP{}}ASSDFGHJKL:\"\"ZXCVBNM<>~~#")),
}

# Argument keys that stay as typed: f/F jump to a name starting with that character.
LITERAL_ARGS = {"find_char", "find_char_back"}


# Key names as vim writes them, in any case: <cr>, <ESC>, <Bar>.
KEY_ALIASES = {n.lower(): n for n in (
    "<CR>", "<Esc>", "<Tab>", "<S-Tab>", "<BS>", "<Del>", "<Space>", "<Up>", "<Down>", "<Left>",
    "<Right>", "<Home>", "<End>", "<PgUp>", "<PgDown>", "<Menu>", "<MediaPlay>", "<MediaNext>",
    "<MediaPrev>", "<MediaStop>", *(f"<F{i}>" for i in range(1, 13)))}
KEY_ALIASES.update({"<return>": "<CR>", "<enter>": "<CR>", "<escape>": "<Esc>", "<backspace>": "<BS>",
                    "<delete>": "<Del>", "<pageup>": "<PgUp>", "<pagedown>": "<PgDown>",
                    "<bar>": "|", "<lt>": "<", "<bslash>": "\\"})
MODIFIER_ORDER = "CAS"  # <C-A-S-F5>, as key_name() writes them
KEY_TOKEN = re.compile(r"<[^<>\s]+>|.")  # one key in a sequence


def split_keys(keys: str, leader: str = "\\") -> list[str]:
    """Vim key notation as a list of keys in the engine's names:
    "<leader>d<cr>" -> ["\\", "d", "<CR>"], "<c-W>o" -> ["<C-w>", "o"]."""
    out = []
    for token in KEY_TOKEN.findall(keys):
        low = token.lower()
        if low == "<leader>":
            out.append(leader)
        elif low in KEY_ALIASES:
            out.append(KEY_ALIASES[low])
        elif m := re.fullmatch(r"<((?:[csam]-)+)(.+)>", token, re.IGNORECASE):
            mods = {c if c != "M" else "A" for c in m.group(1).upper().replace("-", "")}
            key = m.group(2)
            if len(key) == 1:
                key = key.lower() if key.isalpha() and "C" in mods else key
                order = "CSA"  # <C-S-p>, as key_name() writes letters
            else:
                key = KEY_ALIASES.get(f"<{key.lower()}>", f"<{key}>")[1:-1]
                order = MODIFIER_ORDER
            out.append("<" + "".join(c + "-" for c in order if c in mods) + key + ">")
        else:
            out.append(token)
    return out


def _split(spec: str, sep: str) -> list[str]:
    """Split at `sep`, where a backslash escapes the next character."""
    parts, cur, i = [], "", 0
    while i < len(spec):
        if spec[i] == "\\" and i + 1 < len(spec):
            cur += spec[i:i + 2]
            i += 2
            continue
        if spec[i] == sep:
            parts.append(cur)
            cur = ""
        else:
            cur += spec[i]
        i += 1
    parts.append(cur)
    return parts


def _unescape(s: str) -> str:
    out, i = "", 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            i += 1
        out += s[i]
        i += 1
    return out


def parse_langmap(spec: str) -> dict[str, str]:
    """A 'langmap' value as {typed: meant}. The vim format: comma-separated
    parts, each "FROM;TO" (two equally long lists) or pairs ("аfбd"); a part
    may also be a built-in name ("cyrillic"). Raises ValueError if malformed."""
    result: dict[str, str] = {}
    for part in _split(spec, ","):
        if not part.strip():
            continue
        if part.strip().lower() in LANGMAPS:
            result.update(LANGMAPS[part.strip().lower()])
            continue
        halves = _split(part, ";")
        if len(halves) % 2 == 1 and len(halves) > 1:
            raise ValueError(f"langmap: odd number of ';' in {part!r}")
        if len(halves) == 1:  # pairs
            chars = _unescape(part)
            if len(chars) % 2:
                raise ValueError(f"langmap: {part!r} has an odd number of characters")
            pairs = list(zip(chars[::2], chars[1::2]))
        else:
            pairs = []
            for src, dst in zip(halves[::2], halves[1::2]):
                src, dst = _unescape(src), _unescape(dst)
                if len(src) != len(dst):
                    raise ValueError(f"langmap: {src!r} and {dst!r} differ in length")
                pairs += zip(src, dst)
        for a, b in pairs:
            if a.isascii():
                raise ValueError(f"langmap: {a!r} is ASCII; only other characters can be mapped")
            result[a] = b
    return result


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


def _continues(binding: str, seq: str) -> bool:
    """Whether `binding` is `seq` plus more keys ("<C-w><C-w>" doesn't continue "<C-w><")."""
    if binding == seq or not binding.startswith(seq):
        return False
    return not seq.endswith("<") or KEY_TOKEN.findall(binding)[:len(KEY_TOKEN.findall(seq))] == KEY_TOKEN.findall(seq)


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
        self.langmap: dict[str, str] = {}  # parse_langmap(): typed character -> meant
        self.leftover: str | None = None  # a key that ended a waiting binding: feed it again

    def translate(self, key: str) -> str:
        """Apply the langmap to a typed key: "о" -> "j", "<C-в>" -> "<C-d>"."""
        if not self.langmap or key.isascii():
            return key
        if len(key) == 1:
            return self.langmap.get(key, key)
        for prefix in ("<C-S-", "<C-"):
            if key.startswith(prefix) and len(key) == len(prefix) + 2:
                meant = self.langmap.get(key[-2], key[-2]).lower()
                return f"{prefix}{meant}>" if meant.isascii() and meant.isalpha() else key
        return key

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
        if self.awaiting not in LITERAL_ARGS:
            key = self.translate(key)
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
        longer = any(_continues(k, seq) for k in (*table, *arg_table))
        if seq in table:
            if longer:  # ",x" is mapped too: wait for the next key or the timeout
                self.pending = seq
                return None
            return self._done(_action(table[seq], self._count()))
        if seq in arg_table:
            self.pending = seq
            self.awaiting = arg_table[seq]
            return None
        if longer:
            self.pending = seq
            return None
        if self.pending in table:  # the shorter binding it waited on, then this key again
            self.leftover = key
            return self._done(_action(table[self.pending], self._count()))
        self.reset()
        return None

    def flush(self) -> Action | None:
        """The 'timeoutlen' delay ran out: run the binding typed so far, if it is
        a whole one (it waited because a longer binding starts with it)."""
        table = self.bindings.get(self.mode, {})
        if self.pending in table and not self.awaiting:
            return self._done(_action(table[self.pending], self._count()))
        return None
