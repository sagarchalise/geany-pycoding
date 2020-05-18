# PyCoding Helper for [Geany](https://geany.org) which needs [Peasy](https://github.com/kugel-/peasy)


#### Comes with:

 * Python Files Linting based on [this helper](https://wiki.geany.org/howtos/check_python_code). Runs automatically on open/activation of python files if configured.
 * Configurable python code formatting option. Can use [black](https://black.readthedocs.io/en/stable/) or `autopep8` or `yapf` whichever is installed. Keybinding and Auto present.
 * Complete Python Code Based on [jedi](https://jedi.readthedocs.io/en/latest/).
 * Opinionated python project initializer for geany based on `virtualenv` and `virtualenvwrapper`
 * when on specific line that has `class` or `def`, document can be generated from editor menu. Formatters are supported as that by `pydocstring` and can be chosen from configuration.

#### Requirements:

 * `black` and `jedi` needs to be installed as well as your favorite linter. I use `flake8`. All in `python3`
 * `pydocstring` if you want to use docstring feature.

#### WARNING: MAY BE BUGGY
