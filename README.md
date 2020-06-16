# PyCoding Helper for [Geany](https://geany.org) which needs [Peasy](https://github.com/kugel-/peasy)


#### Comes with:

 * Python Files Linting based on [this helper](https://wiki.geany.org/howtos/check_python_code). Runs automatically on open/activation of python files if configured.
 * Code Formatting:
    - Can use [black](https://black.readthedocs.io/en/stable/)(**prioritized**) or `autopep8` or `yapf` whichever is installed.
    - Configurable through keybinding as well as can be run automatically during saves. Is available in context menu as well.
* Complete Python Code Based on [jedi](https://jedi.readthedocs.io/en/latest/).
* Genrate docstring on `class` or `def` definition from menu if `pydocstring` installed. **OPTIONAL** 

#### A python project initializer highly opinionated.
* Either `pyenv`/ `virtualenv`/ `virtualenvwrapper` needs to be installed.
* Creates a virtualenvironment for python projects during project creation looking inside `$HOME/.virtualenvs/` or `pyenv` setup.
* Create a folder in project name inside project base path.
      
 
#### Requirements:
* All in `python3`.
* `jedi` needs to be installed for completion.
* Favorite linter of choice. `flake8` or `pylint` or others.
* Favorite formatter of choice amongst `black`, `yapf` and `auopep8`.
* `pydocstring` if docstring feature is required.
