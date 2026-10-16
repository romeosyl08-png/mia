"""Build Bara's optional C accelerator for the current CPython interpreter."""
import argparse
import os
from pathlib import Path
import shlex
import subprocess
import sys
import sysconfig
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'native' / 'retrieval' / 'cosine.c'
TARGET = ROOT / 'bara' / ('_retrieval_native' + (sysconfig.get_config_var('EXT_SUFFIX') or '.so'))


def build():
    if sys.implementation.name != 'cpython' or os.name != 'posix':
        raise RuntimeError('The native build supports CPython on Linux/macOS; use the Python fallback elsewhere.')
    linker = shlex.split(sysconfig.get_config_var('LDSHARED') or 'cc -shared')
    if os.environ.get('CC'):
        linker = shlex.split(os.environ['CC']) + linker[1:]
    include = sysconfig.get_path('include')
    if not include or not (Path(include) / 'Python.h').is_file():
        raise RuntimeError('Python development headers are missing; use the Python fallback or install them.')
    target = TARGET
    with tempfile.TemporaryDirectory(prefix='.retrieval-build-', dir=target.parent) as directory:
        output = Path(directory) / target.name
        command = linker + ['-std=c11', '-O3', '-fPIC', '-fno-fast-math', '-ffp-contract=off',
                            '-Wall', '-Wextra', '-Werror', '-I' + include, str(SOURCE), '-o', str(output), '-lm']
        subprocess.run(command, check=True)
        # Validate the compiled module in a separate process before replacing it.
        subprocess.run([sys.executable, '-c',
                        'import importlib.util, sys; '
                        's = importlib.util.spec_from_file_location("_retrieval_native", sys.argv[1]); '
                        'm = importlib.util.module_from_spec(s); s.loader.exec_module(m); '
                        'assert m.cosine([1.0, 0.0], [1.0, 0.0]) == 1.0', str(output)], check=True)
        output.replace(target)
    print(f'Built {target.relative_to(ROOT)} for Python {sys.version.split()[0]}.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--optional', action='store_true', help='Keep the Python fallback if compilation is unavailable')
    args = parser.parse_args()
    try:
        build()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        if not args.optional:
            raise SystemExit(str(error)) from error
        # A reused build directory must not load an older binary after failure.
        TARGET.unlink(missing_ok=True)
        print(f'Native retrieval unavailable; Python fallback remains active: {error}', file=sys.stderr)


if __name__ == '__main__':
    main()
