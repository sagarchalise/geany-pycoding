import ctypes
import importlib
import site
import subprocess
import sys
import os
import io
import re
import tokenize
import configparser
import collections
import itertools
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
    "enable_venv_project": (
        _("Initialize Python Project Properties."),
        _("Requires pyenv, virtualenv or venv."),
    ),
    "enable_jedi": (
        _("Jedi Completion/Documentation/Signatures for Python."),
        _("Requires jedi."),
    ),
    "enable_autolint": (
        _("Enable auto lint on save."),
        _("Requires linting config in build or linter for annotation."),
    ),
    "enable_annotate_lint": (
        _("Enable Annotation on lint."),
        _("Requires a linter and will disable build linting."),
    ),
    "enable_autoformat": (
        _("Enable auto format on save."),
        _("Requires formatter such as black, yapf etc."),
    ),
}


def is_mod_available(modname):
    try:
        return importlib.util.find_spec(modname) is not None
    except ImportError:
        return False


DEFAULT_LINTER = "flake8"
LINTERS = {DEFAULT_LINTER, "pylint", "pycodestyle", "pyflakes"}

mapped_key = {
    "warning": 15,
    "error": 13,
    "convention": 12,
    "refactor": 10,
    "fatal": 13,
}


def get_patched_checker(name=DEFAULT_LINTER):
    is_linter_available = is_mod_available(name)
    if not is_linter_available:
        raise ImportError("No linter: {0}".format(name))

    def get_severity(err_code):
        if err_code.startswith(("E999", "E901", "E902", "E113", "F82")):
            key = "fatal"
        elif err_code.startswith(
            ("W", "F402", "F403", "F405", "E722", "E112", "F812", "F9", "F82", "F83")
        ):
            key = "warning"
        elif err_code.startswith("E9"):
            key = "error"
        elif err_code.startswith("VNE"):
            key = "refactor"
        elif err_code not in mapped_key:
            key = "convention"
        else:
            key = err_code
        return key

    def read_file_line(fd):
        try:
            (coding, lines) = tokenize.detect_encoding(fd.readline)
            textfd = io.TextIOWrapper(fd, coding, line_buffering=True)
            return [l.decode(coding) for l in lines] + textfd.readlines()
        except (LookupError, SyntaxError, UnicodeError):
            return fd.readlines()

    if name == "flake8":
        from flake8.api.legacy import get_style_guide
        from flake8.checker import FileChecker, processor

        class PatchedFileChecker(FileChecker):
            def __init__(self, filename, checks, options, file_lines=None):
                self.file_lines = file_lines
                super().__init__(filename, checks, options)

            def _make_processor(self):
                return processor.FileProcessor(self.filename, self.options, lines=self.file_lines)

        sg = get_style_guide()

        def check_and_get_results(filename, file_content=None, line_length=79):
            py_file = io.BytesIO(file_content.encode("utf8"))
            flake8_mngr = sg._file_checker_manager
            flake8_mngr.options.max_line_length = line_length
            checks = flake8_mngr.checks.to_dictionary()
            file_chk = PatchedFileChecker(
                filename, checks, flake8_mngr.options, file_lines=read_file_line(py_file)
            )
            file_chk.run_checks()
            g = sg._application.guide
            formatter = g.formatter
            formatter.write = lambda x, y: None
            for result in file_chk.results:
                err_code, line, col, msg, code_str = result
                if g.handle_error(
                    code=err_code,
                    filename=filename,
                    line_number=line,
                    column_number=col,
                    text=msg,
                    physical_line=code_str,
                ):
                    severity = get_severity(err_code)
                    yield (severity, line, col, msg)

        return check_and_get_results
    elif name == "pycodestyle":
        from pycodestyle import Checker

        def check_and_get_results(filename, file_content=None, line_length=79):
            py_file = io.BytesIO(file_content.encode("utf8"))
            chk = Checker(filename, lines=read_file_line(py_file))
            chk.max_line_length = line_length
            results = chk.check_all()
            results = chk.report._deferred_print
            for result in results:
                line, col, err_code, msg, smry = result
                severity = get_severity(err_code)
                yield (severity, line, col, msg)

        return check_and_get_results
    elif name == "pyflakes":
        from pyflakes.api import check
        from pyflakes.reporter import Reporter

        class PyFlakeReporter(Reporter):
            def __init__(self):
                self.errors = []

            def unexpectedError(self, filename, msg):
                self.errors.append(("E9", 1, 1, msg))

            def syntaxError(self, filename, msg, lineno, offset, text):
                self.errors.append(("E9", lineno, offset, msg))

            def flake(self, message):
                self.errors.append(
                    ("", message.lineno, message.col, message.message % message.message_args)
                )

        def check_and_get_results(filename, file_content=None, line_length=79):
            rprter = PyFlakeReporter()
            check(file_content, filename, reporter=rprter)
            for result in rprter.errors:
                err_code, line, col, msg = result
                severity = get_severity(err_code)
                yield (severity, line, col, msg)

        return check_and_get_results
    elif name == "pylint":
        import os
        from pylint.lint import PyLinter
        from pylint import utils
        from pylint import interfaces
        from astroid import MANAGER, builder
        from pylint import reporters

        bd = builder.AstroidBuilder(MANAGER)

        class PatchedPyLinter(PyLinter):
            def check(self, filename, file_content):
                # initialize msgs_state now that all messages have been registered into
                # the store
                for msg in self.msgs_store.messages:
                    if not msg.may_be_emitted():
                        self._msgs_state[msg.msgid] = False
                basename = (
                    os.path.splitext(os.path.basename(filename))[0] if filename else "untitled"
                )
                walker = utils.PyLintASTWalker(self)
                self.config.reports = True
                _checkers = self.prepare_checkers()
                tokencheckers = [
                    c
                    for c in _checkers
                    if interfaces.implements(c, interfaces.ITokenChecker) and c is not self
                ]
                rawcheckers = [
                    c for c in _checkers if interfaces.implements(c, interfaces.IRawChecker)
                ]
                # notify global begin
                for checker in _checkers:
                    checker.open()
                    if interfaces.implements(checker, interfaces.IAstroidChecker):
                        walker.add_checker(checker)
                self.set_current_module(basename, filename)
                ast_node = bd.string_build(file_content, filename, basename)
                self.file_state = utils.FileState(basename)
                self._ignore_file = False
                # fix the current file (if the source file was not available or
                # if it's actually a c extension)
                self.current_file = ast_node.file  # pylint: disable=maybe-no-member
                self.check_astroid_module(ast_node, walker, rawcheckers, tokencheckers)
                # warn about spurious inline messages handling
                spurious_messages = self.file_state.iter_spurious_suppression_messages(
                    self.msgs_store
                )
                for msgid, line, args in spurious_messages:
                    self.add_message(msgid, line, None, args)
                # notify global end
                self.stats["statement"] = walker.nbstatements
                for checker in reversed(_checkers):
                    checker.close()

        def check_and_get_results(filename, file_content=None, line_length=79):
            if not isinstance(file_content, str):
                file_content = file_content.decode("utf8") if file_content else ""
            if not filename:
                filename = ""
            linter = PatchedPyLinter()
            linter.load_default_plugins()
            rp = reporters.json.JSONReporter()
            linter.set_reporter(rp)
            linter.check(filename, file_content)
            for msg in rp.messages:
                yield get_severity(msg["type"]), msg["line"], msg["column"], "[{0}] {1}".format(
                    msg["message-id"], msg["message"]
                )

        return check_and_get_results


def check_python_code(filename, file_content, line_length, linter=DEFAULT_LINTER):
    check_and_get_results = get_patched_checker(name=linter)
    results = collections.defaultdict(dict)
    for result in check_and_get_results(filename, file_content, line_length):
        severity, line, _, msg = result
        start_line = max(line - 1, 0)
        results[start_line][severity] = msg
    for line, vals in results.items():
        if len(vals) > 1:
            severity = ""
            msg = ""
            for sev, message in vals.items():
                if not severity:
                    msg += message
                else:
                    msg += "\n" + message
                if sev in {"error", "fatal"}:
                    severity = sev
                elif sev == "warning":
                    severity = sev
                elif not severity:
                    severity = sev
        else:
            for severity, msg in vals.items():
                break
        yield (severity, line, msg)


DEFAULT_FORMATTER = "black"
FORMATTER_TYPES = {DEFAULT_FORMATTER, "autopep8", "yapf"}

DEFAULTS = {
    "formatter_name": {
        "label": "Code Formatter:",
        "default": DEFAULT_FORMATTER,
        "options": FORMATTER_TYPES,
    },
    "docstring_name": {"label": "Format for docstrings:", "default": "google", "options": []},
    "linter_name": {
        "label": "Code Linter for annotation:",
        "default": DEFAULT_LINTER,
        "options": LINTERS,
    },
}

is_pydoc_available = is_mod_available("pydocstring")
if is_pydoc_available:
    import pydocstring

    DEFAULTS["docstring_name"]["options"] = pydocstring.formatters._formatter_map


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
pyenv_versions = set()
if PYENV_HOME.exists():
    VIRTUALENV_HOME = PYENV_HOME.joinpath("versions")
    has_pyenv = True
    try:
        pyenv_versions = {
            p.strip()
            for p in subprocess.check_output(["pyenv", "versions", "--bare", "--skip-aliases"])
            .decode("utf8")
            .split("\n")
        }
        from_command = True
    except (subprocess.CalledProcessError, FileNotFoundError):
        pyenv_versions = {d.name for d in VIRTUALENV_HOME.iterdir() if d.is_dir()}
        from_command = False
    pyenv_versions.add("system")
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
                data += "..."
                break
        return data

    def append_project_venv(project, cnf=None):
        if not project:
            return sys.path
        site.addsitedir(project.base_path)
        proj_name = project.name
        if cnf is None:
            cnf = configparser.ConfigParser()
            cnf.read(project.file_name)
        already_pth = cnf.get(NAME, PYTHON_PTH_LBL, fallback=None)
        if not already_pth:
            already_pth = proj_name
        if not VIRTUALENV_HOME.is_dir():
            return sys.path
        for pth in VIRTUALENV_HOME.iterdir():
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
    already_venv = VIRTUALENV_HOME.joinpath(python_pth)
    if (
        not python_pth.lower().startswith(("3.", "pypy3"))
        and already_venv.joinpath("bin/python").exists()
    ):
        project_venv = already_venv
    else:
        status = "{0} in python venv creation for project: {1}".format("{0}", proj_name)
        if has_pyenv:
            project_venv = VIRTUALENV_HOME.joinpath(proj_name)
            if proj_name not in pyenv_versions:
                if from_command:
                    args = ["pyenv", "virtualenv", python_pth, proj_name]
                else:
                    args = "{0} -m venv {1}".format(
                        "python"
                        if python_pth == "system"
                        else VIRTUALENV_HOME.joinpath(python_pth).joinpath("bin/python"),
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
                venvwrapper = is_mod_available("virtualenvwrapper")
    pyenv_versions.add(project_venv.name)
    try:
        if venvwrapper and project_venv.is_dir():
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


def get_pyproj_properties(settings):
    box = Gtk.VBox(False, 0)
    for name in PYCODING_CNF:
        val = settings[name]
        if name == PYTHON_PTH_LBL:
            dir_label = Gtk.Label(_("Virtual Environment Source Python Path:"))
            dir_label.set_alignment(0, 0.5)
            if has_pyenv:
                button = Gtk.ComboBoxText()
                button.insert_text(0, val)
                for nam in pyenv_versions:
                    if not nam or val == nam:
                        continue
                    button.append_text(nam.strip())
                button.set_active(0)
            else:
                button = Gtk.Button(val)
                button.connect("clicked", PythonPorjectDialog.on_folder_clicked)
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
            button.set_active(val)
            button.set_name(name)
            box.add(button)
    return box


class PythonPorjectDialog(Gtk.Dialog):
    def __init__(self, parent, settings):
        Gtk.Dialog.__init__(
            self,
            _("Create Python Project"),
            parent,
            Gtk.DialogFlags.DESTROY_WITH_PARENT,
            (Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OK, Gtk.ResponseType.OK),
        )

        self.set_default_size(400, 200)
        box = get_pyproj_properties(settings)
        self.set_content_area(box)
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


class PycodingPlugin(Peasy.Plugin, Peasy.PluginConfigure):
    __gtype_name__ = NAME.title()
    pycoding_config = None
    format_signal = None
    lint_signals = None
    pyproj_signal = None
    format_item = None
    pytest_item = None
    document_item = None
    docstring_name = "google"
    DEFAULT_LINE_WIDTH = 79
    default_pth_dir = None
    formatter_name = DEFAULT_FORMATTER
    properties_tab = None
    settings = None
    linter_name = DEFAULT_LINTER
    enable_jedi = True
    line_compile = re.compile(r"(.*):(\d+):")

    def on_document_lint(self, user_data, doc):
        sci = doc.editor.sci
        sci.send_message(
            GeanyScintilla.SCI_MARKERDELETEALL,
            GeanyScintilla.SC_MARK_BACKGROUND,
            GeanyScintilla.SC_MARK_BACKGROUND,
        )
        Geany.msgwin_clear_tab(Geany.MessageWindowTabNum.COMPILER)
        Geany.msgwin_clear_tab(Geany.MessageWindowTabNum.MESSAGE)
        if not self.is_doc_python(doc):
            return False
        if self.enable_annotate_lint:
            contents = sci.get_contents(-1)
            sci.send_message(
                GeanyScintilla.SCI_ANNOTATIONSETVISIBLE,
                GeanyScintilla.ANNOTATION_BOXED,
                GeanyScintilla.ANNOTATION_INDENTED,
            )
            if contents:
                fp = doc.real_path or doc.file_name
                try:
                    checks = list(
                        check_python_code(
                            fp, contents, self.DEFAULT_LINE_WIDTH, linter=self.linter_name
                        )
                    )
                except ImportError as err:
                    checks = [["fatal", 0, str(err)]]
                sci.send_command(GeanyScintilla.SCI_ANNOTATIONCLEARALL)
                for severity, line, msg in checks:
                    self.scintilla_command(
                        sci,
                        sci_cmd=None,
                        sci_msg=GeanyScintilla.SCI_ANNOTATIONSETTEXT,
                        lparam=line,
                        data=msg,
                    )
                    sci.send_message(
                        GeanyScintilla.SCI_ANNOTATIONSETSTYLE, line, mapped_key[severity]
                    )
        else:
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
        line = -1
        code_contents = sci.get_contents(line)
        if not code_contents:
            return False
        try:
            style_paths = [str(Path(doc.real_path).parent)]
        except Exception:
            style_paths = []
        project = self.geany_plugin.geany_data.app.project
        if project:
            style_paths.append(project.base_path)
        Geany.msgwin_clear_tab(Geany.MessageWindowTabNum.MESSAGE)
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
                Geany.msgwin_msg_add_string(Geany.MsgColors.DARK_RED, line, doc, str(error))
                Geany.msgwin_switch_tab(Geany.MessageWindowTabNum.MESSAGE, False)
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
        if self.enable_autolint or self.enable_annotate_lint:
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
        self.pyproj_signal = []
        if self.enable_venv_project:
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
            if self.properties_tab:
                self.set_proj_settings(self.properties_tab.get_children())
                self.properties_tab.destroy()
                self.properties_tab = None
        else:
            self.settings = None

    def on_pyproj_open(self, obj, gtk_widget):
        self.properties_tab = get_pyproj_properties(self.settings)
        gtk_widget.append_page(self.properties_tab, Gtk.Label("Python"))
        gtk_widget.show_all()

    def on_pyproj_confirmed(self, obj, gtk_widget):
        self.set_proj_settings(gtk_widget.get_children())

    def on_pyproj_response(self, obj, proj_cnf_file):
        settings_read = True
        if not self.settings:
            self.settings = dict(zip(PYCODING_CNF, [False, False, False, "system"]))
            settings_read = False
        if not settings_read:
            for name in PYCODING_CNF:
                try:
                    if name == PYTHON_PTH_LBL:
                        setting = proj_cnf_file.get_string(NAME, name)
                        if not has_pyenv:
                            pth = Path(setting)
                            if pth.is_file():
                                setting = str(pth)
                        self.settings[name] = setting
                    else:
                        self.settings[name] = proj_cnf_file.get_boolean(NAME, name)
                except (GLib.Error, TypeError):
                    show_dlg = True
                    break
            else:  # nobreak
                show_dlg = False
        else:
            show_dlg = False
        childrens = []
        if show_dlg:
            dlg = PythonPorjectDialog(
                self.geany_plugin.geany_data.main_widgets.window, self.settings,
            )
            ok = dlg.run()
            if ok in (Gtk.ResponseType.APPLY, Gtk.ResponseType.OK):
                childrens = dlg.get_contenct_area()
                dlg.destroy()
        self.set_proj_settings(childrens)
        for name, value in self.settings.items():
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
        run_project_create(proj_name, base_path, self.settings)
        if self.settings.get(PYCODING_CNF[0]) and "*.py" not in pattern_list:
            pattern_list.append("*.py")
            proj_cnf_file.set_string_list("project", "file_patterns", pattern_list)

    def set_proj_settings(self, childrens):
        if not childrens:
            return
        for child in childrens:
            is_chkbtn = isinstance(child, Gtk.CheckButton)
            is_btn = (
                isinstance(child, Gtk.ComboBoxText) if has_pyenv else isinstance(child, Gtk.Button)
            )
            if not (is_chkbtn or is_btn):
                continue
            if is_chkbtn:
                self.settings[child.get_name()] = child.get_active()
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
                self.settings[child.get_name()] = pth

    def on_pytest_click(self, item=None):
        cur_doc = Geany.document_get_current()
        if not self.is_doc_python(cur_doc):
            return
        proj = self.geany_plugin.geany_data.app.project
        if proj:
            cnf = configparser.ConfigParser()
            cnf.read(proj.file_name)
        else:
            cnf = None
        append_project_venv(proj, cnf=cnf)
        if not is_mod_available("pytest"):
            return
        fp = cur_doc.real_path or cur_doc.file_name
        if proj:
            os.chdir(proj.base_path)
            if cnf:
                already_pth = cnf.get(NAME, PYTHON_PTH_LBL, fallback="system")
            else:
                already_pth = "system"
            pytest_cmd = (
                "pytest"
                if already_pth == "system"
                else VIRTUALENV_HOME.joinpath(already_pth).joinpath("bin/pytest")
            )
            fp = fp.replace(proj.base_path, ".")
        else:
            pytest_cmd = "pytest"
            fp = cur_doc.file_name
        err_lines = set()
        color = Geany.MsgColors.BLACK
        msgs = []
        ignore_count = 0
        with subprocess.Popen(
            [pytest_cmd, "--tb=short", "-p", "no:sugar" "-q", fp],  # shorter traceback format
            stdout=subprocess.PIPE,
            bufsize=1,
            universal_newlines=True,
        ) as p:
            for lines in p.stdout:
                if ignore_count < 3:
                    ignore_count += int(lines.startswith("="))
                    if ignore_count < 2:
                        continue
                if "FAILED" in lines:
                    color = Geany.MsgColors.RED
                msgs.append(lines)
                if os.path.basename(fp) not in lines:
                    continue
                reg_data = self.line_compile.search(lines)
                if not reg_data:
                    continue
                line = reg_data.group(2)
                try:
                    line = int(line)
                except (ValueError, TypeError):
                    continue
                else:
                    err_lines.add(line)

        sci = cur_doc.editor.sci
        sci.send_message(
            GeanyScintilla.SCI_MARKERDELETEALL,
            GeanyScintilla.SC_MARK_BACKGROUND,
            GeanyScintilla.SC_MARK_BACKGROUND,
        )
        sci.send_message(
            GeanyScintilla.SCI_MARKERDEFINE,
            GeanyScintilla.SC_MARK_BACKGROUND,
            GeanyScintilla.SC_MARK_BACKGROUND,
        )
        sci.send_message(
            GeanyScintilla.SCI_MARKERSETALPHA, GeanyScintilla.SC_MARK_BACKGROUND, 40,
        )
        for line in err_lines:
            sci.set_marker_at_line(line - 1, GeanyScintilla.SC_MARK_BACKGROUND)
        Geany.msgwin_clear_tab(Geany.MessageWindowTabNum.COMPILER)
        Geany.msgwin_clear_tab(Geany.MessageWindowTabNum.MESSAGE)
        Geany.msgwin_compiler_add_string(color, "\n".join(msgs))
        Geany.msgwin_switch_tab(Geany.MessageWindowTabNum.COMPILER, False)

    def do_enable(self):
        geany_data = self.geany_plugin.geany_data
        self.pycoding_config = Path(geany_data.app.configdir).joinpath(
            "plugins", "{0}.conf".format(NAME)
        )
        keys = self.add_key_group(
            NAME, 2 + int(bool(self.formatter_name)) + int(is_pydoc_available)
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
        rpf = _("Run pytest on Current File")
        self.pytest_item = Geany.ui_image_menu_item_new(Gtk.STOCK_EXECUTE, rpf)
        self.pytest_item.connect("activate", self.on_pytest_click)
        geany_data.main_widgets.editor_menu.append(self.pytest_item)
        keys.add_keybinding("test_python_file", fpc, self.pytest_item, 0, 0)
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
        for name in DEFAULTS:
            try:
                setattr(self, name, self.keyfile.get_string(NAME, name).lower())
            except GLib.Error:
                setattr(self, name, DEFAULTS[name].get("default"))
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
        if self.pytest_item:
            geany_data.main_widgets.editor_menu.remove(self.pytest_item)
            self.pytest_item.destroy()
            self.pytest_item = None

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
            self.pytest_item.show()
        else:
            self.document_item.hide()
            self.format_item.hide()
            self.pytest_item.hide()
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
                stop_len=self.geany_plugin.geany_data.editor_prefs.autocompletion_max_entries,
            )
        except ValueError:
            return

    def get_calltip(self, editor, pos, text=None):
        if text is None:
            word_at_pos = editor.get_word_at_pos(pos, GEANY_WORDCHARS)
            if not word_at_pos:
                return
        else:
            word_at_pos = text
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
        if not (HAS_JEDI and self.is_doc_python(cur_doc) and self.enable_jedi):
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
            if nt.nmhdr.code in {
                GeanyScintilla.SCN_DWELLSTART,
                GeanyScintilla.SCN_DWELLEND,
            }:
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
        Geany.msgwin_clear_tab(Geany.MessageWindowTabNum.MESSAGE)
        if not (can_doc and data):
            return
        Geany.msgwin_compiler_add_string(Geany.MsgColors.BLACK, "Doc:\n{0}".format(data))
        Geany.msgwin_switch_tab(Geany.MessageWindowTabNum.COMPILER, False)

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
            if name in DEFAULTS:
                self.keyfile.set_string(NAME, name, cnf_val)
            else:
                self.keyfile.set_boolean(NAME, name, cnf_val)
        self.keyfile.save_to_file(conf_file)
        obj = self.geany_plugin.geany_data.object
        self.set_lint_signal_handler(obj)
        self.set_format_signal_handler(obj)

    @staticmethod
    def create_combobox(name, value=None):
        data = DEFAULTS.get(name)
        label = Gtk.Label(data["label"])
        combo = Gtk.ComboBoxText()
        combo.set_name(name)
        for c_name in data["options"]:
            if c_name == value:
                combo.insert_text(0, value)
            else:
                combo.append_text(c_name)
        combo.set_active(0)
        return label, combo

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
        for name in DEFAULTS:
            value = getattr(self, name)
            label, combo = self.create_combobox(name, value)
            vbox.add(label)
            vbox.add(combo)
        align.add(vbox)
        dialog.connect("response", self.on_configure_response, vbox)
        return align
