import re
import io
import inspect
import doctest

from gi.repository import Geany
from gi.repository import Gtk

try:
    import jedi
except ImportError:
    Geany.msgwin_status_add_string("jedi not found, python auto-completion not possible.")
    jedi = None
    Script = None
else:
    jedi.settings.case_insensitive_completion = False
    Script = jedi.Script


try:
    import doq
except ImportError:
    doq = None
else:
    from doq.cli import get_lines, get_template_path, generate_docstrings


magic_method_re = re.compile(r"^__(\w)+(_\w+)?__$")

name_match = re.compile(r"^[A-Za-z_]\w+")

doc_defs = ("function", "class", "def", "async def")
import_kw = ("import ", "from ")
ignore_kind = {inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL}
code_char_check = {
    "\r",
    "\n",
    " ",
    "\t",
    "\v",
    "\f",
    ">",
    "/",
    "{",
    "[",
    '"',
    "'",
    "}",
    ":",
    ")",
}

DEFAULT_DOCSTRING = "google"
DOCSTRING_TYPES = (DEFAULT_DOCSTRING, "sphinx", "numpy")

refactor_actions = {
    "Extract Variable": ("extract_variable", 0),
    "Extract Function": ("extract_function", 0),
    "Do Inline": ("inline", None),
    "Rename Variable": ("rename", True),
}
refactor_nn = "new_name"
refactor_opt = {
    "action": refactor_actions,
    refactor_nn: ["New Name", "{NEW_VAR}"],
    "until_loc": ["Location Until", "{LINE,COL}"],
}
refactor_dlg = "Refactor Code: '{0}''"


class JediRefactorDialog(Gtk.Dialog):
    """JediRefactorDialog."""

    cur_action = None

    def __init__(self, parent, title, button_text):
        """__init__.

        Args:
            parent:
            title:
            button_text:
        """
        super().__init__(title, parent, Gtk.DialogFlags.DESTROY_WITH_PARENT)
        self.add_buttons(
            button_text,
            Gtk.ResponseType.OK,
        )

    def set_and_show(self, gettext_func):
        """set_and_show.

        Args:
            gettext_func:
        """
        self.box = self.get_content_area()
        for name, actions in refactor_opt.items():
            if isinstance(actions, list):
                entry = Gtk.Entry()
                entry.set_name(name)
                entry.set_text(actions[1])
                entry.set_tooltip_text(gettext_func(actions[0]))
                self.box.add(entry)
            else:
                combo = Gtk.ComboBoxText()
                combo.set_name(name)
                for nam, _ in actions.items():
                    if self.cur_action is None:
                        self.cur_action = _[0]
                    combo.append_text(gettext_func(nam))
                combo.set_active(0)
                combo.connect("changed", self.on_action_combo_changed)
                self.box.add(combo)
        self.show_all()

    def on_action_combo_changed(self, combo):
        """on_action_combo_changed.

        Args:
            combo:
        """
        text = combo.get_active_text()
        action = refactor_actions.get(text)
        if not action:
            return
        self.cur_action = action[0]
        childrens = self.box.get_children()
        if action[1] is None:
            for child in childrens:
                if isinstance(child, Gtk.Entry):
                    child.hide()
        elif action[1]:
            for child in childrens:
                if child.get_name() == "until_loc":
                    child.hide()
        else:
            for child in childrens:
                child.show()


def generate_for_docstring(contents, docstring_name=DEFAULT_DOCSTRING, indent_info=None):
    """generate_for_docstring.

    Args:
        contents:
        docstring_name:
        indent_info:
    """
    text = io.StringIO(contents)
    lines = get_lines(text, 1, 0)
    path = get_template_path(
        template_path=None,
        formatter=docstring_name.lower(),
    )
    docstrings = generate_docstrings(code=lines, path=path)
    if len(docstrings) == 0:
        return None
    outputter = doq.StringOutptter()
    return outputter.format(
        lines=lines,
        docstrings=docstrings,
        indent=indent_info,
    )


def on_refactor_done(future):
    """on_refactor_done.

    Args:
        future:
    """
    result = future.result()
    if result:
        result.apply()


def start_jedi_refactor(content, file_name, line, column, action=None, action_args=None):
    """start_jedi_refactor.

    Args:
        content:
        file_name:
        line:
        column:
        action:
        action_args:
    """
    params = {}
    for idx, name in enumerate(action_args):
        if name is None:
            continue
        if idx == 0:
            _match = name_match.fullmatch(name)
            params["new_name"] = _match.group() if _match else None
        else:
            try:
                until_line, until_col = name.split(",")
                until_line, until_col = int(until_line), int(until_col)
            except (IndexError, TypeError, ValueError):
                pass
            else:
                params["until_line"] = until_line
                params["until_column"] = until_col
    try:
        ref = getattr(Script(content, path=file_name), action)(line, column, **params)
    except (AttributeError, jedi.RefactoringError, TypeError) as error:
        Geany.msgwin_status_add_string(str(error))
        return
    else:
        return ref


def show_docstring(docstring):
    """show_docstring.

    Args:
        docstring:
    """
    Geany.msgwin_clear_tab(Geany.MessageWindowTabNum.COMPILER)
    if not docstring:
        return
    if ">>>" in docstring:
        try:
            dt_parse = doctest.DocTestParser().parse(docstring)
        except ValueError:
            pass
        else:
            desc = []
            for doc in dt_parse:
                if not isinstance(doc, str):
                    continue
                sr_doc = doc.strip()
                if not sr_doc or sr_doc == "\n":
                    continue
                desc.append(sr_doc)
            docstring = "\n".join(desc)
    Geany.msgwin_compiler_add_string(Geany.MsgColors.BLACK, "Doc:\n\n{0}".format(docstring))
    Geany.msgwin_switch_tab(Geany.MessageWindowTabNum.COMPILER, False)
