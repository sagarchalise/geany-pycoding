# PyCoding Helper for [Geany](https://geany.org)


#### Comes with:

 * Auto Lint Python Files based on [this helper](https://wiki.geany.org/howtos/check_python_code). Runs automatically on open/activation of python files.
 * Format python code on save with [black](https://black.readthedocs.io/en/stable/).
 * Complete Python Code Based on [jedi](https://jedi.readthedocs.io/en/latest/).
 
 
#### Requirements:
 
 * DBus Runner [bundled here](https://github.com/sagarchalise/geany-pycoding/blob/master/pycoding.py) needs to be run manually.
 * `black` and `jedi` needs to be installed as well as your favorite linter. I use `flake8`. All in `python3`
 
 
 
#### TODO

* Run DBus Runner Automatically.
* Give Option for formatter.

#### Caveats

* currently using `blackd` for formatting so uses `libsoup-2.4` for **HTTP** request.
* Install with `pip install black[d]`. Uses default port used by `blackd`

#### WARNING: VERY BUGGY 
