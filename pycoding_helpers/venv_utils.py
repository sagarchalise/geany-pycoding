import os
import re
import sys
import subprocess
import configparser
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from gi.repository import Geany

exec_cmds = {Geany.BuildGroup.FT: 3, Geany.BuildGroup.EXEC: 2}
magic_method_re = re.compile(r"^__(\w)+(_\w+)?__$")

py_cmd = "python"
NAME = "pycoding"
IS_PYPROJECT = "is_pyproj"

DEFAULT_PYTHON_NAME = "system"
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
    pyenv_versions.add(DEFAULT_PYTHON_NAME)
else:
    if VIRTUALENV_HOME.exists():
        pyenv_versions = {d.name for d in VIRTUALENV_HOME.iterdir() if d.is_dir()}
    has_pyenv = False


def get_possible_venv_path(project, config=None):
    env_venv = os.environ.get("VIRTUAL_ENV")
    if env_venv:
        return Path(env_venv)
    if not project:
        return
    if isinstance(project, str):
        workdir_path = project
        proj_name = None
    else:
        workdir_path = project.base_path
        proj_name = project.name
    if config is None:
        try:
            config = configparser.ConfigParser()
            config.read(project.file_name)
        except AttributeError:
            pass
    try:
        venv_name = config.get(NAME, PYTHON_PTH_LBL, fallback=None)
    except AttributeError:
        venv_name = None
    possible_venv_locs = {
        "venv",
        "~/.pyenv/versions",
        "~/.virtualenvs",
    }
    workdir_path = Path(workdir_path).resolve()
    # Prioritize Venv Path
    venv_name = venv_name or proj_name or workdir_path.name
    venv_pth = VIRTUALENV_HOME.joinpath(venv_name)
    if venv_pth.exists():
        return venv_pth
    possible_venv_locs.discard(str(VIRTUALENV_HOME))
    venv_pth = None
    for v_name in possible_venv_locs:
        if v_name.startswith("~"):
            venv_pth = Path(v_name).expanduser().joinpath(venv_name)
        else:
            venv_pth = workdir_path.joinpath(v_name)
        if venv_pth.exists():
            break
    else:  # nobreak
        venv_pth = workdir_path.joinpath(venv_name)
        if not venv_pth.joinpath("bin", "python").exists():
            return
    return venv_pth


generic_cmd_paths = {"/usr/local/bin", "/usr/bin"}


def get_possible_cmd(cmd, project=None, config=None, venv_path=None, check_system=True):
    if venv_path is None:
        venv_path = get_possible_venv_path(project, config)
    cmd_name = Path(cmd).name if "/" in cmd else cmd
    if venv_path:
        venv_cmd = venv_path.joinpath("bin", cmd_name)
        if venv_cmd.exists():
            return str(venv_cmd)
    if not check_system:
        return
    for pth in generic_cmd_paths:
        if cmd.startswith(pth):
            break
        pth = Path(pth).joinpath(cmd_name)
        if pth.exists():
            cmd = str(pth)
            break
    return cmd


PYTHON_PTH_LBL = "python_path"


def create_venv(proj_path, proj_name, python_pth):
    venvwrapper = None
    already_venv = VIRTUALENV_HOME.joinpath(python_pth)
    if (
        not python_pth.lower().startswith(("3.", "pypy3"))
        and already_venv.joinpath("bin/python").exists()
    ):
        project_venv = already_venv
        status = "Venv for project already exits. {0} {1}".format(already_venv.name, proj_name)
    else:
        status = "{0} in python venv creation for project: {1}".format("{0}", proj_name)
        if has_pyenv:
            project_venv = VIRTUALENV_HOME.joinpath(proj_name)
            if proj_name not in pyenv_versions:
                if from_command:
                    args = ["pyenv", "virtualenv", python_pth, proj_name]
                else:
                    args = "{0} -m venv {1}".format(
                        py_cmd
                        if python_pth == DEFAULT_PYTHON_NAME
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
                status = status.format("NO VIRTUALENV: Fallback to venv mode.")
                import venv

                venv.create(proj_path)
            else:
                if not VIRTUALENV_HOME.exists():
                    VIRTUALENV_HOME.mkdir()
                project_venv = VIRTUALENV_HOME.joinpath(proj_name)
                sys.argv = [
                    "virtualenv",
                    "--python={0}".format(
                        "/usr/bin/python3" if python_pth == DEFAULT_PYTHON_NAME else python_pth
                    ),
                    str(project_venv),
                ]
                virtualenv.main()
                try:
                    import virtualenvwrapper
                except ImportError:
                    virtualenvwrapper = None
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
    Geany.msgwin_switch_tab(Geany.MessageWindowTabNum.STATUS, False)


executor = ThreadPoolExecutor(max_workers=4)


def create_venv_for_project(proj_name, proj_path, python_cnf=None):
    if not (python_cnf and python_cnf.get(IS_PYPROJECT)):
        return
    proj_path = Path(proj_path)
    if python_cnf.get("mkvenv"):
        future = executor.submit(create_venv, proj_path, proj_name, python_cnf.get(PYTHON_PTH_LBL))
        future.add_done_callback(on_project_done)
    if python_cnf.get("create_template"):
        future = executor.submit(create_proj_template, proj_path.joinpath(proj_name))
        future.add_done_callback(on_project_done)


def set_build_command(commands, file_name):
    cur_doc = Geany.document_get_current()
    if not cur_doc:
        return
    if not cur_doc.real_path.endswith(file_name):
        return
    bs = Geany.BuildSource.PROJ
    wd = "%p"
    main_cmd, linter_cmd, formatter_cmd, testing_cmd = commands
    for grp, rng in exec_cmds.items():
        for i in range(rng):
            lbl = Geany.build_get_current_menu_item(grp, i, Geany.BuildCmdEntries.LABEL)
            lbl_l = (lbl or "").lower()
            cmd = None
            if grp == Geany.BuildGroup.FT:
                if "lint" in lbl_l or (i == 1 and not lbl):
                    cmd = linter_cmd + file_name
                    lbl = "_Lint"
                elif "format" in lbl_l or (i == 2 and not lbl):
                    cmd = formatter_cmd + file_name
                    lbl = "_Format"
                elif i == 0:
                    cmd = main_cmd + " -m py_compile " + file_name
            else:
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
