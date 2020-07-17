import site
import ctypes
import doctest
import inspect
import keyword
import itertools

import collections
import configparser


from pathlib import Path

from gi.repository import Geany, GeanyScintilla, GLib, Gtk, Peasy

pth = Path(__file__).resolve()
site.addsitedir(str(pth.parent))
from pycoding_helpers import format_utils
from pycoding_helpers import lint_utils
from pycoding_helpers import generic_utils as utils
from pycoding_helpers import testing_utils as test_utils
from pycoding_helpers import venv_utils


_ = Peasy.gettext

GEANY_WORDCHARS = "_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
venv_key = "enable_venv_project"
jedi_key = "enable_jedi"
autolint_key = "enable_autolint"
autoformat_key = "enable_autoformat"
annotate_lint_key = "enable_annotate_lint"
pyconsole_key = "enable_pyconsole"
ENABLE_CONFIGS = {
    venv_key: (
        _("Initialize Python Project Properties."),
        _("Requires pyenv, virtualenv or venv."),
    ),
    jedi_key: (_("Jedi Completion/Documentation/Signatures for Python."), _("Requires jedi."),),
    autolint_key: (
        _("Enable auto lint on save."),
        _("Requires linting config in build or linter for annotation."),
    ),
    annotate_lint_key: (
        _("Enable Annotation on lint."),
        _("Requires a linter and will disable build linting."),
    ),
    autoformat_key: (
        _("Enable auto format on save."),
        _("Requires formatter such as black, yapf etc."),
    ),
    pyconsole_key: (
        _("Enable console menu with python."),
        _("Defaults to 'ipython'/'python'. Will use one of venv if enabled."),
    ),
}

formatter_key = "formatter_name"
docstring_key = "docstring_name"
linter_key = "linter_name"

DEFAULTS = {
    formatter_key: {
        "label": "Code Formatter:",
        "default": format_utils.DEFAULT_FORMATTER,
        "options": format_utils.FORMATTER_TYPES,
    },
    docstring_key: {
        "label": "Format for docstrings:",
        "default": utils.DEFAULT_DOCSTRING,
        "options": utils.DOCSTRING_TYPES,
    },
    linter_key: {
        "label": "Code Linter for annotation:",
        "default": lint_utils.DEFAULT_LINTER,
        "options": lint_utils.LINTER_TYPES,
    },
}
HAS_JEDI = utils.jedi is not None

DIR_LABEL = "Choose Python Path"

PYCODING_CNF = (
    venv_utils.IS_PYPROJECT,
    "create_template",
    "mkvenv",
    venv_utils.PYTHON_PTH_LBL,
    test_utils.PYTHON_TESTING_LBL,
)
pyproj_setting_lbls = {
    venv_utils.IS_PYPROJECT: _("Is python project ?"),
    "mkvenv": _("Create Virtual Environment."),
    "create_template": _("Create a template dir in project."),
    test_utils.PYTHON_TESTING_LBL: _("Testing Library :"),
    venv_utils.PYTHON_PTH_LBL: _("Virtual Environment Path :"),
}


def get_pyproj_properties(proj_settings):
    for name in PYCODING_CNF:
        val = proj_settings[name]
        lbl = pyproj_setting_lbls.get(name)
        if name == venv_utils.PYTHON_PTH_LBL:
            dir_label = Gtk.Label(lbl)
            dir_label.set_alignment(0, 0.5)
            yield dir_label
            if venv_utils.has_pyenv:
                button = Gtk.ComboBoxText()
                for nam in venv_utils.pyenv_versions:
                    nam = nam.strip()
                    if not nam:
                        continue
                    if val == nam:
                        button.insert_text(0, val)
                    else:
                        button.append_text(nam)
                button.set_active(0)
            else:
                button = Gtk.Button(val)
                button.connect("clicked", PythonPorjectDialog.on_folder_clicked)
            button.set_name(name)
        elif name == test_utils.PYTHON_TESTING_LBL:
            dir_label = Gtk.Label(lbl)
            dir_label.set_alignment(0, 0.5)
            yield dir_label
            button = Gtk.ComboBoxText()
            for nam in test_utils.TESTING_LIBRARIES:
                if val == nam:
                    button.insert_text(0, val)
                else:
                    button.append_text(nam)
            button.set_active(0)
            button.set_name(name)
        else:
            button = Gtk.CheckButton(lbl)
            button.set_active(bool(val))
            button.set_name(name)
        yield button


class PythonPorjectDialog(utils.JediRefactorDialog):
    def set_and_show(self, proj_settings):
        box = self.get_content_area()
        for widget in get_pyproj_properties(proj_settings):
            box.add(widget)
        self.show_all()

    @staticmethod
    def on_folder_clicked(widget, parent=None):
        filename = widget.get_label()
        pth = Path(filename)
        if not pth.is_file():
            filename = DIR_LABEL
        dialog = Gtk.FileChooserDialog(
            DIR_LABEL,
            parent,
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


test_sidebar = test_utils.PythonTestingWindow()


class PycodingPlugin(Peasy.Plugin, Peasy.PluginConfigure):
    __gtype_name__ = venv_utils.NAME.title()
    pycoding_config = None
    format_signal = None
    lint_signals = None
    pyproj_signal = None
    format_item = None
    pyconsole_item = None
    pytest_item = None
    jgoto_item = None
    document_item = None
    properties_tab = None
    proj_settings = None
    formatter_methods = [None]
    jedi_script = None
    select_cache = collections.defaultdict(list)
    proj_env = None
    refactor_item = None

    def on_document_lint(self, user_data, doc):
        sci = doc.editor.sci
        sci.send_message(
            GeanyScintilla.SCI_MARKERDELETEALL,
            GeanyScintilla.SC_MARK_BACKGROUND,
            GeanyScintilla.SC_MARK_BACKGROUND,
        )
        Geany.msgwin_clear_tab(Geany.MessageWindowTabNum.COMPILER)
        if not self.is_doc_python(doc):
            return False
        if self.get_keyfile_pref(annotate_lint_key, pref_type=0):
            sci.send_message(
                GeanyScintilla.SCI_ANNOTATIONSETVISIBLE,
                GeanyScintilla.ANNOTATION_BOXED,
                GeanyScintilla.ANNOTATION_INDENTED,
            )
            contents = sci.get_contents(-1)
            if not contents:
                return True
            try:
                checks = lint_utils.check_python_code(
                    doc.real_path or doc.file_name,
                    file_content=contents,
                    line_length=max(
                        self.geany_plugin.geany_data.editor_prefs.long_line_column,
                        self.geany_plugin.geany_data.editor_prefs.line_break_column,
                    ),
                    linter=self.get_keyfile_pref(linter_key),
                    syn_errors=utils.Script(contents).get_syntax_errors() if HAS_JEDI else [],
                )
            except Exception as err:
                checks = [["fatal", 1, 0, str(err)]]
            sci.send_command(GeanyScintilla.SCI_ANNOTATIONCLEARALL)
            for indicator in lint_utils.annotation_indicators:
                doc.editor.indicator_clear(indicator)
            for severity, line, col, msg in checks:
                self.scintilla_command(
                    sci,
                    sci_cmd=None,
                    sci_msg=GeanyScintilla.SCI_ANNOTATIONSETTEXT,
                    lparam=line,
                    data=msg,
                )
                sci.send_message(GeanyScintilla.SCI_ANNOTATIONSETSTYLE, line, severity)
                text = sci.get_line(line)
                if col == 0 and text:
                    for i in text:
                        if i.strip():
                            break
                        col += 1
                text = text[col - 1 :].strip()
                start = sci.get_position_from_line(line) + col
                end = start + len(text)
                if end > start:
                    for indicator in lint_utils.annotation_indicators:
                        doc.editor.indicator_set_on_range(indicator, start, end)
        elif self.get_keyfile_pref(autolint_key, pref_type=0):
            Geany.keybindings_send_command(Geany.KeyGroupID.BUILD, Geany.KeyBindingID.BUILD_LINK)
        self.on_pyproj_doc(doc)
        return True

    def on_documentation_item_click(self, item=None):
        if utils.parso is None:
            return
        cur_doc = Geany.document_get_current()
        if not self.is_doc_python(cur_doc):
            return True
        sci = cur_doc.editor.sci
        contents = sci.get_contents(-1)
        if not contents:
            return True
        cur_line = sci.get_current_line()
        cur_line_content = sci.get_line(cur_line)
        line_content = (cur_line_content or "").lstrip()
        if not line_content.startswith(utils.doc_defs):
            return True
        col = len(cur_line_content) - len(line_content)
        indent_pref = cur_doc.editor.get_indent_prefs()
        indent = " " * indent_pref.width if indent_pref.type == Geany.IndentType.SPACES else "\t"
        try:
            docstring = utils.generate_for_docstring(
                contents,
                cur_pos=[cur_line + 1, col + 2],
                docstring_name=self.get_keyfile_pref(docstring_key),
                indent_info=indent,
            )
        except utils.parso.ParserSyntaxError as error:
            Geany.msgwin_msg_add_string(Geany.MsgColors.RED, -1, cur_doc, str(error))
            return True
        if not docstring:
            return True
        line = docstring["line"]
        pos = sci.get_position_from_line(line) + col + indent_pref.width
        ds = "\n".join(docstring["doc"])
        cur_doc.editor.insert_text_block(ds, pos, -1, -1, True)

    def on_format_item_click(self, item=None):
        cur_doc = Geany.document_get_current()
        return self.format_code(cur_doc)

    def format_code(self, doc):
        if not (self.formatter_methods[0] and self.is_doc_python(doc)):
            return False
        sci = doc.editor.sci
        line = -1
        code_contents = sci.get_contents(line)
        if not code_contents:
            return False
        try:
            style_paths = [str(Path(doc.real_path).parent)]
        except Exception:
            style_paths = []
        project = self.geany_plugin.geany_data.app.project
        cur_line = sci.get_current_line()
        if project:
            style_paths.append(project.base_path)
        formatted, format_text = format_utils.run_formatter_on_content(
            self.formatter_methods,
            code_contents,
            style_paths=style_paths,
            line_width=max(
                self.geany_plugin.geany_data.editor_prefs.long_line_column,
                self.geany_plugin.geany_data.editor_prefs.line_break_column,
            ),
        )
        Geany.msgwin_clear_tab(Geany.MessageWindowTabNum.COMPILER)
        if formatted:
            line_cnt = sci.get_line_count()
            sci.set_text(format_text)
            new_line_cnt = sci.get_line_count()
            cur_line += new_line_cnt - line_cnt
            sci.set_current_position(sci.get_position_from_line(cur_line), True)
        elif formatted is None:
            Geany.msgwin_compiler_add_string(Geany.MsgColors.DARK_RED, format_text)
            Geany.msgwin_switch_tab(Geany.MessageWindowTabNum.COMPILER, False)
        return formatted if formatted is not None else True

    def on_document_close(self, user_data, doc):
        Geany.msgwin_clear_tab(Geany.MessageWindowTabNum.COMPILER)
        self.select_cache = collections.defaultdict(list)
        self.jedi_script = None

    def on_document_notify(self, user_data, doc):
        self.format_code(doc)

    def set_format_signal_handler(self, geany_obj=None, disable=False):
        if not geany_obj:
            geany_obj = self.geany_plugin.geany_data.object
        if not disable and self.get_keyfile_pref(formatter_key):
            if self.format_signal:
                return
            self.format_signal = geany_obj.connect("document-before-save", self.on_document_notify)
        else:
            if self.format_signal:
                geany_obj.disconnect(self.format_signal)

    def set_lint_signal_handler(self, geany_obj=None, disable=False):
        if not geany_obj:
            geany_obj = self.geany_plugin.geany_data.object
        signals = ("document-activate", "document-save", "document-open")
        if not disable and (
            self.get_keyfile_pref(autolint_key, pref_type=0)
            or self.get_keyfile_pref(annotate_lint_key, pref_type=0)
        ):
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

    def set_pyproj_signal_handler(self, geany_obj=None, disable=False):
        if not geany_obj:
            geany_obj = self.geany_plugin.geany_data.object
        self.pyproj_signal = []
        if not disable and self.get_keyfile_pref(venv_key, pref_type=0):
            if self.pyproj_signal:
                return
            self.pyproj_signal.append(geany_obj.connect("project-open", self.on_pyproj_response))
            self.pyproj_signal.append(geany_obj.connect("project-save", self.on_pyproj_response))
            self.pyproj_signal.append(geany_obj.connect("project-close", self.on_pyproj_close))
            self.pyproj_signal.append(
                geany_obj.connect("project-dialog-close", self.on_pyproj_close)
            )
            self.pyproj_signal.append(
                geany_obj.connect("project-dialog-confirmed", self.on_pyproj_confirmed)
            )
            self.pyproj_signal.append(
                geany_obj.connect("project-dialog-open", self.on_pyproj_open)
            )
        else:
            if self.pyproj_signal:
                for sig in self.pyproj_signal:
                    geany_obj.disconnect(sig)

    def on_pyproj_close(self, obj, gtk_widget=None):
        if gtk_widget is not None:
            self.set_proj_env()
            if self.properties_tab:
                self.set_proj_settings(self.properties_tab.get_children())
                self.properties_tab.destroy()
                self.properties_tab = None
        else:
            self.proj_settings = None
            self.select_cache = collections.defaultdict(list)
            self.proj_env = None
            self.geany_plugin.geany_data.main_widgets.sidebar_notebook.remove_page(
                self.testing_win.sidebar_page
            )
            self.testing_win.sidebar_page = 0
            self.geany_plugin.geany_data.main_widgets.message_window_notebook.remove_page(
                self.testing_win.testout_page
            )
            self.testing_win.testout_page = 0
            self.testing_win.hide_all_window()

    def on_pyproj_open(self, obj, gtk_widget):
        self.properties_tab = Gtk.VBox(False, 0)
        for widget in get_pyproj_properties(self.proj_settings):
            self.properties_tab.add(widget)
        gtk_widget.append_page(self.properties_tab, Gtk.Label("Python Coding"))
        gtk_widget.show_all()

    def set_proj_env(self, cnf=None):
        proj = self.geany_plugin.geany_data.app.project
        env = venv_utils.get_possible_cmd(venv_utils.py_cmd, project=proj, config=cnf)
        self.proj_env = (
            proj if proj is None else utils.jedi.Project(proj.base_path, environment_path=env)
        )

    def on_pyproj_confirmed(self, obj, gtk_widget):
        self.set_proj_settings(gtk_widget.get_children())
        self.set_proj_env()

    def on_pyproj_response(self, obj, proj_cnf_file):
        settings_read = True
        if not self.proj_settings:
            self.proj_settings = dict(
                zip(
                    PYCODING_CNF,
                    [
                        False,
                        False,
                        False,
                        venv_utils.DEFAULT_PYTHON_NAME,
                        test_utils.DEFAULT_TESTING_LIB,
                    ],
                )
            )
            settings_read = False
        if not settings_read:
            for name in PYCODING_CNF:
                try:
                    if name == venv_utils.PYTHON_PTH_LBL:
                        setting = proj_cnf_file.get_string(venv_utils.NAME, name)
                        if not venv_utils.has_pyenv:
                            pth = Path(setting)
                            if pth.is_file():
                                setting = str(pth)
                        self.proj_settings[name] = setting
                    elif name == test_utils.PYTHON_TESTING_LBL:
                        self.proj_settings[name] = proj_cnf_file.get_string(venv_utils.NAME, name)
                    else:
                        self.proj_settings[name] = proj_cnf_file.get_integer(venv_utils.NAME, name)
                except (GLib.Error, TypeError) as e:
                    print(e)
                    show_dlg = True
                    break
            else:  # nobreak
                show_dlg = False
        else:
            show_dlg = False
        if show_dlg:
            dlg = PythonPorjectDialog(
                self.geany_plugin.geany_data.main_widgets.window,
                _("Is Python Project ?"),
                _("_Save"),
            )
            dlg.set_and_show(self.proj_settings)
            ok = dlg.run()
            if ok in (Gtk.ResponseType.APPLY, Gtk.ResponseType.OK):
                self.set_proj_settings(dlg.get_content_area().get_children())
                dlg.destroy()
        for name, value in self.proj_settings.items():
            if name == venv_utils.PYTHON_PTH_LBL:
                proj_cnf_file.set_string(venv_utils.NAME, name, str(value))
            elif test_utils.PYTHON_TESTING_LBL:
                proj_cnf_file.set_string(venv_utils.NAME, name, str(value))
            else:
                proj_cnf_file.set_integer(venv_utils.NAME, name, int(value))
        is_pyproj = self.proj_settings.get(venv_utils.IS_PYPROJECT)
        if not is_pyproj:
            return
        try:
            pattern_list = proj_cnf_file.get_string_list("project", "file_patterns")
        except GLib.GError:
            pattern_list = []
        proj_name = self.geany_plugin.geany_data.app.project.name
        if is_pyproj and "*.py" not in pattern_list:
            pattern_list.append("*.py")
            proj_cnf_file.set_string_list("project", "file_patterns", pattern_list)
        base_path, cnf = self.get_proj_bp_and_cnf()
        venv_utils.create_venv_for_project(proj_name, base_path, self.proj_settings)
        self.set_proj_env(cnf)
        if not self.testing_win.sidebar_page and base_path:
            self.testing_win.sidebar_page = self.geany_plugin.geany_data.main_widgets.sidebar_notebook.append_page(
                self.testing_win.sidebar_window, Gtk.Label("Python Tests")
            )
            test_module = self.proj_settings.get(test_utils.PYTHON_TESTING_LBL)
            action = test_utils.MODULE_MAP.get(test_module).get("discover")
            if isinstance(action, list):
                python_cmd = venv_utils.get_possible_cmd(
                    venv_utils.py_cmd, project=base_path, config=cnf
                )
                action = [python_cmd, "-m", test_module] + action
            self.testing_win.setup_sidebar_treeview(action, base_path)
        if not self.testing_win.testout_page and base_path:
            self.testing_win.testout_page = self.geany_plugin.geany_data.main_widgets.message_window_notebook.append_page(
                self.testing_win.testing_output_window, Gtk.Label("Pycoding Output")
            )
        self.testing_win.show_all_window()

    def on_pyproj_doc(self, cur_doc):
        proj = self.geany_plugin.geany_data.app.project
        on_python_proj = proj and self.proj_settings.get(venv_utils.IS_PYPROJECT)
        linter_cmd = venv_utils.get_possible_cmd(self.get_keyfile_pref(linter_key), project=proj)
        formatter_cmd = venv_utils.get_possible_cmd(
            self.get_keyfile_pref(formatter_key), project=proj
        )
        print(formatter_cmd)
        if on_python_proj:
            file_name = " " + cur_doc.real_path.replace(proj.base_path, ".")
            testing_cmd = self.proj_settings[test_utils.PYTHON_TESTING_LBL]
            main_cmd = venv_utils.get_possible_cmd(venv_utils.py_cmd, project=proj)
            bs = Geany.BuildSource.PROJ
            wd = "%p"
        elif not proj:
            file_name = " %f"
            bs = Geany.BuildSource.FT
            wd = ""
        else:
            return
        for grp, rng in venv_utils.exec_cmds.items():
            for i in range(rng):
                lbl = Geany.build_get_current_menu_item(grp, i, Geany.BuildCmdEntries.LABEL)
                lbl_l = (lbl or "").lower()
                cmd = None
                if grp == Geany.BuildGroup.FT:
                    if "lint" in lbl_l or (i == 1 and not lbl):
                        if on_python_proj or "/usr" in linter_cmd:
                            cmd = linter_cmd + file_name
                            lbl = "_Lint"
                    elif "format" in lbl_l or (i == 2 and not lbl):
                        if on_python_proj or "/usr" in linter_cmd:
                            cmd = formatter_cmd + file_name
                            lbl = "_Format"
                    elif i == 0 and on_python_proj:
                        cmd = main_cmd + " -m py_compile " + file_name
                elif on_python_proj:
                    if "test" in lbl_l or (i == 1 and not lbl):
                        lbl = "_Test"
                        cmd = main_cmd + " -m {0} {1}".format(testing_cmd, file_name)
                    elif i == 0:
                        cmd = main_cmd + " " + file_name
                if cmd is None or not lbl:
                    continue
                Geany.build_set_menu_item(bs, grp, i, Geany.BuildCmdEntries.COMMAND, cmd)
                Geany.build_set_menu_item(
                    bs, grp, i, Geany.BuildCmdEntries.WORKING_DIR, wd,
                )
                Geany.build_set_menu_item(
                    bs, grp, i, Geany.BuildCmdEntries.LABEL, lbl,
                )

    def set_proj_settings(self, childrens):
        if not childrens:
            return
        for child in childrens:
            name = child.get_name()
            if name not in PYCODING_CNF:
                continue
            pth = None
            if name == test_utils.PYTHON_TESTING_LBL:
                pth = child.get_active_text()
            elif name == venv_utils.PYTHON_PTH_LBL:
                if venv_utils.has_pyenv:
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
            else:
                pth = int(child.get_active())
            if pth is not None:
                self.proj_settings[name] = pth

    def get_proj_bp_and_cnf(self):
        proj = self.geany_plugin.geany_data.app.project
        if proj:
            cnf = configparser.ConfigParser()
            cnf.read(proj.file_name)
            base_path = proj.base_path
        else:
            cnf = None
            base_path = None
        return base_path, cnf

    def on_pytest_click(self, item=None):
        cur_doc = Geany.document_get_current()
        if not self.is_doc_python(cur_doc):
            return True
        base_path, cnf = self.get_proj_bp_and_cnf()
        if base_path is None:
            return True
        python_cmd = venv_utils.get_possible_cmd(venv_utils.py_cmd, project=base_path, config=cnf)
        test_module = self.proj_settings.get(test_utils.PYTHON_TESTING_LBL)
        action = test_utils.MODULE_MAP.get(test_module).get("run")
        test_cmd = [str(python_cmd), "-m", test_module] + action
        fp = cur_doc.real_path or cur_doc.file_name

        def on_test_run_exit(*args):
            term = args[0]
            if term:
                text, _ = term.get_text_range(0, 0, term.get_row_count() + 1, 0)
                test_utils.on_python_test_done(
                    text, filepath=cur_doc.real_path or cur_doc.file_name
                )
                if text:
                    Geany.msgwin_switch_tab(self.testing_win.testout_page, False)

        fp = fp.replace(base_path, "")
        test_cmd.append(fp[1:] if fp.startswith("/") else fp)
        self.testing_win.output_vte.connect("child-exited", on_test_run_exit)
        self.testing_win.output_vte.reset(True, True)
        venv_utils.executor.submit(
            test_utils.run_python_test_file,
            test_cmd,
            base_path,
            self.testing_win.output_vte,
            check_first=True,
        )
        return True

    def on_refactor_item_click(self, item=None):
        cur_doc = Geany.document_get_current()
        if not (
            HAS_JEDI
            and self.is_doc_python(cur_doc)
            and self.get_keyfile_pref(jedi_key, pref_type=0)
        ):
            return True
        sci = cur_doc.editor.sci
        contents = sci.get_contents(-1)
        if not contents:
            return True
        pos = sci.get_current_position()
        word_at_pos = cur_doc.editor.get_word_at_pos(pos, GEANY_WORDCHARS + ".")
        if not (
            Geany.highlighting_is_code_style(sci.get_lexer(), sci.get_style_at(pos))
            and word_at_pos
        ):
            return True
        dlg = utils.JediRefactorDialog(
            self.geany_plugin.geany_data.main_widgets.window,
            _(utils.refactor_dlg.format(word_at_pos)),
            _("_Apply"),
        )
        dlg.set_and_show(_)
        ok = dlg.run()
        names = [None, None]
        action = None
        if ok in (Gtk.ResponseType.APPLY, Gtk.ResponseType.OK):
            action = dlg.cur_action
            for child in dlg.box.get_children():
                if not isinstance(child, Gtk.Entry):
                    continue
                txt = child.get_text()
                if child.is_visible():
                    if child.get_name() == utils.refactor_nn:
                        names[0] = txt
                    else:
                        names[1] = txt
            dlg.destroy()
        if not action:
            return True
        cur_line = sci.get_current_line()
        cur_column = sci.get_col_from_position(pos)
        future = venv_utils.executor.submit(
            utils.start_jedi_refactor,
            sci.get_contents(-1),
            cur_doc.real_path or cur_doc.file_name,
            cur_line + 1,
            cur_column,
            action=action,
            action_args=names,
        )
        future.add_done_callback(utils.on_refactor_done)
        return True

    def get_keyfile_pref(self, name, pref_type=1, default="", only_set=False):
        key_method = {
            1: (self.keyfile.get_string, self.keyfile.set_string),
            0: (self.keyfile.get_boolean, self.keyfile.set_boolean),
        }.get(pref_type)
        if only_set:
            key_method[1](venv_utils.NAME, name, default)
            return
        try:
            return key_method[0](venv_utils.NAME, name)
        except GLib.Error:
            key_method[1](venv_utils.NAME, name, default)
            return default

    def on_pyconsole_item_click(self, item=None):
        proj = self.geany_plugin.geany_data.app.project
        ipython_cmd = "ipython"
        if proj:
            venv_py = venv_utils.get_possible_cmd(
                ipython_cmd, project=proj, check_system=False
            ) or venv_utils.get_possible_cmd(venv_utils.py_cmd, project=proj, check_system=False)
            workdir = proj.base_path
        else:
            venv_py = venv_utils.get_possible_cmd(ipython_cmd) or venv_utils.get_possible_cmd(
                venv_utils.py_cmd
            )
            workdir = None
        win, term = test_utils.python_console([venv_py], workdir=workdir)
        win.set_title("Pycoding Console")

        def on_child_exit(term, *args):
            parent = term.get_parent()
            term.destroy()
            parent.destroy()

        term.connect("child-exited", on_child_exit)
        win.show_all()
        win.present()

    def on_jgoto_item_click(self, item=None):
        cur_doc = Geany.document_get_current()
        if not (
            HAS_JEDI
            and self.is_doc_python(cur_doc)
            and self.get_keyfile_pref(jedi_key, pref_type=0)
        ):
            return True
        self.get_jedi_doc_and_signatures(cur_doc.editor, calltip=False)

    def do_enable(self):
        geany_data = self.geany_plugin.geany_data
        self.pycoding_config = Path(geany_data.app.configdir).joinpath(
            "plugins", "{0}.conf".format(venv_utils.NAME)
        )
        conf_file = str(self.pycoding_config)
        o = geany_data.object
        self.doc_close = o.connect("document-close", self.on_document_close)
        # load startup config
        self.keyfile = GLib.KeyFile.new()
        if self.pycoding_config.is_file():
            self.keyfile.load_from_file(conf_file, GLib.KeyFileFlags.KEEP_COMMENTS)
        for cnf, v in itertools.chain(DEFAULTS.items(), ENABLE_CONFIGS.items()):
            if isinstance(v, dict):
                pref_type = 1
                default = v.get("default")
            else:
                pref_type = 0
                default = True
            pref = self.get_keyfile_pref(cnf, pref_type=pref_type, default=default)
            if cnf == venv_key and pref:
                self.set_pyproj_signal_handler(o)
            if cnf == formatter_key and pref:
                self.formatter_methods = format_utils.get_formatter(pref, with_name=True)
        self.keyfile.save_to_file(conf_file)
        self.set_lint_signal_handler(o)
        self.set_format_signal_handler(o)
        self.jedi_handler = o.connect("editor-notify", self.on_editor_notify)
        enable_pyconsole = self.get_keyfile_pref(pyconsole_key, pref_type=0)
        is_pydoc_available = utils.parso is not None
        keys = self.add_key_group(
            venv_utils.NAME,
            2 + int(is_pydoc_available) + (int(HAS_JEDI) * 2) + int(enable_pyconsole),
        )
        fpc = _("Pycoding Run Formatter on File")
        self.format_item = Geany.ui_image_menu_item_new(Gtk.STOCK_EXECUTE, fpc)
        self.format_item.connect("activate", self.on_format_item_click)
        geany_data.main_widgets.tools_menu.append(self.format_item)
        keys.add_keybinding("pycoding_format_file", fpc, self.format_item, 0, 0)
        if is_pydoc_available:
            dpc = _("Pycoding Generate Docstring for Block")
            self.document_item = Geany.ui_image_menu_item_new(Gtk.STOCK_EXECUTE, dpc)
            self.document_item.connect("activate", self.on_documentation_item_click)
            geany_data.main_widgets.editor_menu.append(self.document_item)
            keys.add_keybinding("pycoding_generate_docstring", dpc, self.document_item, 0, 0)
        if HAS_JEDI:
            rpc = _("Pycoding Refactor")
            self.refactor_item = Geany.ui_image_menu_item_new(Gtk.STOCK_EXECUTE, rpc)
            self.refactor_item.connect("activate", self.on_refactor_item_click)
            geany_data.main_widgets.editor_menu.append(self.refactor_item)
            keys.add_keybinding("pycoding_jedi_refactor", rpc, self.refactor_item, 0, 0)
            jpc = _("Pycoding Goto File")
            self.jgoto_item = Geany.ui_image_menu_item_new(Gtk.STOCK_EXECUTE, jpc)
            self.jgoto_item.connect("activate", self.on_jgoto_item_click)
            geany_data.main_widgets.editor_menu.append(self.jgoto_item)
            keys.add_keybinding("pycoding_jedi_goto", jpc, self.jgoto_item, 0, 0)
        if enable_pyconsole:
            pc = _("Pycoding Console")
            self.pyconsole_item = Geany.ui_image_menu_item_new(Gtk.STOCK_EXECUTE, pc)
            self.pyconsole_item.connect("activate", self.on_pyconsole_item_click)
            geany_data.main_widgets.tools_menu.append(self.pyconsole_item)
            keys.add_keybinding("pycoding_python_console", pc, self.pyconsole_item, 0, 0)
            self.pyconsole_item.show()
        rpf = _("Pycoding Run Test on File")
        self.pytest_item = Geany.ui_image_menu_item_new(Gtk.STOCK_EXECUTE, rpf)
        self.pytest_item.connect("activate", self.on_pytest_click)
        geany_data.main_widgets.tools_menu.append(self.pytest_item)
        keys.add_keybinding("pycoding_run_test", fpc, self.pytest_item, 0, 0)
        self.testing_win = test_utils.PythonTestingWindow()
        return True

    def do_disable(self):
        geany_data = self.geany_plugin.geany_data
        o = geany_data.object
        o.disconnect(self.jedi_handler)
        o.disconnect(self.doc_close)
        self.set_lint_signal_handler(o, disable=True)
        self.set_format_signal_handler(o, disable=True)
        self.set_pyproj_signal_handler(o, disable=True)
        if self.format_item:
            geany_data.main_widgets.tools_menu.remove(self.format_item)
            self.format_item.destroy()
            self.format_item = None
        if self.document_item:
            geany_data.main_widgets.editor_menu.remove(self.document_item)
            self.document_item.destroy()
            self.document_item = None
        if self.refactor_item:
            geany_data.main_widgets.editor_menu.remove(self.refactor_item)
            self.refactor_item.destroy()
            self.refactor_item = None
        if self.jgoto_item:
            geany_data.main_widgets.editor_menu.remove(self.jgoto_item)
            self.jgoto_item.destroy()
            self.jgoto_item = None
        if self.pytest_item:
            geany_data.main_widgets.tools_menu.remove(self.pytest_item)
            self.pytest_item.destroy()
            self.pytest_item = None
        if self.pyconsole_item:
            geany_data.main_widgets.tools_menu.remove(self.pyconsole_item)
            self.pyconsole_item.destroy()
            self.pyconsole_item = None
        self.formatter_methods = [None]

    @staticmethod
    def scintilla_command(sci, sci_msg, sci_cmd, lparam, data):
        if sci_cmd:
            sci.send_command(sci_cmd)
        if data:
            data = ctypes.c_char_p(data.encode("utf8"))
            tt = ctypes.cast(data, ctypes.c_void_p).value
            sci.send_message(sci_msg, lparam, tt)

    @staticmethod
    def check_doc_is_python(doc):
        return (
            doc is not None
            and doc.is_valid
            and doc.file_type.id == Geany.FiletypeID.FILETYPES_PYTHON
        )

    def is_doc_python(self, doc):
        is_python = self.check_doc_is_python(doc)
        if is_python:
            self.document_item.show()
            self.format_item.show()
            self.pytest_item.show()
            self.refactor_item.show()
            self.jgoto_item.show()
        else:
            self.document_item.hide()
            self.format_item.hide()
            self.pytest_item.hide()
            self.refactor_item.hide()
            self.jgoto_item.hide()
        return is_python

    def call_jedi(self, doc_content, cur_doc=None, text=None, loc=None):
        if not cur_doc:
            cur_doc = Geany.document_get_current()
        if loc:
            line = loc[0] + 1
            col = loc[1]
        else:
            line = None
            col = None
        data = ""
        fp = cur_doc.real_path or cur_doc.file_name
        self.jedi_script = utils.Script(
            doc_content, path=fp, project=self.proj_env or utils.jedi.Project(Path(fp).parent),
        )
        stop_len = self.geany_plugin.geany_data.editor_prefs.autocompletion_max_entries
        try:
            for count, complete in enumerate(self.jedi_script.complete(line=line, column=col)):
                if not complete:
                    continue
                name = complete.name
                if name != "__init__" and utils.magic_method_re.match(name):
                    continue
                name = complete.name
                sig = complete.get_signatures()
                if text is not None:
                    if text != name:
                        continue
                    func_symbol_ = []
                    if not complete.is_keyword:
                        if sig:
                            sig = sig[0]
                            ret_string = sig._signature.annotation_string
                            for_doc = []
                            for p in sig.params:
                                for_doc.append(p.to_string())
                                kind = p.kind
                                if p.infer_default() or kind in utils.ignore_kind:
                                    continue
                                if kind == inspect.Parameter.KEYWORD_ONLY:
                                    func_symbol_.append(p.name + "=" + utils.cursor_marker)
                                else:
                                    func_symbol_.append(utils.cursor_marker)
                            params = ",\n    ".join(for_doc)
                            sig = "{0}({1})".format(text, params)
                            if ret_string:
                                sig += " -> " + ret_string
                    complete.signature_ = sig or None
                    complete.func_symbol_ = ",".join(func_symbol_)
                    complete.docstring_ = complete.docstring()
                    self.select_cache[text].append(complete)
                    return complete
                if count > 0:
                    data += "\n"
                data += name
                data += "?1" if sig else "?2"
                if count == stop_len + 1:
                    data += "..."
                    break
            return data
        except (ValueError, AttributeError) as error:
            print(error)
            return

    def get_jedi_doc_and_signatures(self, editor, pos=None, text=None, calltip=True):
        sci = editor.sci
        if pos is None:
            pos = sci.get_current_position()
        if text is None:
            word_at_pos = editor.get_word_at_pos(pos, GEANY_WORDCHARS) or ""
        else:
            word_at_pos = text or ""
        word_at_pos = word_at_pos.strip()
        if not word_at_pos:
            return
        if word_at_pos in keyword.kwlist:
            return
        from_cache = self.select_cache.get(word_at_pos)
        complete = None
        line = sci.get_current_line()
        column = sci.get_col_from_position(pos)
        if from_cache:
            complete = from_cache[-1]
        if not complete:
            doc_content = (sci.get_contents_range(0, pos) or "").rstrip()
            if not doc_content:
                return
            complete = self.call_jedi(
                doc_content, cur_doc=editor.document, text=word_at_pos, loc=(line, column)
            )
        if not complete or pos < 0:
            return
        if calltip:
            if not complete.signature_:
                return
            self.scintilla_command(
                sci,
                sci_cmd=GeanyScintilla.SCI_CALLTIPCANCEL,
                sci_msg=GeanyScintilla.SCI_CALLTIPSHOW,
                lparam=pos,
                data=complete.signature_,
            )
            sci.send_message(GeanyScintilla.SCI_CALLTIPSETHLT, 0, len(complete.signature_))
        elif calltip is None:
            docstring = complete.docstring_
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
            Geany.msgwin_compiler_add_string(
                Geany.MsgColors.BLACK, "Doc:\n\n{0}".format(docstring)
            )
            Geany.msgwin_switch_tab(Geany.MessageWindowTabNum.COMPILER, False)
        else:
            if complete.is_stub():
                return
            mp = complete.module_path
            if not mp:
                return
            try:
                line, col = complete.get_definition_start_position()
            except TypeError:
                line = 1
            Geany.navqueue_goto_line(
                editor.document,
                Geany.document_find_by_real_path(mp)
                or Geany.document_open_file(mp, False, None, None),
                line,
            )

    def on_editor_notify(self, g_obj, editor, nt):
        cur_doc = editor.document or Geany.document_get_current()
        sci = editor.sci
        pos = sci.get_current_position()
        if (
            not (
                HAS_JEDI
                and self.is_doc_python(cur_doc)
                and self.get_keyfile_pref(jedi_key, pref_type=0)
            )
            or pos < 2
            or not Geany.highlighting_is_code_style(sci.get_lexer(), sci.get_style_at(pos - 2))
        ):
            return False
        try:
            nt_text = getattr(nt, "text", None)
        except UnicodeDecodeError:
            pass
        index = nt.nmhdr.code
        if index == GeanyScintilla.SCN_CHARADDED:
            char = chr(nt.ch)
            if char in utils.code_char_check:
                return False
            self.complete_python(editor, char, pos)
        elif index == GeanyScintilla.SCN_AUTOCCOMPLETED:
            snippet = editor.find_snippet(nt_text)
            if snippet:
                Geany.keybindings_send_command(
                    Geany.KeyGroupID.EDITOR, Geany.KeyBindingID.EDITOR_COMPLETESNIPPET
                )
                return False
            self.get_jedi_doc_and_signatures(editor, pos, text=nt_text)
        elif index == GeanyScintilla.SCN_AUTOCSELECTION:
            self.get_jedi_doc_and_signatures(editor, pos, text=nt_text, calltip=None)
        elif index == GeanyScintilla.SCN_DWELLSTART:
            self.get_jedi_doc_and_signatures(editor, nt.position)
        elif index == GeanyScintilla.SCN_DWELLEND:
            self.scintilla_command(
                sci,
                sci_cmd=GeanyScintilla.SCI_CALLTIPCANCEL,
                sci_msg=None,
                lparam=None,
                data=None,
            )
        return False

    def complete_python(self, editor, char, pos=None):
        sci = editor.sci
        if not pos:
            pos = sci.get_current_position()
        if char == "(":
            text = editor.get_word_at_pos(pos - 1, GEANY_WORDCHARS)
            if text:
                cache = self.select_cache.get(text) or []
                cache_choice = set()
                for sym in cache:
                    if sym.func_symbol_:
                        cache_choice.add(sym.func_symbol_)
                if cache_choice:
                    if len(cache_choice) == 1:
                        editor.insert_text_block(cache_choice.pop(), pos, -1, -1, True)
                    else:
                        self.scintilla_command(
                            sci,
                            sci_cmd=GeanyScintilla.SCI_AUTOCCANCEL,
                            sci_msg=GeanyScintilla.SCI_AUTOCSHOW,
                            lparam=0,
                            data="\n".join(cache_choice),
                        )

            return
        col = sci.get_col_from_position(pos)
        line = sci.get_current_line()
        loc = None
        word_at_pos = sci.get_line(line)
        if not word_at_pos:
            return
        check_snippet = False
        word_at_pos = word_at_pos.lstrip()
        rootlen = len(word_at_pos)
        import_check = word_at_pos.startswith(utils.import_kw)
        if rootlen <= 3:
            kw_list = []
            for kw in keyword.kwlist:
                if kw.startswith(word_at_pos.strip()):
                    if editor.find_snippet(kw):
                        kw_list.append(kw + "?1")
                        check_snippet = True
        elif import_check:
            doc_content = word_at_pos
            check_snippet = False
            loc = [0, rootlen - 1]
        if not (import_check or check_snippet):
            doc_content = (sci.get_contents_range(0, pos) or "").rstrip()
        cur_doc = editor.document
        if not check_snippet:
            word_at_pos = editor.get_word_at_pos(pos, GEANY_WORDCHARS + ".")
            if not (word_at_pos and rootlen):
                return
            try:
                _, word_at_pos = word_at_pos.rsplit(".", maxsplit=1)
            except ValueError:
                pass
            rootlen = len(word_at_pos) if word_at_pos else 0
            if not loc:
                loc = (line, col)
        if check_snippet:
            data = "\n".join(kw_list)
        else:
            data = self.call_jedi(doc_content, cur_doc=cur_doc, loc=loc)
        self.scintilla_command(
            sci,
            sci_cmd=GeanyScintilla.SCI_AUTOCCANCEL,
            sci_msg=GeanyScintilla.SCI_AUTOCSHOW,
            lparam=rootlen,
            data=data,
        )

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
            if name in DEFAULTS:
                pref_type = 1
                if name == formatter_key:
                    self.formatter_methods = format_utils.get_formatter(cnf_val, with_name=True)
            else:
                pref_type = 0
            self.get_keyfile_pref(name, pref_type=pref_type, default=cnf_val, only_set=True)
        self.keyfile.save_to_file(conf_file)
        obj = self.geany_plugin.geany_data.object
        self.set_lint_signal_handler(obj)
        self.set_format_signal_handler(obj)

    def do_configure(self, dialog):
        align = Gtk.Alignment.new(0, 0, 1, 0)
        align.props.left_padding = 12
        vbox = Gtk.VBox(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        vbox.set_border_width(2)
        for name, cnf in itertools.chain(ENABLE_CONFIGS.items(), DEFAULTS.items()):
            key_pref = int(name in DEFAULTS)
            cnf_val = self.get_keyfile_pref(name, pref_type=key_pref)
            if isinstance(cnf, dict):
                combo = Gtk.ComboBoxText()
                combo.set_name(name)
                for c_name in cnf["options"]:
                    if c_name == cnf_val:
                        combo.insert_text(0, cnf_val)
                    else:
                        combo.append_text(c_name)
                combo.set_active(0)
                vbox.add(Gtk.Label(cnf["label"]))
                vbox.add(combo)
            else:
                button = Gtk.CheckButton(cnf[0])
                button.set_tooltip_text(cnf[1])
                button.set_name(name)
                button.set_active(cnf_val)
                vbox.add(button)
        align.add(vbox)
        dialog.connect("response", self.on_configure_response, vbox)
        return align
