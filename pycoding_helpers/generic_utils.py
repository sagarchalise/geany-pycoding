import re
import ast
import inspect

from gi.repository import Geany, Gtk

try:
    import jedi
except ImportError:
    print("jedi not found, python auto-completion not possible.")
    jedi = None
    Script = None
else:
    jedi.settings.case_insensitive_completion = False
    Script = jedi.Script

try:
    import parso
except ImportError:
    parso = None

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

common_doc_kw = {"Yields", "Returns", "Raises"}
DEFAULT_DOCSTRING = "google"
doc_map_list = {
    "reST": {
        "templates": {"maps": ":{3} {1}{0}: {2}", "type": ":{2}type {1}: {0}",},
        "args": "param",
        "attrs": "var",
    },
    DEFAULT_DOCSTRING: {
        "templates": {"maps": "{0} {1}: {2}", "type": "({0}{1})",},
        "args": "Args:",
        "attrs": "Attributes:",
        "kargs": "Keyword Args:",
    },
    "numpy": {
        "templates": {"maps": "{0} {1}", "type": ": {0}{1}",},
        "args": "Parameters",
        "attrs": "Attributes",
    },
}

DOCSTRING_TYPES = doc_map_list.keys()
start_ds = "About *{0}*."


default_pref = " DEFAULT: "
cursor_marker = "__GEANY_CURSOR_MARKER__"
exp = cursor_marker + "."

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
    cur_action = None

    def __init__(self, parent, title, button_text):
        super().__init__(title, parent, Gtk.DialogFlags.DESTROY_WITH_PARENT)
        self.add_buttons(
            button_text, Gtk.ResponseType.OK,
        )

    def set_and_show(self, gettext_func):
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


def safe_determine_default_value(default):
    try:
        string = default.value
    except AttributeError:
        for child in default.children:
            return cursor_marker, child.value
    if string == "None":
        return cursor_marker, string
    try:
        return ast.literal_eval(string).__class__.__name__, string
    except ValueError:
        try:
            if string.startswith("set(") or isinstance(
                ast.literal_eval(string.replace("{", "[").replace("}", "]")), list
            ):
                return "set", string
        except ValueError:
            return cursor_marker, string


ignore_dec = {"staticmethod", "classmethod", "property"}


def docstring_template_data(docstring_name, node, cur_pos, indent_info):
    is_restxt = docstring_name in {"reST", "rest"}
    if is_restxt:
        docstring_name = "reST"
    is_numpy = docstring_name == "numpy"
    notes = ["..note::" if is_restxt else "Notes"]
    if is_numpy:
        notes.append("-----")
    docs = []
    maps = doc_map_list.get(docstring_name)
    docs.append("'''{0}".format(start_ds.format(node.name.value)))
    is_google = docstring_name == DEFAULT_DOCSTRING
    templates = maps["templates"]
    is_star_cnt = False
    ret_type = None
    type_extra = ["", ""]
    new_line = False

    def get_maps_info(name, exp, header, default_type, type_extra=None):
        type_map = templates["type"]
        if type_extra is None:
            type_extra = ("", "")
        default_type = type_map.format(default_type, *type_extra)
        if is_restxt:
            yield templates["maps"].format(name, "", exp, header)
            yield default_type
        elif is_numpy:
            yield templates["maps"].format(name, default_type)
            yield indent_info + exp
        else:
            yield indent_info + templates["maps"].format(name, default_type, exp)

    try:
        params = node.get_params()
        remove_self = isinstance(node.parent.parent, parso.python.tree.Class)
    except AttributeError:
        super_args = node.get_super_arglist()
        if super_args:
            notes.append(
                indent_info + "* Parent Classes: " + ",".join(k.name.value for k in super_args)
            )
        header = maps["attrs"]
        start = cur_pos[0] + 1
        type_extra = ["", "var"]
        while start - cur_pos[0] < 100 and node.end_pos[0] > start:
            leaf = node.get_leaf_for_position((start, cur_pos[1] + 4))
            start += 1
            if not leaf:
                continue
            if leaf.value == "def" or leaf.value == "pass":
                break
            try:
                default_type, default = safe_determine_default_value(leaf.parent.get_rhs())
            except AttributeError:
                name = leaf.parent.name.value
                default_type = leaf.value
                default = None
            else:
                name = leaf.value
            if default:
                default = exp + default_pref + default
            else:
                default = exp
            if is_restxt:
                type_extra[0] = name
            if not new_line:
                docs.append("\n")
                if is_numpy or is_google:
                    docs.append(header)
                    if is_numpy:
                        docs.append("-" * len(header))
            new_line = True
            for val in get_maps_info(name, default, header, default_type, type_extra):
                docs.append(val)
    else:
        header = maps.get("args")
        kargs_header = maps.get("kargs")
        key = None
        for p in params:
            name = p.name.value
            if remove_self and name == "self":
                continue
            if p.star_count and not is_star_cnt:
                is_star_cnt = True
            default = p.default
            if default:
                default_type, default_str = safe_determine_default_value(default)
                default = default_pref + default_str
            else:
                default = ""
            if p.annotation:
                try:
                    default_type = p.annotation.value
                except AttributeError:
                    default_type = p.annotation.get_code()
            elif not default:
                default_type = cursor_marker

            add_star = ""
            if is_star_cnt:
                if p.star_count == 1:
                    add_star = "*"
                    default = "VARARGS"
                else:
                    if is_google:
                        key = True if key is None else key
                    if p.star_count == 2:
                        add_star = "**"
                        default = "KEYWORD VARARGS"
                    else:
                        add_star = ""
            if not new_line:
                docs.append("\n")
                if is_numpy or is_google:
                    docs.append(header)
                    if is_numpy:
                        docs.append("-" * len(header))
            new_line = True
            if key:
                docs.append("\n")
                docs.append(kargs_header)
                key = False
            if is_restxt:
                type_extra[0] = name
            elif not p.annotation and (default or add_star):
                type_extra[0] = ",optional"
            default = exp + default
            for val in get_maps_info(
                add_star + name, default, header, default_type, type_extra=type_extra
            ):
                docs.append(val)
        if node.is_generator():
            notes.append(indent_info + "* Is a generator.")
    decorator_list = node.get_decorators()
    if decorator_list:
        notes.append(
            indent_info
            + "* Decorators Used: "
            + ",".join(n.name.value for n in decorator_list if n.name.value not in ignore_dec)
        )
    extra = "-------" if is_numpy else ":"
    try:
        new_line = False
        header = "raises" if is_restxt else "Raises"
        for leaf in node.iter_raise_stmts():
            rtype, val = safe_determine_default_value(leaf.children[1])
            if val == "None":
                continue
            if not new_line and (is_numpy or is_google):
                docs.append("\n")
                if is_numpy:
                    docs.append(header)
                    docs.append(extra)
                else:
                    docs.append(header + extra)
            new_line = True
            if is_restxt:
                docs.append(":raises " + val + ": " + rtype)
            elif is_numpy:
                docs.append(val)
                docs.append(indent_info + rtype)
            else:
                docs.append(indent_info + val + " : " + rtype)
    except AttributeError:
        pass
    else:
        if new_line and not is_restxt:
            docs.append("\n")
    try:
        new_line = False
        header = "yields" if is_restxt else "Yields"
        for leaf in node.iter_yield_exprs():
            rtype, val = safe_determine_default_value(leaf.children[1])
            if val == "None":
                continue
            if not new_line and (is_numpy or is_google):
                docs.append("\n")
                if is_numpy:
                    docs.append(header)
                    docs.append(extra)
                else:
                    docs.append(header + extra)
            new_line = True
            if is_restxt:
                docs.append(":yields: " + val)
                docs.append(":ytype: " + rtype)
            elif is_numpy:
                docs.append(rtype)
                docs.append(indent_info + val)
            else:
                docs.append(indent_info + rtype + " : " + val)
    except AttributeError:
        pass
    else:
        if not is_restxt and new_line:
            docs.append("\n")
    try:
        new_line = False
        header = "returns" if is_restxt else "Returns"
        if node.annotation:
            if not new_line and (is_numpy or is_google):
                docs.append("\n")
                if is_numpy:
                    docs.append(header)
                    docs.append(extra)
                else:
                    docs.append(header + extra)
            new_line = True
            try:
                ret_type = node.annotation.value
            except AttributeError:
                ret_type = node.annotation.get_code()
            if ret_type != "None":
                if is_restxt:
                    docs.append(":returns: " + cursor_marker)
                    docs.append(":rtype: " + ret_type)
                elif is_numpy:
                    docs.append(ret_type)
                    docs.append(indent_info + cursor_marker)
                else:
                    docs.append(indent_info + ret_type + " : " + cursor_marker)
        else:
            for leaf in node.iter_return_stmts():
                rtype, val = safe_determine_default_value(leaf.children[1])
                if val == "None":
                    continue
                if not new_line and (is_numpy or is_google):
                    docs.append(header)
                    if is_numpy:
                        docs.append(extra)
                new_line = True
                if is_restxt:
                    docs.append(":returns: " + val)
                    docs.append(":rtype: " + rtype)
                elif is_numpy:
                    docs.append(rtype)
                    docs.append(indent_info + val)
                else:
                    docs.append(indent_info + rtype + " : " + val)
    except AttributeError:
        pass
    else:
        if not is_restxt and new_line:
            docs.append("\n")
    if len(notes) > (2 if is_numpy else 1):
        docs.append("\n")
        docs.extend(notes)
    docs.append("'''\n")
    return docs


def generate_for_docstring(
    contents, cur_pos=None, docstring_name=DEFAULT_DOCSTRING, indent_info=None, node=None
):
    if indent_info is None:
        indent_info = "    "
    if cur_pos is None:
        whole_doc = True
    else:
        whole_doc = False
    parsed = parso.parse(contents)
    if whole_doc:
        return None
    if not node:
        name = parsed.get_leaf_for_position(tuple(cur_pos))
        if not name:
            return
        parent = name.parent
    else:
        parent = node
    template_data = docstring_template_data(
        docstring_name=docstring_name,
        node=parent,
        cur_pos=parent.start_pos,
        indent_info=indent_info,
    )
    if not template_data:
        return
    for child in parent.children[2:]:
        if isinstance(child, parso.python.tree.Operator) and child.value == ":":
            break
    else:
        return
    cur_pos = child.start_pos
    return {"line": cur_pos[0], "doc": template_data}


def on_refactor_done(future):
    result = future.result()
    if result:
        result.apply()


def start_jedi_refactor(content, file_name, line, column, action=None, action_args=None):
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
