import importlib


def get_module(modname):
    try:
        return importlib.import_module(modname)
    except ImportError:
        return False


DEFAULT_FORMATTER = "black"
FORMATTER_TYPES = {DEFAULT_FORMATTER, "autopep8", "yapf"}


def get_formatter(name=DEFAULT_FORMATTER, with_name=False):
    ret = [None, None, None]
    if with_name:
        ret.append(name)
    formatter_module = get_module(name)
    if not formatter_module:
        print("No Formatter: {0}".format(name))
        return ret
    print("Formatter: {0}".format(name))
    if name == "black":

        def format_code(content, style_config=None):
            try:
                mode = formatter_module.FileMode(**style_config)
                changed_content = formatter_module.format_file_contents(
                    content, fast=True, mode=mode
                )
            except formatter_module.NothingChanged:
                return "", False
            else:
                return changed_content, True

        ret[0] = format_code
        ret[2] = formatter_module.InvalidInput
    elif name == "yapf":
        format_code = formatter_module.yapflib.yapf_api.FormatCode
        style_dir = formatter_module.yapflib.file_resources.GetDefaultStyleForDir
        from lib2to3.pgen2 import parse

        ret[0] = format_code
        ret[1] = style_dir
        ret[2] = parse.ParseError
    elif name == "autopep8":

        def format_code(content, style=None):
            fixed_code = formatter_module.fix_code(content, options=style)
            return fixed_code, True

        ret[0] = format_code
    return ret


def run_formatter_on_content(
    formatter_signature, code_contents, style_paths=None, line_width=None
):
    if isinstance(formatter_signature, str):
        code_formatter, default_style_dir, exceptions = get_formatter(formatter_signature)
        formatter_name = formatter_signature
    else:
        code_formatter, default_style_dir, exceptions, formatter_name = formatter_signature
    if code_formatter is None:
        return None, "No formatter available: {0}.".format(formatter_name)
    default_style_dir_exists = default_style_dir is not None
    if default_style_dir_exists:
        for path in style_paths:
            style = default_style_dir(path)
            if style:
                break
        else:  # nobreak
            style = {}
    else:
        style = {}
    if line_width:
        style[
            "COLUMN_LIMIT"
            if default_style_dir_exists
            else "line_length"
            if formatter_name == "black"
            else "max_line_length"
        ] = line_width
    if exceptions:
        try:
            format_text, formatted = code_formatter(code_contents, style_config=style)
        except exceptions as error:
            formatted = None
            format_text = str(error)
    else:
        format_text, formatted = code_formatter(code_contents, style_config=style)
    return formatted, format_text
