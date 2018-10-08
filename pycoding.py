#!/usr/bin/env python3

# Copyright (C) 2015 Red Hat, Inc.
#   This copyrighted material is made available to anyone wishing to use,
#  modify, copy, or redistribute it subject to the terms and conditions of
#  the GNU General Public License v.2.
#
#   This application is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#   General Public License for more details.
#
# Authors:
#   Guy Streeter <streeter@redhat.com>

# /glib/gio/tests/gdbus-example-server.c

import os
import glob
import sys
from gi.repository import Gio, GObject, GLib

import black
try:
    import jedi
except ImportError:
    HAS_JEDI = False
else:
    jedi.settings.case_insensitive_completion = False
    HAS_JEDI = True

try:
    import pipenv
except ImportError:
    print("No pipenv")
    pipenv = None

USER_HOME = os.path.expanduser("~")

OBJECT_PATH = "/org/gtk/GDBus/GeanyPyCodingObject"
BUS_NAME = "org.gtk.GDBus.GeanyPyCodingServer"
INTERFACE_NAME = "org.gtk.GDBus.GeanyPyCodingInterface"


def dump_args(func):
    """
    Using for debugging...
    """

    def wrapper(*func_args, **func_kwargs):
        arg_names = func.__code__.co_varnames[: func.__code__.co_argcount]
        args = func_args[: len(arg_names)]
        defaults = func.__defaults__ or ()
        args = args + defaults[len(defaults) - (func.__code__.co_argcount - len(args)) :]
        params = list(zip(arg_names, args))
        args = func_args[len(arg_names) :]
        if args:
            params.append(("args", args))
        if func_kwargs:
            params.append(("kwargs", func_kwargs))
        print(func.__name__ + " (" + ", ".join("%s = %r" % p for p in params) + " )")
        return func(*func_args, **func_kwargs)

    return wrapper


introspection_xml = """
<node>
  <interface name='{}'>
    <method name='Complete'>
      <arg type='s' name='content' direction='in'/>
      <arg type='s' name='file_path' direction='in'/>
      <arg type='s' name='project_path' direction='in'/>
      <arg type='s' name='doc_text' direction='in'/>
      <arg type='s' name='completions' direction='out'/>
    </method>
    <method name='Format'>
      <arg type='s' name='content' direction='in'/>
      <arg type='i' name='line_length' direction='in'/>
      <arg type='s' name='formatted' direction='out'/>
    </method>
  </interface>
</node>
""".format(
    INTERFACE_NAME
)

introspection_data = Gio.DBusNodeInfo.new_for_xml(introspection_xml)
print(repr(introspection_data.interfaces))
for iface in introspection_data.interfaces:
    print(repr(iface))
interface_info = introspection_data.lookup_interface(INTERFACE_NAME)
print("if", interface_info)

# dbus main loop is handled by Gio
main_loop = GObject.MainLoop()


def static_vars(**kwargs):
    def decorate(func):
        for k, v in kwargs.items():
            setattr(func, k, v)
        return func

    return decorate

@static_vars(swap_it=0)
def on_timeout_cb(connection):
    return True


sys_path = sys.path


def get_path_for_completion(proj_name=None):
    if proj_name:
        append_project_venv(proj_name)
    faked_gir_path = os.path.join(USER_HOME, ".cache/fakegir")
    if os.path.isdir(faked_gir_path):
        path = [faked_gir_path] + sys_path
    else:
        print("Support for GIR may be missing")
        path = sys_path
    return path


def append_sys_path(path):
    if path and path not in sys_path:
        sys_path.append(path)


def append_project_venv(proj_name):
    if not proj_name:
        return
    venv_pth = os.path.join(USER_HOME, ".virtualenvs")
    if not os.path.isdir(venv_pth):
        return
    for pth in os.listdir(venv_pth):
        entry = os.path.join(venv_pth, pth)
        if pth.startswith(proj_name) and os.path.isdir(entry):
            st_pk = glob.glob(os.path.join(entry, "lib/pytho*/site-packages"))
            st_pk = st_pk.pop() if st_pk else None
            if not (st_pk and os.path.isdir(st_pk)):
                return
            proj_name = st_pk
            break
    else:  # nobreak
        return
    sys_path.append(proj_name)


def jedi_complete(buffer, fp=None, path=None, text=None):
    script = jedi.Script(buffer, line=None, column=None, path=fp, sys_path=path)
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
                return doc or ''
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
        if count == 24:
            break
    return data

@dump_args
def handle_method_call(
    connection, sender, object_path, interface_name, method_name, parameters, invocation
):
    if method_name == "Complete":
        parm_unpacked = parameters.unpack()
        print(repr(parameters), parm_unpacked)
        buffer = parm_unpacked[0]
        file_path = parm_unpacked[1]
        project_path = parm_unpacked[2]
        doc_text = parm_unpacked[3] or None
        path = get_path_for_completion(os.path.basename(project_path or ""))
        try:
            completions = jedi_complete(buffer, fp=file_path, path=path, text=doc_text)
        except Exception as error:
            invocation.return_error_literal(
                Gio.io_error_quark(), Gio.IOErrorEnum.FAILED_HANDLED, str(error)
            )
        else:
            invocation.return_value(GLib.Variant("(s)", (completions,)))
    if method_name == "Format":
        parm_unpacked = parameters.unpack()
        print(repr(parameters), parm_unpacked)
        content = parm_unpacked[0]
        line_length = parm_unpacked[1]
        try:
            formatted = black.format_file_contents(
                content, line_length=line_length, fast=True 
            )
        except Exception as error:
            print(error)
            invocation.return_error_literal(
                Gio.io_error_quark(), Gio.IOErrorEnum.FAILED_HANDLED, str(error)
            )
        else:
            invocation.return_value(GLib.Variant("(s)", (formatted,)))
    else:
        print("Not handled")
        invocation.return_error_literal(
            Gio.io_error_quark(), Gio.IOErrorEnum.FAILED_HANDLED, "Error"
            )


@dump_args
def bus_acquired_handler(connection, name):
    # From https://lazka.github.io/pgi-docs/Gio-2.0/structs/DBusInterfaceVTable.html
    #  Since 2.38, if you want to handle getting/setting D-Bus properties
    # asynchronously, give None as your get_property() or set_property()
    # function. The D-Bus call will be directed to your method_call
    # function, with the provided interface_name set to
    # “org.freedesktop.DBus.Properties”.
    #
    # Specifying the handlers here works, but they don't have a way to
    # return an error.
    registration_id = connection.register_object(
        OBJECT_PATH, interface_info, handle_method_call, None, None
    )
    assert registration_id > 0
    # swap the properties of Foo and Bar every two seconds
    GLib.timeout_add_seconds(2, on_timeout_cb, connection)


def name_acquired_handler(connection, name):
    print("name acquired", name)


def name_lost_handler(connection, name):
    print("name lost", name)
    main_loop.quit()


owner_id = Gio.bus_own_name(
    Gio.BusType.SESSION,
    BUS_NAME,
    Gio.BusNameOwnerFlags.NONE,
    bus_acquired_handler,
    name_acquired_handler,
    name_lost_handler,
)
main_loop.run()
Gio.bus_unown_name(owner_id)


