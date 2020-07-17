# PyCoding Helper for [Geany](https://geany.org) which needs [Peasy](https://github.com/kugel-/peasy)


#### Comes with:

 * Linting
    - Either lint based on [this helper](https://wiki.geany.org/howtos/check_python_code) or from program which annotates in editor itself.
    - Run automatically on open/activation of python files if configured. Build linting will be disabled on annotation.
 * Code Formatting:
    - Can use [black](https://black.readthedocs.io/en/stable/) or `autopep8` or `yapf`. Can be chosen from settings.
    - Configurable through keybinding as well as can be run automatically during saves. Is available in tools menu as well.
* Complete Python Code Based on [jedi](https://jedi.readthedocs.io/en/latest/). And additional jedi [features](https://jedi.readthedocs.io/en/latest/docs/features.html) through keybinding and editor menu.
* Show docstring when autocomplete is completed on compiler window.
* Show `calltip` of signatures if available when hovering over with mouse or autocomplete is done.
* Jump to source module if availabe through keybinding or editor menu.
* Genrate docstring on `class` or `def` definition from menu or keybinding.

#### Project based approach.

* Either `pyenv`/ `virtualenv`/ `virtualenvwrapper` needs to be installed.
* Creates a virtualenvironment for python projects during project creation looking inside `$HOME/.virtualenvs/` or `pyenv` setup.
* Create a folder in project name inside project base path.
* Configurable through project properties.
* Will use project base path and venv paths for code complete.
* Have test runner and test window with `vte`.
* Will automatically create some build commands based on choices of `formatter`, `linter` and `test`.


#### Requirements:
* All in `python3`.
* `jedi` needs to be installed for completion, refactoring, docstrings and goto feature.
* Favorite linter of choice. `flake8` or `pylint` or others.
* Favorite formatter of choice amongst `black`, `yapf` and `auopep8`.
