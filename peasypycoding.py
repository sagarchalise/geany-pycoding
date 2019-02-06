import site
import ctypes
import asyncio
import importlib
from pathlib import Path

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

if DEFAULT_FORMATTER:
    print("Formatter: {}".format(DEFAULT_FORMATTER))

    def get_formatter(name=DEFAULT_FORMATTER):
        if name == "black":
            import black

            GetDefaultStyleForDir = None

            def FormatCode(content, style_config=None):
                try:
                    changed_content = black.format_file_contents(
                        content, line_length=style_config["line_width"], fast=False
                    )
                except black.NothingChanged:
                    return "", False
                else:
                    return changed_content, True

            return FormatCode, GetDefaultStyleForDir
        elif name == "yapf":
            from yapf.yapflib.yapf_api import FormatCode  # reformat a string of code
            from yapf.yapflib.file_resources import GetDefaultStyleForDir

            return FormatCode, GetDefaultStyleForDir
        elif name == "autopep8":
            from autopep8 import fix_code

            GetDefaultStyleForDir = None

            def FormatCode(content, style=None):
                return fix_code(content, options={"max_line_length": style["line_width"]})

            return FormatCode, GetDefaultStyleForDir
        return None, None


if HAS_JEDI:

    def jedi_complete(content, fp=None, text=None, sys_path=None, stop_len=25):
        script = jedi.Script(content, path=fp, sys_path=sys_path)
        data = ""
        doc = None
        for count, complete in enumerate(script.completions()):
            name = complete.name
            if name.startswith("__") and name.endswith("__"):
                continue
            if text is not None:
                if text != name:
                    continue
                if not (complete.is_keyword or complete.type == "module"):
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
            return
        venv_pth = Path.home().joinpath(".virtualenvs")
        if not venv_pth.is_dir():
            return
        for pth in venv_pth.iterdir():
            if pth.name.lower().startswith(proj_name.lower()) and pth.is_dir():
                st_pk = pth.glob("lib/pytho*/site-packages")
                st_pk = next(st_pk) if st_pk else None
                if not (st_pk and st_pk.is_dir()):
                    return
                proj_name = str(st_pk)
                break
        else:  # nobreak
            return
        site.addsitedir(proj_name)


NAME = "PyCoding"
DIR_LABEL = "Choose Directory"


async def run_async_geany_clear_cmd(tab_type):
    Geany.msgwin_clear_tab(tab_type)


async def run_async_geany_key_cmd(key_group, key_id):
    Geany.keybindings_send_command(key_group, key_id)


async def run_formatter(formatter, scintilla, line_width=99, style_paths=None):
    if style_paths is None:
        style_paths = []
    contents = scintilla.get_contents(-1)
    if not contents:
        return
    code_formatter, default_style_dir = get_formatter(DEFAULT_FORMATTER)
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
    format_text, formatted = code_formatter(contents, style_config=style)
    if formatted:
        pos = scintilla.get_current_position()
        scintilla.set_text(format_text)
        scintilla.set_current_position(pos, True)


class PycodingPlugin(Peasy.Plugin, Peasy.PluginConfigure):
    __gtype_name__ = NAME
    pycoding_config = None
    completion_words = None
    format_signal = None
    lint_signals = None
    format_item = None
    pyproj_item = None
    DEFAULT_LINE_WIDTH = 79
    default_pth_dir = None

    def on_document_lint(self, user_data, doc):
        run = self.check_and_lint(doc)
        self.set_menuitem_sensitivity(run)

    def set_menuitem_sensitivity(self, val):
        if self.format_item:
            self.format_item.set_sensitive(val)

    def on_document_close(self, user_data, doc):
        asyncio.run(run_async_geany_clear_cmd(Geany.MessageWindowTabNum.COMPILER))
        # asyncio.run(run_async_geany_clear_cmd(Geany.MessageWindowTabNum.MESSAGE))
        self.set_menuitem_sensitivity(False)

    def check_and_lint(self, doc, check=True):
        self.on_document_close(None, doc)
        if check and not self.is_doc_python(doc):
            return False

        asyncio.run(run_async_geany_key_cmd(Geany.KeyGroupID.BUILD, Geany.KeyBindingID.BUILD_LINK))
        if check:
            return True
        return False

    def on_format_item_click(self, item=None):
        cur_doc = Geany.Document.get_current()
        self.format_code(cur_doc)

    def format_code(self, doc):
        if not (DEFAULT_FORMATTER or self.is_doc_python(doc)):
            return False
        sci = doc.editor.sci
        contents = sci.get_contents(-1)
        if not contents:
            return False
        style_paths = [str(Path(doc.real_path).parent)]
        project = self.geany_plugin.geany_data.app.project
        if project:
            style_paths.append(project.base_path)
        asyncio.run(
            run_formatter(
                DEFAULT_FORMATTER, sci, style_paths=style_paths, line_width=self.DEFAULT_LINE_WIDTH
            )
        )
        self.check_and_lint(doc, check=False)
        return True

    def on_document_notify(self, user_data, doc):
        run = self.format_code(doc)
        self.set_menuitem_sensitivity(run)

    def set_format_signal_handler(self, geany_obj=None):
        if not DEFAULT_FORMATTER:
            return
        if not geany_obj:
            geany_obj = self.geany_plugin.geany_data.object
        if self.enable_autoformat:
            if self.format_signal:
                return
            self.format_signal = geany_obj.connect("document-save", self.on_document_notify)
        else:
            if self.format_signal:
                geany_obj.disconnect(self.format_signal)

    def set_lint_signal_handler(self, geany_obj=None):
        if not geany_obj:
            geany_obj = self.geany_plugin.geany_data.object
        signals = ("document-activate", "document-open")
        if self.enable_autolint:
            if self.lint_signals:
                return
            self.lint_signals = []
            for s in signals:
                self.lint_signals.append((geany_obj.connect(s, self.on_document_lint)))
        else:
            for signl in self.lint_signals:
                geany_obj.disconnect(signl)
            self.lint_signals = None

    def on_pyproj_item_click(self, item=None):
        pass

    def do_enable(self):
        geany_data = self.geany_plugin.geany_data
        self.pycoding_config = Path(geany_data.app.configdir).joinpath(
            "plugins", "{0}.conf".format(NAME.lower())
        )
        keys = self.add_key_group(NAME.lower(), 1 + int(bool(DEFAULT_FORMATTER)))
        if DEFAULT_FORMATTER:
            self.DEFAULT_LINE_WIDTH = max(
                geany_data.editor_prefs.long_line_column, geany_data.editor_prefs.line_break_column
            )
            fpc = _("Format Python Code")
            self.format_item = Geany.ui_image_menu_item_new(Gtk.STOCK_EXECUTE, fpc)
            self.format_item.connect("activate", self.on_format_item_click)
            geany_data.main_widgets.tools_menu.append(self.format_item)
            self.format_item.show_all()
            keys.add_keybinding("format_python_code", fpc, self.format_item, 0, 0)
        o = geany_data.object
        self.jedi_handler = o.connect("editor-notify", self.on_editor_notify)
        self.doc_close = o.connect("document-close", self.on_document_close)
        # load startup config
        self.keyfile = GLib.KeyFile.new()
        if self.pycoding_config.is_file():
            self.keyfile.load_from_file(str(self.pycoding_config), GLib.KeyFileFlags.KEEP_COMMENTS)
        for cnf in ENABLE_CONFIGS:
            try:
                setattr(self, cnf, self.keyfile.get_boolean(NAME.lower(), cnf[0]))
            except GLib.Error:
                setattr(self, cnf, True)
            else:
                self.default_pth_dir = self.keyfile.get(NAME.lower(), "include_dir")
        if self.enable_venv_project:
            ipp = ENABLE_CONFIGS.get("enable_venv_project")[0]
            self.pyproj_item = Geany.ui_image_menu_item_new(Gtk.STOCK_EXECUTE, ipp)
            self.pyproj_item.connect("activate", self.on_pyproj_item_click)
            keys.add_keybinding("init_python_project", ipp, self.pyproj_item, 0, 0)
            geany_data.main_widgets.tools_menu.append(self.pyproj_item)
            self.pyproj_item.show_all()
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
        if self.format_item:
            geany_data.main_widgets.tools_menu.remove(self.format_item)
            self.format_item.destroy()
            self.format_item = None
        if self.pyproj_item:
            geany_data.main_widgets.tools_menu.remove(self.pyproj_item)
            self.pyproj_item.destroy()
            self.pyproj_item = None

    @staticmethod
    def scintilla_command(sci, sci_msg, sci_cmd, lparam, data):
        sci.send_command(sci_cmd)
        if data:
            data = ctypes.c_char_p(data.encode("utf8"))
            tt = ctypes.cast(data, ctypes.c_void_p).value
            sci.send_message(sci_msg, lparam, tt)

    @staticmethod
    def is_doc_python(doc):
        return (
            doc is not None
            and doc.is_valid
            and doc.file_type.id == Geany.FiletypeID.FILETYPES_PYTHON
        )

    def on_editor_notify(self, g_obj, editor, nt):
        cur_doc = editor.document or Geany.Document.get_current()
        if not (HAS_JEDI or self.is_doc_python(cur_doc)):
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

    def append_site_paths(self, proj_name=None):
        append_project_venv(proj_name)
        if self.default_pth_dir:
            def_pth = Path(self.default_pth_dir)
            if not def_pth.is_dir():
                return
            # run for smthing
            # check pth file
            site.addsitedir(str(def_pth))

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
        self.append_site_paths(proj.name if proj else None)
        try:
            data = jedi_complete(doc_content, fp=fp, text=text)
        except ValueError as e:
            print(e)
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
            is_chkbtn = isinstance(child, Gtk.CheckButton)
            is_fcbtn = isinstance(child, Gtk.FileChooserButton)
            if not (is_chkbtn or is_fcbtn):
                continue
            if is_chkbtn:
                val = child.get_active()
                name = child.get_name()
                setattr(self, name, val)
            else:
                name = "include_dir"
                val = child.get_filename()
            self.keyfile.set_boolean(NAME.lower(), name, val)
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
        label = Gtk.Label(_("Extra Folder to include:"))
        label.set_alignment(0, 0.5)
        file_chooser = Gtk.FileChooserButton(DIR_LABEL, Gtk.FileChooserAction.SELECT_FOLDER)
        try:
            is_dir = self.default_pth_dir.is_dir()
        except AttributeError:
            pass
        else:
            if is_dir:
                file_chooser.set_current_name(str(self.default_pth_dir))
        vbox.add(label)
        vbox.add(file_chooser)
        align.add(vbox)
        dialog.connect("response", self.on_configure_response, vbox)
        return align
