import ctypes
import importlib
import site
import subprocess
import sys
import configparser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from gi.repository import Geany, GeanyScintilla, GLib, Gtk, Peasy

try:
    import jedi
except ImportError:
    print("jedi not found, python auto-completion not possible.")
    HAS_JEDI = False
else:
    jedi.settings.case_insensitive_completion = False
    HAS_JEDI = True

_ = Peasy.gettext

GEANY_WORDCHARS = "_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

ENABLE_CONFIGS = {
    "enable_venv_project": (_("Initialize Python Project."), _("Requires virtualenv or venv.")),
    "enable_jedi": (_("Jedi Completion for Python."), _("Requires Jedi")),
    "enable_autolint": (_("Enable auto lint on save."), _("Requires linting config in build.")),
    "enable_autoformat": (
        _("Enable auto format on save."),
        _("Requires formatter such as black,yapf etc"),
    ),
}


def is_mod_available(modname):
    try:
        return importlib.util.find_spec(modname) is not None
    except ImportError:
        return False


DEFAULT_FORMATTER = "black"
DEFAULTS = {"docstring_name": "google", "formatter_name": DEFAULT_FORMATTER}
FORMATTER_TYPES = {DEFAULT_FORMATTER, "autopep8", "yapf"}

is_pydoc_available = is_mod_available("pydocstring")
if is_pydoc_available:
    import pydocstring


def get_formatter(name=DEFAULT_FORMATTER):
    if not is_mod_available(name):
        print("No Formatter: {0}".format(name))
        return (
            None,
            None,
            None,
        )
    print("Formatter: {0}".format(name))
    get_default_style_for_dir = None
    if name == "black":
        import black

        def format_code(content, style_config=None):
            try:
                mode = black.FileMode(line_length=style_config["line_width"])
                changed_content = black.format_file_contents(content, fast=True, mode=mode)
            except black.NothingChanged:
                return "", False
            else:
                return changed_content, True

        return format_code, get_default_style_for_dir, black.InvalidInput
    elif name == "yapf":
        from yapf.yapflib.yapf_api import FormatCode
        from yapf.yapflib.file_resources import GetDefaultStyleForDir
        from lib2to3.pgen2 import parse

        return FormatCode, GetDefaultStyleForDir, parse.ParseError
    elif name == "autopep8":
        from autopep8 import fix_code

        def format_code(content, style=None):
            fixed_code = fix_code(content, options={"max_line_length": style["line_width"]})
            return fixed_code, True

        return format_code, get_default_style_for_dir, None
    return (None, None, None)


PYENV_HOME = Path.home().joinpath(".pyenv")
VIRTUALENV_HOME = Path.home().joinpath(".virtualenvs")
PYENV_VENV_HOME = PYENV_HOME.joinpath("versions")
pyenv_versions = set()
if PYENV_HOME.exists():
    has_pyenv = True
    try:
        pyenv_versions = {
            p.strip() if "system" not in p else "system"
            for p in subprocess.check_output(["pyenv", "versions"]).decode("utf8").split("\n")
        }
        from_command = True
    except (subprocess.CalledProcessError, FileNotFoundError):
        pyenv_versions = {d.name for d in PYENV_VENV_HOME.iterdir() if d.is_dir()}
        pyenv_versions.add("system")
        from_command = False
else:
    if VIRTUALENV_HOME.exists():
        pyenv_versions = {d.name for d in VIRTUALENV_HOME.iterdir() if d.is_dir()}
    has_pyenv = False

if HAS_JEDI:

    def jedi_complete(
        content, fp=None, text=None, sys_path=None, stop_len=25, project_dir=None, is_doc=True
    ):
        project = (
            project_dir if project_dir is None else jedi.Project(project_dir, sys_path=sys_path)
        )
        script = jedi.Script(content, path=fp, project=project)
        data = ""
        try:
            completions = script.completions()
        except AttributeError:
            return data
        for count, complete in enumerate(completions):
            name = complete.name
            if text is None and name.startswith("__") and name.endswith("__"):
                continue
            if text is not None:
                if text != name:
                    continue
                if not (complete.is_keyword or (complete.type and complete.type == "module")):
                    if is_doc:
                        return complete.docstring() or ""
                    sig = complete.get_signatures()
                    if sig:
                        sig = sig[0]
                        ret_string = sig._signature.annotation_string
                        params = ",\n    ".join(p.to_string() for p in sig.params)
                        sig = "{0}({1})".format(text, params)
                        if ret_string:
                            sig += " -> " + ret_string
                    return sig
                break
            if count > 0:
                data += "\n"
            data += name
            try:
                complete.params
            except AttributeError:
                data += "?2"
            else:
                data += "?1"
            if count == stop_len:
                break
        return data

    def append_project_venv(project):
        if not project:
            return sys.path
        site.addsitedir(project.base_path)
        proj_name = project.name
        cnf = configparser.ConfigParser()
        cnf.read(project.file_name)
        already_pth = cnf.get(NAME, PYTHON_PTH_LBL, fallback=None)
        if not already_pth:
            already_pth = proj_name
        venv_pth = PYENV_VENV_HOME if has_pyenv else VIRTUALENV_HOME
        if not venv_pth.is_dir():
            return sys.path
        for pth in venv_pth.iterdir():
            if pth.name.startswith((already_pth, already_pth.lower())) and pth.is_dir():
                st_pk = pth.glob("lib/python*/site-packages")
                st_pk = next(st_pk) if st_pk else None
                if st_pk and st_pk.is_dir():
                    proj_name = str(st_pk)
                    break
        else:  # nobreak
            return sys.path
        site.addsitedir(proj_name)
        return sys.path


NAME = "pycoding"
DIR_LABEL = "Choose Python Path"


PYTHON_PTH_LBL = "python_path"
PYCODING_CNF = ("is_pyproj", "create_template", "mkvenv", PYTHON_PTH_LBL)


def create_venv(proj_path, proj_name, python_pth):
    venvwrapper = None
    already_venv = (
        PYENV_VENV_HOME.joinpath(python_pth) if has_pyenv else VIRTUALENV_HOME.joinpath(python_pth)
    )
    if (
        not python_pth.lower().startswith(("3.", "pypy3"))
        and already_venv.joinpath("bin/python").exists()
    ):
        project_venv = already_venv
    else:
        status = "{0} in python venv creation for project: {1}".format("{0}", proj_name)
        if has_pyenv:
            project_venv = PYENV_VENV_HOME.joinpath(proj_name)
            if proj_name not in pyenv_versions:
                if from_command:
                    args = ["pyenv", "virtualenv", python_pth, proj_name]
                else:
                    args = "{0} -m venv {1}".format(
                        "python"
                        if python_pth == "system"
                        else PYENV_VENV_HOME.joinpath(python_pth).joinpath("bin/python"),
                        project_venv,
                    ).split()
                subprocess.check_call(args)
            else:
                status = status.format("Already present: No need")
        else:
            try:
                import virtualenv
            except ImportError:
                status = status.format("NO VIRTUALENV: Error")
            else:
                if not VIRTUALENV_HOME.exists():
                    VIRTUALENV_HOME.mkdir()
                project_venv = VIRTUALENV_HOME.joinpath(proj_name)
                sys.argv = [
                    "virtualenv",
                    "--python={0}".format(
                        "/usr/bin/python3" if python_pth == "system" else python_pth
                    ),
                    str(project_venv),
                ]
                virtualenv.main()
                venvwrapper = importlib.find_spec("virtualenvwrapper")
    pyenv_versions.add(project_venv.name)
    try:
        if project_venv.exists() and venvwrapper is not None:
            project_pth = project_venv.joinpath(".project")
            with project_pth.open("w") as of:
                of.write(str(proj_path))
    except Exception:
        status = status.format("Error")
    else:
        status = status.format("Success")
    return status


def create_proj_template(proj_path):
    status = "{0} in python template creation for project: {1}".format("{0}", proj_path.name)
    try:
        if not proj_path.is_dir():
            proj_path.mkdir()
    except Exception:
        status = status.format("Error")
    else:
        status = status.format("Success")
    return status


def on_project_done(future):
    status = future.result()
    Geany.msgwin_status_add_string(status)


def run_project_create(proj_name, proj_path, python_cnf=None):
    if not (python_cnf and python_cnf.get(PYCODING_CNF[0])):
        return
    proj_path = Path(proj_path)
    executor = ThreadPoolExecutor(max_workers=2)
    if python_cnf.get("mkvenv"):
        future = executor.submit(create_venv, proj_path, proj_name, python_cnf.get(PYTHON_PTH_LBL))
        future.add_done_callback(on_project_done)
    if python_cnf.get("create_template"):
        future = executor.submit(create_proj_template, proj_path.joinpath(proj_name))
        future.add_done_callback(on_project_done)


class PythonPorjectDialog(Gtk.Dialog):
    def __init__(self, parent, label_to_show=DIR_LABEL):
        Gtk.Dialog.__init__(
            self,
            _("Create Python Project"),
            parent,
            Gtk.DialogFlags.DESTROY_WITH_PARENT,
            (Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OK, Gtk.ResponseType.OK),
        )

        self.set_default_size(400, 200)
        box = self.get_content_area()
        for name in PYCODING_CNF:
            if name == PYTHON_PTH_LBL:
                dir_label = Gtk.Label(_("Virtual Environment Source Python Path:"))
                dir_label.set_alignment(0, 0.5)
                if has_pyenv:
                    button = Gtk.ComboBoxText()
                    button.insert_text(0, label_to_show)
                    for nam in pyenv_versions:
                        if not nam or label_to_show == nam:
                            continue
                        button.append_text(nam.strip())
                    button.set_active(0)
                else:
                    button = Gtk.Button(label_to_show)
                    button.connect("clicked", self.on_folder_clicked)
                button.set_name(name)
                box.add(dir_label)
                box.add(button)
            else:
                button = Gtk.CheckButton(
                    _(
                        "Is python project ?"
                        if name == "is_pyproj"
                        else "Make Virtual Environment ?"
                        if name == "mkvenv"
                        else "Make Template ?"
                    )
                )
                button.set_active(True)
                button.set_name(name)
                box.add(button)
        self.show_all()

    def on_folder_clicked(self, widget):
        filename = widget.get_label()
        pth = Path(filename)
        if not pth.is_file():
            filename = DIR_LABEL
        dialog = Gtk.FileChooserDialog(
            DIR_LABEL,
            self,
            Gtk.FileChooserAction.OPEN,
            (Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, _("Select"), Gtk.ResponseType.OK),
        )
        dialog.set_select_multiple(False)
        dialog.set_show_hidden(False)
        if filename:
            dialog.set_filename(filename)
        dialog.set_default_size(600, 300)
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            widget.set_label(dialog.get_filename())
        dialog.destroy()


class PycodingPlugin(Peasy.Plugin, Peasy.PluginConfigure):
    __gtype_name__ = NAME.title()
    pycoding_config = None
    completion_words = None
    format_signal = None
    lint_signals = None
    pyproj_signal = None
    format_item = None
    document_item = None
    docstring_name = "google"
    DEFAULT_LINE_WIDTH = 79
    default_pth_dir = None
    formatter_name = DEFAULT_FORMATTER

    def on_document_lint(self, user_data, doc):
        Geany.msgwin_clear_tab(Geany.MessageWindowTabNum.COMPILER)
        if not self.is_doc_python(doc):
            return False
        Geany.keybindings_send_command(Geany.KeyGroupID.BUILD, Geany.KeyBindingID.BUILD_LINK)
        return True

    def on_documentation_item_click(self, item=None):
        if not is_pydoc_available:
            return
        cur_doc = Geany.Document.get_current()
        if not self.is_doc_python(cur_doc):
            return
        sci = cur_doc.editor.sci
        contents = sci.get_contents(-1)
        if not contents:
            return

        try:
            cur_line = sci.get_current_line()
            line_content = sci.get_line(cur_line).strip()
            if not (line_content.startswith(("def", "class")) and line_content.endswith(":")):
                return
            add_to = 1
            while True:
                if add_to > 2:
                    break
                doc_text = pydocstring.generate_docstring(
                    contents,
                    position=(cur_line + add_to, 0),
                    formatter=self.docstring_name or "google",
                )
                add_to += 1
                dt = doc_text.strip()
                if not dt or dt == "Empty Module":
                    continue
                elif dt:
                    break
        except pydocstring.exc.InvalidFormatterError:
            return
        else:
            if not doc_text.strip() or doc_text.strip() == "Empty Module":
                return
            indent_pref = cur_doc.editor.get_indent_prefs()
            insert_pos = sci.get_line_end_position(cur_line)
            ind_type = " " * indent_pref.width if Geany.IndentType.SPACES else "\t"
            start_end = "{0}'''".format(ind_type)
            template = ["\n{0}...".format(start_end)]
            for doc in doc_text.splitlines():
                if self.docstring_name == "numpy":
                    template.append(doc)
                else:
                    template.append(doc if not doc else "{0}{1}".format(ind_type, doc))
            template.append(start_end)
            cur_doc.editor.insert_text_block("\n".join(template), insert_pos, -1, -1, False)

    def on_format_item_click(self, item=None):
        cur_doc = Geany.document_get_current()
        self.format_code(cur_doc)

    def format_code(self, doc):
        if not (self.formatter_name and self.is_doc_python(doc)):
            return False
        sci = doc.editor.sci
        code_contents = sci.get_contents(-1)
        if not code_contents:
            return False
        try:
            style_paths = [str(Path(doc.real_path).parent)]
        except Exception:
            style_paths = []
        project = self.geany_plugin.geany_data.app.project
        if project:
            style_paths.append(project.base_path)
        Geany.msgwin_clear_tab(Geany.MessageWindowTabNum.COMPILER)
        code_formatter, default_style_dir, exceptions = get_formatter(self.formatter_name)
        if code_formatter is None:
            return False
        if default_style_dir is not None:
            for path in style_paths:
                style = default_style_dir(path)
                if style:
                    break
            else:  # nobreak
                style = {}
            style["COLUMN_LIMIT"] = self.DEFAULT_LINE_WIDTH
        else:
            style = {"line_width": self.DEFAULT_LINE_WIDTH}
        if exceptions:
            try:
                format_text, formatted = code_formatter(code_contents, style_config=style)
            except exceptions as error:
                formatted = None
                Geany.msgwin_compiler_add_string(Geany.MsgColors.RED, str(error))
        else:
            format_text, formatted = code_formatter(code_contents, style_config=style)
        if formatted:
            pos = sci.get_current_position()
            sci.set_text(format_text)
            sci.set_current_position(pos, True)
        return formatted if formatted is not None else True

    def on_document_notify(self, user_data, doc):
        self.format_code(doc)

    def set_format_signal_handler(self, geany_obj=None):
        if not self.formatter_name:
            return
        if not geany_obj:
            geany_obj = self.geany_plugin.geany_data.object
        if self.enable_autoformat:
            if self.format_signal:
                return
            self.format_signal = geany_obj.connect("document-before-save", self.on_document_notify)
        else:
            if self.format_signal:
                geany_obj.disconnect(self.format_signal)

    def set_lint_signal_handler(self, geany_obj=None):
        if not geany_obj:
            geany_obj = self.geany_plugin.geany_data.object
        signals = ("document-activate", "document-save", "document-open")
        if self.enable_autolint:
            if self.lint_signals:
                return
            self.lint_signals = []
            for s in signals:
                self.lint_signals.append((geany_obj.connect(s, self.on_document_lint)))
        else:
            if not self.lint_signals:
                return
            for signl in self.lint_signals:
                geany_obj.disconnect(signl)
            self.lint_signals = None

    def set_pyproj_signal_handler(self, geany_obj=None):
        if not geany_obj:
            geany_obj = self.geany_plugin.geany_data.object
        if self.enable_venv_project:
            if self.pyproj_signal:
                return
            self.pyproj_signal = geany_obj.connect("project-save", self.on_pyproj_response)
        else:
            if self.pyproj_signal:
                geany_obj.disconnect(self.pyproj_signal)

    def on_pyproj_response(self, obj, proj_cnf_file):
        settings = dict(zip(PYCODING_CNF, [False, False, False, "system"]))
        for name in PYCODING_CNF:
            try:
                if name == PYTHON_PTH_LBL:
                    setting = proj_cnf_file.get_string(NAME, name)
                    if not has_pyenv:
                        pth = Path(setting)
                        if pth.is_file():
                            setting = str(pth)
                    settings[name] = setting
                else:
                    settings[name] = proj_cnf_file.get_boolean(NAME, name)
            except (GLib.Error, TypeError):
                show_dlg = True
                break
        else:  # nobreak
            show_dlg = False
        if show_dlg:
            dlg = PythonPorjectDialog(
                self.geany_plugin.geany_data.main_widgets.window,
                label_to_show=settings[PYTHON_PTH_LBL],
            )
            ok = dlg.run()
            if ok in (Gtk.ResponseType.APPLY, Gtk.ResponseType.OK):
                for child in dlg.get_content_area():
                    is_chkbtn = isinstance(child, Gtk.CheckButton)
                    is_btn = (
                        isinstance(child, Gtk.ComboBoxText)
                        if has_pyenv
                        else isinstance(child, Gtk.Button)
                    )
                    if not (is_chkbtn or is_btn):
                        continue
                    if is_chkbtn:
                        settings[child.get_name()] = child.get_active()
                    elif is_btn:
                        if has_pyenv:
                            pth = child.get_active_text()
                        else:
                            try:
                                pth = Path(child.get_label())
                            except TypeError:
                                pass
                            else:
                                if pth.is_file():
                                    pth = str(pth)
                                else:
                                    pth = ""
                        settings[child.get_name()] = pth
            dlg.destroy()
        for name, value in settings.items():
            if name == PYTHON_PTH_LBL:
                proj_cnf_file.set_string(NAME, name, str(value))
            else:
                proj_cnf_file.set_boolean(NAME, name, value)
        try:
            pattern_list = proj_cnf_file.get_string_list("project", "file_patterns")
        except GLib.GError:
            pattern_list = []
        proj_name = self.geany_plugin.geany_data.app.project.name
        base_path = self.geany_plugin.geany_data.app.project.base_path
        run_project_create(proj_name, base_path, settings)
        if settings.get(PYCODING_CNF[0]) and "*.py" not in pattern_list:
            pattern_list.append("*.py")
            proj_cnf_file.set_string_list("project", "file_patterns", pattern_list)

    def do_enable(self):
        geany_data = self.geany_plugin.geany_data
        self.pycoding_config = Path(geany_data.app.configdir).joinpath(
            "plugins", "{0}.conf".format(NAME)
        )
        keys = self.add_key_group(
            NAME, 1 + int(bool(self.formatter_name)) + int(is_pydoc_available)
        )
        self.DEFAULT_LINE_WIDTH = max(
            geany_data.editor_prefs.long_line_column, geany_data.editor_prefs.line_break_column
        )
        fpc = _("Format Python Code")
        self.format_item = Geany.ui_image_menu_item_new(Gtk.STOCK_EXECUTE, fpc)
        self.format_item.connect("activate", self.on_format_item_click)
        geany_data.main_widgets.editor_menu.append(self.format_item)
        keys.add_keybinding("format_python_code", fpc, self.format_item, 0, 0)
        if is_pydoc_available:
            dpc = _("Docstring for Python Code Block")
            self.document_item = Geany.ui_image_menu_item_new(Gtk.STOCK_EXECUTE, dpc)
            self.document_item.connect("activate", self.on_documentation_item_click)
            geany_data.main_widgets.editor_menu.append(self.document_item)
            keys.add_keybinding("generate_python_docstring", dpc, self.document_item, 0, 0)
        o = geany_data.object
        self.jedi_handler = o.connect("editor-notify", self.on_editor_notify)
        self.doc_close = o.connect(
            "document-close",
            lambda x, y: Geany.msgwin_clear_tab(Geany.MessageWindowTabNum.COMPILER),
        )
        # load startup config
        self.keyfile = GLib.KeyFile.new()
        if self.pycoding_config.is_file():
            self.keyfile.load_from_file(str(self.pycoding_config), GLib.KeyFileFlags.KEEP_COMMENTS)
        for cnf in ENABLE_CONFIGS:
            try:
                setattr(self, cnf, self.keyfile.get_boolean(NAME, cnf[0]))
            except GLib.Error:
                setattr(self, cnf, True)
        for name in {"docstring_name", "formatter_name"}:
            try:
                setattr(self, name, self.keyfile.get_string(NAME, name).lower())
            except GLib.Error:
                setattr(self, name, DEFAULTS[name])
        if self.enable_venv_project:
            self.set_pyproj_signal_handler(o)
        self.set_lint_signal_handler(o)
        self.set_format_signal_handler(o)
        return True

    def do_disable(self):
        geany_data = self.geany_plugin.geany_data
        o = geany_data.object
        o.disconnect(self.jedi_handler)
        o.disconnect(self.doc_close)
        self.enable_autolint = False
        self.set_lint_signal_handler(o)
        self.enable_autoformat = False
        self.set_format_signal_handler(o)
        self.enable_venv_project = False
        self.set_pyproj_signal_handler(o)
        if self.format_item:
            geany_data.main_widgets.editor_menu.remove(self.format_item)
            self.format_item.destroy()
            self.format_item = None
        if self.document_item:
            geany_data.main_widgets.editor_menu.remove(self.document_item)
            self.document_item.destroy()
            self.document_item = None

    @staticmethod
    def scintilla_command(sci, sci_msg, sci_cmd, lparam, data):
        if sci_cmd:
            sci.send_command(sci_cmd)
        if data:
            data = ctypes.c_char_p(data.encode("utf8"))
            tt = ctypes.cast(data, ctypes.c_void_p).value
            sci.send_message(sci_msg, lparam, tt)

    def is_doc_python(self, doc):
        is_python = (
            doc is not None
            and doc.is_valid
            and doc.file_type.id == Geany.FiletypeID.FILETYPES_PYTHON
        )
        if is_python:
            self.document_item.show()
            self.format_item.show()
        else:
            self.document_item.hide()
            self.format_item.hide()
        return is_python

    def call_jedi(self, doc_content, cur_doc=None, text=None, is_doc=True):
        cur_doc = cur_doc or Geany.document_get_current()
        fp = cur_doc.real_path or cur_doc.file_name
        proj = self.geany_plugin.geany_data.app.project
        path = append_project_venv(proj)
        try:
            project_dir = proj.base_path if proj else None
            return jedi_complete(
                doc_content,
                fp=fp,
                text=text,
                sys_path=path,
                project_dir=project_dir,
                is_doc=is_doc,
            )
        except ValueError:
            return

    def get_calltip(self, editor, pos):
        word_at_pos = editor.get_word_at_pos(pos, GEANY_WORDCHARS)
        if not word_at_pos:
            return
        sci = editor.sci
        doc_content = (sci.get_contents_range(0, pos) or "").rstrip()
        if not doc_content:
            return
        data = self.call_jedi(doc_content, cur_doc=editor.document, text=word_at_pos, is_doc=False)
        self.scintilla_command(
            sci,
            sci_cmd=GeanyScintilla.SCI_CALLTIPCANCEL,
            sci_msg=GeanyScintilla.SCI_CALLTIPSHOW,
            lparam=pos,
            data=data,
        )
        sci.send_message(GeanyScintilla.SCI_CALLTIPSETHLT, 0, len(data))

    def on_editor_notify(self, g_obj, editor, nt):
        cur_doc = editor.document or Geany.document_get_current()
        if not (HAS_JEDI and self.is_doc_python(cur_doc)):
            return False
        sci = editor.sci
        pos = sci.get_current_position()
        if pos < 2:
            return False
        if not Geany.highlighting_is_code_style(sci.get_lexer(), sci.get_style_at(pos - 2)):
            return False
        if nt.nmhdr.code in {
            GeanyScintilla.SCN_CHARADDED,
            GeanyScintilla.SCN_AUTOCSELECTION,
            GeanyScintilla.SCN_DWELLSTART,
            GeanyScintilla.SCN_DWELLEND,
        }:
            if nt.nmhdr.code in {GeanyScintilla.SCN_DWELLSTART, GeanyScintilla.SCN_DWELLEND}:
                if nt.nmhdr.code == GeanyScintilla.SCN_DWELLEND:
                    self.scintilla_command(
                        sci,
                        sci_cmd=GeanyScintilla.SCI_CALLTIPCANCEL,
                        sci_msg=None,
                        lparam=None,
                        data=None,
                    )
                else:
                    self.get_calltip(editor, nt.position)
                return False
            char = chr(nt.ch)
            code_check = {
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
                "(",
                ")",
            }
            if char in code_check:
                return False
            nt_text = getattr(nt, "text", None)
            self.complete_python(editor, char, nt_text)
        return False

    def complete_python(self, editor, char, text=None):
        sci = editor.sci
        pos = sci.get_current_position()
        col = sci.get_col_from_position(pos)
        if col == 1 and char in ("f", "i"):
            return
        line = sci.get_current_line()
        word_at_pos = sci.get_line(line)
        if not word_at_pos:
            return
        if word_at_pos.lstrip().startswith(("fr", "im")):
            doc_content = word_at_pos.rstrip()
            import_check = True
        else:
            doc_content = sci.get_contents_range(0, pos).rstrip()
            import_check = False
        word_at_pos = editor.get_word_at_pos(pos, GEANY_WORDCHARS + ".")
        if not word_at_pos:
            return
        rootlen = len(word_at_pos)
        if "." in word_at_pos:
            word_at_pos = editor.get_word_at_pos(pos, GEANY_WORDCHARS)
            if not word_at_pos:
                rootlen = 0
            else:
                rootlen = len(word_at_pos)
        elif not rootlen or (rootlen < 2 and not import_check):
            return
        cur_doc = editor.document
        data = self.call_jedi(doc_content, cur_doc=cur_doc, text=text)
        if not data:
            return
        if text is None:
            self.scintilla_command(
                sci,
                sci_cmd=GeanyScintilla.SCI_AUTOCCANCEL,
                sci_msg=GeanyScintilla.SCI_AUTOCSHOW,
                lparam=rootlen,
                data=data,
            )
            return
        can_doc = hasattr(Geany, "msgwin_compiler_add_string") and text is not None
        Geany.msgwin_clear_tab(Geany.MessageWindowTabNum.COMPILER)
        if not can_doc:
            return
        Geany.msgwin_compiler_add_string(Geany.MsgColors.BLACK, "Doc:\n{0}".format(data))

    def on_configure_response(self, dlg, response_id, user_data):
        if response_id not in (Gtk.ResponseType.APPLY, Gtk.ResponseType.OK):
            return
        conf_file = str(self.pycoding_config)
        if self.pycoding_config.is_file():
            self.keyfile.load_from_file(conf_file, GLib.KeyFileFlags.KEEP_COMMENTS)
        for child in user_data.get_children():
            if not isinstance(child, (Gtk.CheckButton, Gtk.ComboBoxText)):
                continue
            try:
                cnf_val = child.get_active_text()
            except AttributeError:
                cnf_val = child.get_active()
            name = child.get_name()
            setattr(self, name, cnf_val)
            if name not in {"docstring_name", "formatter_name"}:
                self.keyfile.set_boolean(NAME, name, cnf_val)
            else:
                self.keyfile.set_string(NAME, name, cnf_val)
        self.keyfile.save_to_file(conf_file)
        obj = self.geany_plugin.geany_data.object
        self.set_lint_signal_handler(obj)
        self.set_format_signal_handler(obj)

    def do_configure(self, dialog):
        align = Gtk.Alignment.new(0, 0, 1, 0)
        align.props.left_padding = 12
        vbox = Gtk.VBox(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        vbox.set_border_width(2)
        for name, cnf in ENABLE_CONFIGS.items():
            try:
                cnf_val = getattr(self, name)
            except AttributeError:
                cnf_val = True
                setattr(self, name, cnf_val)
            button = Gtk.CheckButton(cnf[0])
            button.set_tooltip_text(cnf[1])
            button.set_name(name)
            button.set_active(cnf_val)
            vbox.add(button)
        label = Gtk.Label("Code Formatter:")
        vbox.add(label)
        combo = Gtk.ComboBoxText()
        combo.set_name("formatter_name")
        for formatter_name in FORMATTER_TYPES:
            if formatter_name == self.formatter_name:
                combo.insert_text(0, formatter_name)
            else:
                combo.append_text(formatter_name)
        combo.set_active(0)
        vbox.add(combo)
        label = Gtk.Label("Format for docstring:")
        vbox.add(label)
        combo = Gtk.ComboBoxText()
        combo.set_name("docstring_name")
        for docstring_name in pydocstring.formatters._formatter_map:
            if docstring_name == self.docstring_name:
                combo.insert_text(0, docstring_name)
            else:
                combo.append_text(docstring_name)
        combo.set_active(0)
        vbox.add(combo)
        align.add(vbox)
        dialog.connect("response", self.on_configure_response, vbox)
        return align
