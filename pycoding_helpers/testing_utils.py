import os
import re
import unittest
import collections
from gi.repository import Geany, GeanyScintilla, Gtk, GdkPixbuf, Vte, GLib

try:
    import pty
except ImportError:
    pty = None

fail_re = re.compile(r"\n\s.*line\s(\d+).*\n", re.MULTILINE)

DEFAULT_TESTING_LIB = "pytest"
PYTHON_TESTING_LBL = "test_lib"


MODULE_MAP = {
    DEFAULT_TESTING_LIB: {
        "discover": ["--collect-only", "-pno:sugar", "-q"],
        "run": ["--tb=native", "-s", "-v"],
    },
    "unittest": {"discover": unittest.defaultTestLoader, "run": ["-v"]},
}
TESTING_LIBRARIES = set(MODULE_MAP.keys())


def ready(*args):
    pass


def call_terminal_cmd(cmd, workdir, terminal=None):
    if terminal is None:
        terminal = Vte.Terminal()
    terminal.spawn_async(
        Vte.PtyFlags.DEFAULT,
        workdir,
        cmd,
        None,
        GLib.SpawnFlags.DO_NOT_REAP_CHILD,
        None,
        None,
        -1,
        None,
        ready,
    )
    return terminal


def run_python_test_file(test_cmd, base_path, vte, check_first=False):
    if check_first:
        test_mod = test_cmd[2]
        dis_cmd = MODULE_MAP.get(test_mod).get("discover")
        check_file = test_cmd[-1]
        if isinstance(dis_cmd, list):
            dis_cmd = test_cmd[:3] + dis_cmd
        if not discover_tests(dis_cmd, base_path, check_file=check_file):
            Geany.msgwin_status_add_string("Not a test file: " + check_file)
            Geany.msgwin_switch_tab(Geany.MessageWindowTabNum.STATUS, False)
            return

    return call_terminal_cmd(test_cmd, base_path, vte)


def on_python_test_done(test_output, filepath=None):
    cur_doc = Geany.document_get_current()
    if filepath != (cur_doc.real_path or cur_doc.file_name):
        return
    err_lines = set()
    ignore_count = 0
    found = fail_re.findall(test_output)
    for line in found:
        err_lines.add(int(line) - 1)
    if not err_lines:
        return
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
    not_set = True
    for line in sorted(err_lines):
        if not_set:
            sci.goto_line(line, False)
            not_set = False
        sci.set_marker_at_line(line, GeanyScintilla.SC_MARK_BACKGROUND)


def get_tree_store_with_collections(data, store=None, head=None, filename=None):
    if store is None:
        store = Gtk.TreeStore(GdkPixbuf.Pixbuf, str, GdkPixbuf.Pixbuf, str)
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, dict):
                fn = v.get("filename")
                test_list = v.get("tests")
            else:
                test_list = v
                fn = filename
            if k.endswith(".py"):
                icon = Gtk.IconTheme.get_default().load_icon("text-x-script", 16, 0)
            else:
                icon = Gtk.IconTheme.get_default().load_icon("folder", 16, 0)
            inner_head = store.append(
                head, [icon, k, Gtk.IconTheme.get_default().load_icon("system-run", 16, 0), fn,],
            )
            store = get_tree_store_with_collections(test_list, store, head=inner_head, filename=fn)
    else:
        if not data:
            return store
        for d in data:
            store.append(
                head,
                [
                    Gtk.IconTheme.get_default().load_icon("application-x-executable", 16, 0),
                    d,
                    Gtk.IconTheme.get_default().load_icon("system-run", 16, 0),
                    filename + "::" + d,
                ],
            )
    return store


def python_console(console_cmd, workdir=None, add_profile=True):
    if not workdir:
        workdir = os.environ.get("HOME")
    if not console_cmd:
        console_cmd = ["/usr/local/bin/ipython"]
    add_profile = False
    for cmd in console_cmd:
        if not cmd.startswith("/usr"):
            break
    else:
        add_profile = True

    terminal = call_terminal_cmd(
        console_cmd + (["--profile=geany_start"] if add_profile else []), workdir
    )
    win = Gtk.Window()
    win.add(terminal)
    return win, terminal


class PythonTestingWindow:
    @staticmethod
    def get_scrolled_window():
        window = Gtk.ScrolledWindow()
        window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        window.set_shadow_type(Gtk.ShadowType.NONE)
        return window

    @staticmethod
    def get_common_vbox(is_sidebar=False):
        run_lbl = "Run all tests" if is_sidebar else "Re-run"
        box = Gtk.VBox(False, 0)
        toolbar = Gtk.Toolbar()
        toolbar.set_icon_size(Gtk.IconSize.MENU)
        toolbar.set_style(Gtk.ToolbarStyle.ICONS)
        image = Gtk.Image.new_from_icon_name(
            "view-refresh" if is_sidebar else "edit-clear", Gtk.IconSize.SMALL_TOOLBAR
        )
        item = Gtk.ToolButton.new(image)
        item.set_tooltip_text("Reload" if is_sidebar else "Clear")
        toolbar.add(item)
        image = Gtk.Image.new_from_icon_name("system-run", Gtk.IconSize.SMALL_TOOLBAR)
        item = Gtk.ToolButton.new(image)
        item.set_tooltip_text(run_lbl)
        toolbar.add(item)
        if is_sidebar:
            entry = Gtk.SearchEntry()
            toolitem = Gtk.ToolItem()
            toolitem.add(entry)
            toolitem.set_tooltip_text("Search Tests")
            entry.show()
            toolbar.add(toolitem)
        box.pack_start(toolbar, False, False, 0)
        toolbar.show_all()
        return box

    def setup_sidebar_treeview(self, cmd, base_path):
        return
        self.proj_path = base_path
        tree_view = Gtk.TreeView()
        tree_store = get_tree_store_with_collections(discover_tests(cmd, base_path))
        tree_view.set_model(tree_store)
        icon_renderer = Gtk.CellRendererPixbuf()
        text_renderer = Gtk.CellRendererText()
        column_text = Gtk.TreeViewColumn("Tests")
        column_text.set_visible(True)
        column_text.pack_start(icon_renderer, False)
        column_text.pack_start(text_renderer, True)
        column_text.add_attribute(icon_renderer, "pixbuf", 0)
        column_text.add_attribute(text_renderer, "text", 1)
        tree_view.append_column(column_text)
        renderer = Gtk.CellRendererPixbuf()
        renderer.set_alignment(0.9, 0.9)
        column = Gtk.TreeViewColumn("Action", renderer)
        column.set_alignment(1.0)
        column.add_attribute(renderer, "pixbuf", 2)
        tree_view.append_column(column)
        self.sbox.add(tree_view)
        tree_view.show_all()
        self.sbox.show_all()

    def set_sidebar_window(self):
        self.sidebar_page = 0
        self.sidebar_window = self.get_scrolled_window()
        self.sbox = self.get_common_vbox(is_sidebar=True)
        self.sidebar_window.add(self.sbox)

    def set_testing_console(self):
        self.testout_page = 0
        self.testing_output_window = self.get_scrolled_window()
        box = self.get_common_vbox()
        self.output_vte = Vte.Terminal()
        self.output_vte.set_audible_bell(False)
        self.output_vte.set_scroll_on_output(True)
        self.output_vte.set_cursor_blink_mode(Vte.CursorBlinkMode.OFF)
        box.add(self.output_vte)
        self.testing_output_window.add(box)
        box.show_all()

    def __init__(self):
        self.set_sidebar_window()
        self.set_testing_console()

    def show_all_window(self):
        self.sidebar_window.show_all()
        self.testing_output_window.show_all()

    def hide_all_window(self):
        self.sidebar_window.hide()
        self.testing_output_window.hide()


def make_path(paths, test_lists, total_filename=None, splitter="/"):
    if total_filename is None:
        total_filename = paths
    path_split = paths.split(splitter, 1)
    paths = {}
    if len(path_split) > 1:
        paths[path_split[0]] = {
            "filename": total_filename.replace(path_split[1], ""),
            "tests": make_path(path_split[1], test_lists, total_filename),
        }
    else:
        paths[path_split[0]] = {"filename": total_filename, "tests": test_lists}
    return paths


def dict_merge(dct, merge_dct):
    for k, v in merge_dct.items():
        if k in dct:
            if isinstance(dct[k], dict) and isinstance(v, collections.Mapping):
                dict_merge(dct[k], merge_dct[k])
            elif isinstance(v, list) and isinstance(dct[k], list):
                dct[k].extend(v)
            else:
                dct[k] = v
        elif isinstance(v, collections.Mapping):
            dct[k] = v
        else:
            dct[k] = v if isinstance(v, list) else [v]


def discover_tests(test_cmd, base_path, check_file=None):
    collect = {}
    output = []
    os.chdir(base_path)
    if isinstance(test_cmd, list):

        def reader(fd):
            c = os.read(fd, 1024)
            while c:
                output.append(c.decode("utf8"))
                c = os.read(fd, 1024)

        pty.spawn(test_cmd, master_read=reader)
        for opt in output:
            opt = opt.replace("\r", " ").replace("\n", " ").strip()
            if not opt or "no tests ran" in opt:
                continue
            opt = opt.split()
            for tests in opt:
                tests = tests.strip()
                if check_file:
                    if check_file in tests:
                        return True
                    continue
                file_split = tests.split("::", maxsplit=1)
                try:
                    filename, others = file_split
                except ValueError:
                    filename = file_split[0]
                others = ".".join(others.split("::"))
                paths = make_path(filename, others)
                dict_merge(collect, paths)
    else:

        def handle_tests(tests, other=None):
            if tests:
                output.extend(tests)

        test_cmd.suiteClass = handle_tests
        test_cmd.discover(base_path)
        for tests in output:
            if not tests:
                continue
            test_name, test_info = str(tests).split()
            test_top = tests.__class__.__name__
            test_file = test_info[1:-1].replace(test_top, "").replace(".", "/")[:-1] + ".py"
            if check_file:
                if test_file in check_file:
                    return True
                continue
            paths = make_path(test_file, [test_top + "." + test_name])
            dict_merge(collect, paths)
    return collect if check_file is None else False
