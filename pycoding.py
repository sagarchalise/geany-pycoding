#! /usr/bin/env python3
import sys
import logging

import blackd
from aiohttp import web
from pathlib import Path

try:
    import jedi
except ImportError:
    HAS_JEDI = False
else:
    jedi.settings.case_insensitive_completion = False
    HAS_JEDI = True
USER_HOME = Path.home()
PROJ_PATH_HEADER = "X-Project-Path"
FILE_PATH_HEADER = "X-File-Path"
DOC_TEXT_HEADER = "X-Doc-Text"
PORT = 45484


def make_app():
    app = blackd.make_app()
    app.add_routes([web.post("/jedi/", handle_jedi)])
    return app


async def handle_jedi(request):
    sys_path = sys.path

    def get_path_for_completion(proj_name=None):
        if proj_name:
            append_project_venv(proj_name)
        faked_gir_path = USER_HOME.joinpath(".cache/fakegir")
        if faked_gir_path.is_dir():
            path = [str(faked_gir_path)] + sys_path
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
        venv_pth = USER_HOME.joinpath(".virtualenvs")
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
        append_sys_path(proj_name)

    def jedi_complete(buffer, fp=None, text=None, sys_path=None, stop_len=25):
        script = jedi.Script(buffer, path=fp, sys_path=sys_path)
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

    try:
        if request.headers.get(blackd.VERSION_HEADER, "1") != "1":
            return web.Response(status=501, text="This server only supports protocol version 1")
        if not HAS_JEDI:
            return web.Response(
                status=400, text="jedi not found, python auto-completion not possible."
            )
        cur_doc = request.headers.get(FILE_PATH_HEADER)
        path = get_path_for_completion(Path(request.headers.get(PROJ_PATH_HEADER) or "").name)
        doc_text = request.headers.get(DOC_TEXT_HEADER)
        try:
            stop_len = int(request.headers.get(blackd.LINE_LENGTH_HEADER))
        except (TypeError, ValueError):
            stop_len = 25
        req_bytes = await request.content.read()
        charset = request.charset if request.charset is not None else "utf8"
        req_str = req_bytes.decode(charset)
        completion_resp = jedi_complete(
            req_str, fp=cur_doc, sys_path=path, text=doc_text, stop_len=stop_len
        )
        if completion_resp:
            return web.Response(
                content_type=request.content_type, charset="utf8", text=completion_resp or ""
            )
        return web.Response(status=204)
    except Exception as error:
        logging.error("Exception during handling a request")
        logging.exception(error)
        return web.Response(status=500, text=str(error))


def main():
    logging.basicConfig(level=logging.INFO)
    app = make_app()
    web.run_app(app, host="localhost", port=PORT, handle_signals=True)


if __name__ == "__main__":
    main()
