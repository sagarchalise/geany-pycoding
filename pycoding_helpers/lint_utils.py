import io
import tokenize
import importlib
import operator
import collections


def is_mod_available(modname):
    try:
        return importlib.util.find_spec(modname) is not None
    except ImportError:
        return False


annotation_indicators = {33, 34}
DEFAULT_LINTER = "flake8"
LINTER_TYPES = {DEFAULT_LINTER, "pylint", "pycodestyle", "pyflakes"}
mapped_key = {
    "warning": 11,
    "error": 15,
    "convention": 12,
    "refactor": 10,
    "fatal": 13,
}


def get_patched_checker(name=DEFAULT_LINTER):
    is_lint = is_mod_available(name)
    if not is_lint:
        raise ImportError("No linter: {0}".format(name))

    def get_severity(err_code):
        if err_code.startswith(("E999", "E901", "E902", "E113", "F82")):
            key = "fatal"
        elif err_code.startswith(("VNE", "W6", "E800")):
            key = "refactor"
        elif err_code.startswith("E"):
            key = "error"
        elif err_code.startswith(
            ("W", "F402", "F403", "F405", "E722", "E112", "F812", "F9", "F82", "F83")
        ):
            key = "warning"
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


def check_python_code(filename, file_content, line_length, linter=DEFAULT_LINTER, syn_errors=None):
    if syn_errors is None:
        syn_errors = []
    check_and_get_results = get_patched_checker(name=linter)
    results = collections.defaultdict(dict)
    diagnostics = []
    for result in check_and_get_results(filename, file_content, line_length):
        severity, line, col, msg = result
        start_line = max(line - 1, 0)
        results[start_line][severity] = (col, msg)
    for error in syn_errors:
        act_line = error.line - 1
        results[act_line]["fatal"] = (error.column, error.get_message())

    for line, vals in results.items():
        if vals:
            severity = ""
            msg = ""
            min_col = -5
            for sev, messages in vals.items():
                col, message = messages
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
                if min_col < 0 or min_col > col:
                    min_col = col
            diagnostics.append((mapped_key[severity], line, min_col, msg))
    diagnostics.sort(key=operator.itemgetter(1))
    return diagnostics
