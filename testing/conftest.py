import functools
import os
import sys
import sysconfig
from contextlib import contextmanager

import pytest
from _pytest.monkeypatch import MonkeyPatch

_orig_trace = None


def pytest_configure():
    global _orig_trace
    _orig_trace = sys.gettrace()


@pytest.fixture(scope="session", autouse=True)
def term():
    """Configure TERM for predictable output from Pygments."""
    m = MonkeyPatch()
    m.setenv("TERM", "xterm-256color")
    yield m
    m.undo()


# if _orig_trace and not hasattr(sys, "pypy_version_info"):
# Fails with PyPy2 (https://travis-ci.org/antocuni/pdb/jobs/509624590)?!
@pytest.fixture(autouse=True)
def restore_settrace(monkeypatch):
    """(Re)store sys.gettrace after test run.

    This is required to re-enable coverage tracking.
    """
    assert sys.gettrace() is _orig_trace

    orig_settrace = sys.settrace

    # Wrap sys.settrace to restore original tracing function (coverage)
    # with `sys.settrace(None)`.
    def settrace(func):
        if func is None:
            orig_settrace(_orig_trace)
        else:
            orig_settrace(func)

    monkeypatch.setattr("sys.settrace", settrace)

    yield

    newtrace = sys.gettrace()
    if newtrace is not _orig_trace:
        sys.settrace(_orig_trace)
        assert newtrace is None, (
            f"tracing function was not reset! Breakpoints left? ({newtrace})"
        )


@pytest.fixture(autouse=True, scope="session")
def _tmphome(tmpdir_factory):
    """Set up HOME in a temporary directory.

    This ignores any real ~/.pdbrc.py then, and seems to be
    required also with linecache on py27, where it would read contents from
    ~/.pdbrc?!.
    """
    mp = MonkeyPatch()

    tmphome = tmpdir_factory.mktemp("tmphome")

    mp.setenv("HOME", str(tmphome))
    mp.setenv("USERPROFILE", str(tmphome))

    with tmphome.as_cwd():
        yield tmphome


@pytest.fixture
def tmpdirhome(tmpdir, monkeypatch):
    monkeypatch.setenv("HOME", str(tmpdir))
    monkeypatch.setenv("USERPROFILE", str(tmpdir))

    with tmpdir.as_cwd():
        yield tmpdir


@pytest.fixture(
    params=(
        ("pyrepl" if sys.version_info < (3, 13) else "_pyrepl"),
        "readline",
    ),
    scope="session",
)
def readline_param(request):
    m = MonkeyPatch()

    if "pyrepl" not in request.param:
        m.setattr("fancycompleter.DefaultConfig.prefer_pyrepl", False)
        return request.param

    old_stdin = sys.stdin

    class fake_stdin:
        """Missing fileno() to skip pyrepl.readline._setup.

        This is required to make tests not hang without capturing (`-s`)."""

    sys.stdin = fake_stdin()
    if sys.version_info >= (3, 13):
        import _pyrepl.readline

        sys.stdin = old_stdin
    else:
        try:
            import pyrepl.readline  # noqa: F401
        except ImportError as exc:
            pytest.skip(reason=f"pyrepl not available: {exc}")
        finally:
            sys.stdin = old_stdin
    m.setattr("fancycompleter.DefaultConfig.prefer_pyrepl", True)
    return request.param


@pytest.fixture
def monkeypatch_readline(monkeypatch, readline_param):
    """Patch readline to return given results."""

    def inner(line, begidx, endidx):
        if readline_param == "pyrepl":
            readline = "pyrepl.readline"
        elif readline_param == "_pyrepl":
            readline = "_pyrepl.readline"
        else:
            assert readline_param == "readline"
            readline = "readline"

        monkeypatch.setattr(f"{readline}.get_line_buffer", lambda: line)
        monkeypatch.setattr(f"{readline}.get_begidx", lambda: begidx)
        monkeypatch.setattr(f"{readline}.get_endidx", lambda: endidx)

    return inner


@pytest.fixture
def monkeypatch_pdb_methods(monkeypatch):
    def mock(method, *args, **kwargs):
        print(f"=== {method}({args}, {kwargs})")

    for mock_method in ("set_trace", "set_continue"):
        monkeypatch.setattr(
            f"pdbpp.pdb.Pdb.{mock_method}", functools.partial(mock, mock_method)
        )


@pytest.fixture
def patched_completions(monkeypatch):
    import fancycompleter

    from .test_pdb import PdbTest

    def inner(text, fancy_comps, pdb_comps):
        _pdb = PdbTest()
        _pdb.reset()
        _pdb.cmdloop = lambda: None
        _pdb.forget = lambda: None

        def _get_comps(complete, text):
            if isinstance(complete.__self__, fancycompleter.Completer):
                return fancy_comps[:]
            return pdb_comps[:]

        monkeypatch.setattr(_pdb, "_get_all_completions", _get_comps)
        _pdb.interaction(sys._getframe(), None)

        comps = []
        while True:
            val = _pdb.complete(text, len(comps))
            if val is None:
                break
            comps += [val]
        return comps

    return inner


@pytest.fixture(params=("color", "nocolor"))
def fancycompleter_color_param(request, monkeypatch):
    if request.param == "color":
        monkeypatch.setattr("fancycompleter.DefaultConfig.use_colors", True)
    else:
        monkeypatch.setattr("fancycompleter.DefaultConfig.use_colors", False)

    yield request.param


@pytest.fixture
def monkeypatch_importerror(monkeypatch):
    @contextmanager
    def cm(mocked_imports):
        orig_import = __import__

        def import_mock(name, *args):
            if name in mocked_imports:
                raise ImportError
            return orig_import(name, *args)

        with monkeypatch.context() as m:
            m.setattr("builtins.__import__", import_mock)
            yield m

    return cm


def skip_with_missing_pth_file():
    pth = os.path.join(sysconfig.get_path("purelib"), "pdbpp_hijack_pdb.pth")
    if not os.path.exists(pth):
        pytest.skip(f"Missing pth file ({pth}), editable install?")
