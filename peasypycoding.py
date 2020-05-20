import sys
import site
import ctypes
import shutil
import importlib
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from gi.repository import Gtk
from gi.repository import GLib
from gi.repository import Geany
from gi.repository import GeanyScintilla
from gi.repository import Peasy

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
        importlib.import_module(modname)
    except ImportError:
        return False
    else:
        return True


for formatter in ["black", "autopep8", "yapf"]:
    if is_mod_available(formatter):
        DEFAULT_FORMATTER = formatter
        break
else:  # nobreak
    DEFAULT_FORMATTER = None

is_pydoc_available = is_mod_available("pydocstring")
if is_pydoc_available:
    import pydocstring

is_isort_available = is_mod_available("isort")
if is_isort_available:
    import isort

if DEFAULT_FORMATTER:
    print("Formatter: {}".format(DEFAULT_FORMATTER))

    def get_formatter(name=DEFAULT_FORMATTER):
        if name == "black":
            import black

            GetDefaultStyleForDir = None

            def FormatCode(content, style_config=None):
                try:
                    mode = black.FileMode(line_length=style_config["line_width"])
                    changed_content = black.format_file_contents(content, fast=True, mode=mode)
                except black.NothingChanged:
                    return "", False
                else:
                    return changed_content, True

            return FormatCode, GetDefaultStyleForDir, black.InvalidInput
        elif name == "yapf":
            from yapf.yapflib.yapf_api import FormatCode  # reformat a string of code
            from yapf.yapflib.file_resources import GetDefaultStyleForDir

            return FormatCode, GetDefaultStyleForDir, None
        elif name == "autopep8":
            from autopep8 import fix_code

            GetDefaultStyleForDir = None

            def FormatCode(content, style=None):
                return fix_code(content, options={"max_line_length": style["line_width"]})

            return FormatCode, GetDefaultStyleForDir, None
        return None, None


PYENV_HOME = Path.home().joinpath(".pyenv")
VIRTUALENV_HOME = Path.home().joinpath(".virtualenvs")
PYENV_VENV_HOME = PYENV_HOME.joinpath("versions")
if PYENV_HOME.exists():
    has_pyenv = True
    try:
        pyenv_venv = {
            p.split()[0].strip()
            for p in subprocess.check_output(["pyenv", "virtualenvs"]).decode("utf8").split("\n")
            if p.strip()
        }
        pyenv_versions = {
            p.strip() if "system" not in p else "system"
            for p in subprocess.check_output(["pyenv", "versions"]).decode("utf8").split("\n")
            if p.strip() not in pyenv_venv
        }
        from_command = True
    except (subprocess.CalledProcessError, FileNotFoundError):
        pyenv_venv = set()
        pyenv_versions = set()
        for d in PYENV_VENV_HOME.iterdir():
            if d.is_symlink():
                pyenv_venv.add(d.name)
            else:
                pyenv_versions.add(d.name)
        pyenv_versions.add("system")
        from_command = False
else:
    has_pyenv = False

if HAS_JEDI:

    def jedi_complete(content, fp=None, text=None, sys_path=None, stop_len=25, project_dir=None):
        project = (
            project_dir if project_dir is None else jedi.Project(project_dir, sys_path=sys_path)
        )
        script = jedi.Script(content, path=fp, project=project)
        data = ""
        doc = None
        try:
            completions = script.completions()
        except AttributeError:
            return data
        for count, complete in enumerate(completions):
            name = complete.name
            if name.startswith("__") and name.endswith("__"):
                continue
            if text is not None:
                if text != name:
                    continue
                if not (complete.is_keyword or (complete.type and complete.type == "module")):
                    doc = complete.docstring()
                    return doc or ""
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

    def append_project_venv(proj_name):
        if not proj_name:
            return sys.path
        venv_pth = PYENV_VENV_HOME if has_pyenv else VIRTUALENV_HOME
        if not venv_pth.is_dir():
            return sys.path
        for pth in venv_pth.iterdir():
            if pth.name.startswith(proj_name.lower()) and pth.is_dir():
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


def check_and_format_sort(formated_text, line_length=79):
    if not is_isort_available:
        return formated_text
    sorted_text = isort.SortImports(file_contents=formated_text, line_length=line_length)
    return sorted_text.output

def run_formatter(formatter, scintilla, line_width=99, style_paths=None):
    if style_paths is None:
        style_paths = []
    contents = scintilla.get_contents(-1)
    if not contents:
        return False
    code_formatter, default_style_dir, exceptions = get_formatter(DEFAULT_FORMATTER)
    if default_style_dir is not None:
        for path in style_paths:
            style = default_style_dir(path)
            if style:
                break
        else:  # nobreak
            style = {}
        style["COLUMN_LIMIT"] = line_width
    else:
        style = {"line_width": line_width}
    error = None
    if exceptions:
        try:
            format_text, formatted = code_formatter(contents, style_config=style)
        except exceptions:
            formatted = None
            Geany.msgwin_compiler_add_string(Geany.MsgColors.RED, str(error))
    else:
        format_text, formatted = code_formatter(contents, style_config=style)
    if formatted is not None:
        sorted_text = check_and_format_sort(format_text or contents, line_length=line_width)
        if formatted or is_isort_available:
            pos = scintilla.get_current_position()
            scintilla.set_text(sorted_text)
            scintilla.set_current_position(pos, True)
    return formatted if formatted is not None else True


PYTHON_PTH_LBL = "python_path"
PYCODING_CNF = ("is_pyproj", "create_template", "mkvenv", PYTHON_PTH_LBL)
PYTHON_PTH_INDEX = PYCODING_CNF.index(PYTHON_PTH_LBL)


def create_venv(proj_path, proj_name, python_pth):
    status = "{0} in python venv creation for project: {1}".format("{0}", proj_name)
    if has_pyenv:
        project_venv = PYENV_VENV_HOME.joinpath(proj_name)
        if proj_name not in pyenv_venv:
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
            pyenv_venv.add(proj_name)
    else:
        try:
            import virtualenv
        except ImportError:
            status = status.format("NO VIRTUALENV: Error")
        else:
            if not VIRTUALENV_HOME.exists():
                VIRTUALENV_HOME.mkdir()
            project_venv = VIRTUALENV_HOME.joinpath(proj_name)

    def write_proj_path(filename):
        if filename.is_file():
            return
        with open(str(filename), "w") as of:
            of.write(str(proj_path))

    try:
        if not project_venv.exists():
            if has_pyenv:
                status = status.format("PYENV: Error")
            else:
                if not VIRTUALENV_HOME.exists():
                    status = status.format("NO VIRTUALENV: Error")
                else:
                    sys.argv = ["virtualenv", "--python={0}".format(python_pth), str(project_venv)]
                    virtualenv.main()
        else:
            st_pk = project_venv.glob("lib/python*/site-packages")
            st_pk = next(st_pk) if st_pk else None
            if st_pk and st_pk.is_dir():
                st_pk = st_pk.joinpath("{0}.pth".format(proj_name))
                write_proj_path(st_pk)
        venvwrapper = importlib.find_spec("virtualenvwrapper")
        if venvwrapper is not None:
            project_pth = project_venv.joinpath(".project")
            if not project_pth.is_file():
                if st_pk and st_pk.is_file():
                    shutil.copy2(str(st_pk), str(project_pth))
                else:
                    write_proj_path(project_pth)
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
        for index, name in enumerate(PYCODING_CNF):
            if index == PYTHON_PTH_INDEX:
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
    docstring_name = None
    DEFAULT_LINE_WIDTH = 79
    default_pth_dir = None

    def on_document_lint(self, user_data, doc):
        self.check_and_lint(doc)

    def on_document_close(self, user_data, doc):
        Geany.msgwin_clear_tab(Geany.MessageWindowTabNum.COMPILER)

    def check_and_lint(self, doc, check=True):
        self.on_document_close(None, doc)
        if check and not self.is_doc_python(doc):
            return False
        Geany.keybindings_send_command(Geany.KeyGroupID.BUILD, Geany.KeyBindingID.BUILD_LINK)
        return True if check else False

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
            doc_text = pydocstring.generate_docstring(
                contents, position=(cur_line + 1, 0), formatter=self.docstring_name or "google"
            ).strip()
        except pydocstring.exc.InvalidFormatterError:
            return
        else:
            if not doc_text or doc_text == "Empty Module":
                return
            indent_pref = cur_doc.editor.get_indent_prefs()
            ind_types = {Geany.IndentType.SPACES: " ", Geany.IndentType.TABS: "\t"}.get(
                indent_pref.type
            )
            insert_pos = sci.get_position_from_line(cur_line + 1)
            line_indent = sci.get_line_indentation(cur_line + 1)
            if ind_types == " ":
                ind_types = ind_types * line_indent
            doc_text = "\n".join(
                (
                    "{0}{1}".format(ind_types, d) if d.strip() else d.strip()
                    for d in doc_text.split("\n")
                    if not d.strip().startswith(("self", "cls"))
                )
            )
            template = """{0}'''[DESCRIPTION]\n\n{1}\n{0}'''\n""".format(ind_types, doc_text)
            sci.insert_text(insert_pos, template)

    def on_format_item_click(self, item=None):
        cur_doc = Geany.Document.get_current()
        self.format_code(cur_doc)

    def format_code(self, doc):
        if not (DEFAULT_FORMATTER and self.is_doc_python(doc)):
            return False
        sci = doc.editor.sci
        contents = sci.get_contents(-1)
        if not contents:
            return False
        try:
            style_paths = [str(Path(doc.real_path).parent)]
        except Exception:
            style_paths = []
        project = self.geany_plugin.geany_data.app.project
        if project:
            style_paths.append(project.base_path)
        self.on_document_close(None, doc)
        return run_formatter(
            DEFAULT_FORMATTER, sci, style_paths=style_paths, line_width=self.DEFAULT_LINE_WIDTH
        )

    def on_document_notify(self, user_data, doc):
        self.format_code(doc)
        
    def set_format_signal_handler(self, geany_obj=None):
        if not DEFAULT_FORMATTER:
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
        signals = ("document-activate", "document-open", "document-save")
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
        settings = dict(
            zip(PYCODING_CNF, [False, False, False, "system" if has_pyenv else "/usr/bin/python3"])
        )
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
        keys = self.add_key_group(NAME, 1 + int(bool(DEFAULT_FORMATTER)) + int(is_pydoc_available))
        if DEFAULT_FORMATTER:
            self.DEFAULT_LINE_WIDTH = max(
                geany_data.editor_prefs.long_line_column, geany_data.editor_prefs.line_break_column
            )
            fpc = _("Format Python Code")
            self.format_item = Geany.ui_image_menu_item_new(Gtk.STOCK_EXECUTE, fpc)
            self.format_item.connect("activate", self.on_format_item_click)
            geany_data.main_widgets.editor_menu.append(self.format_item)
            keys.add_keybinding("format_python_code", fpc, self.format_item, 0, 0)
        if is_pydoc_available:
            dpc = _("Document Python Code")
            self.document_item = Geany.ui_image_menu_item_new(Gtk.STOCK_EXECUTE, dpc)
            self.document_item.connect("activate", self.on_documentation_item_click)
            geany_data.main_widgets.editor_menu.append(self.document_item)
            keys.add_keybinding("generate_python_docstring", dpc, self.document_item, 0, 0)
        o = geany_data.object
        self.jedi_handler = o.connect("editor-notify", self.on_editor_notify)
        self.doc_close = o.connect("document-close", self.on_document_close)
        # load startup config
        self.keyfile = GLib.KeyFile.new()
        if self.pycoding_config.is_file():
            self.keyfile.load_from_file(str(self.pycoding_config), GLib.KeyFileFlags.KEEP_COMMENTS)
        for cnf in ENABLE_CONFIGS:
            try:
                setattr(self, cnf, self.keyfile.get_boolean(NAME, cnf[0]))
            except GLib.Error:
                setattr(self, cnf, True)
        try:
            setattr(self, "docstring_name", self.keyfile.get_string(NAME, "docstring_name"))
        except GLib.Error:
            self.docstring_name = "google"
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

    def on_editor_notify(self, g_obj, editor, nt):
        cur_doc = editor.document or Geany.Document.get_current()
        if not (HAS_JEDI and self.is_doc_python(cur_doc)):
            return False
        sci = editor.sci
        pos = sci.get_current_position()
        if pos < 2:
            return False
        if not Geany.highlighting_is_code_style(sci.get_lexer(), sci.get_style_at(pos - 2)):
            return False
        if nt.nmhdr.code in (GeanyScintilla.SCN_CHARADDED, GeanyScintilla.SCN_AUTOCSELECTION):
            self.complete_python(editor, nt.ch, getattr(nt, "text", None))
        return False

    def complete_python(self, editor, char, text=None):
        char = chr(char)
        code_check = (
            "\r",
            "\n",
            " ",
            "\t",
            "\v",
            "\f",
            ">",
            "/",
            "(",
            ")",
            "{",
            "[",
            '"',
            "'",
            "}",
            ":",
        )
        if char in code_check:
            return
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
        fp = cur_doc.real_path or cur_doc.file_name
        proj = self.geany_plugin.geany_data.app.project
        path = append_project_venv(proj.name if proj else None)
        try:
            project_dir = proj.base_path if proj else None
            data = jedi_complete(
                doc_content, fp=fp, text=text, sys_path=path, project_dir=project_dir
            )
        except ValueError:
            return
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
        can_doc = hasattr(Geany, "msgwin_msg_add_string") and text is not None
        self.on_document_close(None, cur_doc)
        if not can_doc:
            return
        Geany.msgwin_compiler_add_string(Geany.MsgColors.BLACK, "Doc:\n" + data)
        # line -= 1
        # Geany.msgwin_msg_add_string(Geany.MsgColors.BLACK, line, cur_doc, "Doc:\n" + data)
        # Geany.msgwin_switch_tab(Geany.MessageWindowTabNum.MESSAGE, False)

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
                val = child.get_active_text()
            except AttributeError:
                val = child.get_active()
            name = child.get_name()
            setattr(self, name, val)
            if name != "docstring_name":
                self.keyfile.set_boolean(NAME, name, val)
            else:
                self.keyfile.set_string(NAME, name, val)
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
                val = getattr(self, name)
            except AttributeError:
                val = True
                setattr(self, name, val)
            button = Gtk.CheckButton(cnf[0])
            button.set_tooltip_text(cnf[1])
            button.set_name(name)
            button.set_active(val)
            vbox.add(button)
        combo = Gtk.ComboBoxText()
        name = "docstring_name"
        val = getattr(self, name)
        if val not in pydocstring.formatters._formatter_map:
            val = "google"
        combo.set_name(name)
        combo.insert_text(0, val)
        for name in pydocstring.formatters._formatter_map:
            if name == val:
                continue
            combo.append_text(name)
        combo.set_active(0)
        vbox.add(combo)
        align.add(vbox)
        dialog.connect("response", self.on_configure_response, vbox)
        return align
