# PyCoding Helper for [Geany](https://geany.org) which needs [Peasy](https://github.com/kugel-/peasy)


#### Comes with:

 * Python Files Linting based on [this helper](https://wiki.geany.org/howtos/check_python_code). Runs automatically on open/activation of python files if configured.
 * Configurable python code formatting option. Can use [black](https://black.readthedocs.io/en/stable/) or `autopep8` or `yapf` whichever is installed. Keybinding and Auto present.
 * Complete Python Code Based on [jedi](https://jedi.readthedocs.io/en/latest/).
 * genrate docstring on `class` or `def` definition from menu. 

 #### A python project initializer highly opinionated.
 * Either `pyenv`/ `virtualenv`/ `virtualenvwrapper` needs to be installed.
 * Creates a virtualenvironment for python projects during project creation looking inside `$HOME/.virtualenvs/`.
 * create a folder in project name inside project base path.
      
 
#### Requirements:
 * All in `python3`. 
 * `jedi` needs to be installed for completion.
 * Favorite linter. I use `flake8`. Please modify the linter for your need.
 * Favorite formatter. I use `black` with line 99.
 * `pydocstring` if you want to use docstring feature.

:warning: MAY BE BUGGY
