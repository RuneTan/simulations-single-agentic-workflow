"""Inspect this interpreter and package metadata without importing a tutorial."""
from importlib import metadata
from pathlib import Path
import platform
import site
import sys
import subprocess

from nsbi_agent.backend import software_version
from nsbi_agent.contracts import Check, EnvironmentRequest, ToolError, ToolResult

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def inspect_environment(request: EnvironmentRequest) -> ToolResult:
    checks = []

    def check(name: str, passed: bool, expected: str, actual: str) -> None:
        checks.append(Check(
            name=name, status="pass" if passed else "fail",
            expected=expected, actual=str(actual),
        ))

    prefix = Path(sys.prefix).resolve()
    base = Path(sys.base_prefix).resolve()
    actual_python = platform.python_version()
    try:
        expected_python = (PROJECT_ROOT / ".python-version").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as error:
        expected_python = ""
        check("python_version_lock", False, "readable .python-version", str(error))
    check("python_version", bool(expected_python) and actual_python == expected_python,
          expected_python or "nonempty .python-version", actual_python)
    check("virtual_environment", prefix == PROJECT_ROOT / ".venv",
          str(PROJECT_ROOT / ".venv"), str(prefix))
    check("owned_base_python", base.is_relative_to(PROJECT_ROOT / ".tools/python"),
          "base interpreter inside .tools/python", str(base))
    check("user_site_disabled", site.ENABLE_USER_SITE is False,
          "False", str(site.ENABLE_USER_SITE))

    search_paths = [Path(path).resolve() for path in sys.path]
    outside = [str(path) for path in search_paths
               if not path.is_relative_to(PROJECT_ROOT)]
    check("import_paths", not outside, "all import paths inside the project",
          str(outside))

    packages = {}
    for name in sorted(set(request.packages) | set(request.package_names)):
        expected_version = request.packages.get(name)
        try:
            distribution = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            packages[name] = {"version": None, "location": None}
            check("package:" + name, False, expected_version or "installed", "not installed")
            continue
        except (OSError, ValueError, TypeError, AttributeError, RuntimeError) as error:
            packages[name] = {'version': None, 'location': None, 'error': str(error),
                              'error_code': 'PACKAGE_METADATA_UNREADABLE'}
            check('package:' + name, False, 'readable package metadata', error)
            continue
        try:
            version = distribution.metadata.get('Version')
            location = Path(distribution.locate_file("")).resolve()
        except (OSError, ValueError, TypeError, AttributeError, RuntimeError) as error:
            packages[name] = {'version': None, 'location': None, 'error': str(error),
                              'error_code': 'PACKAGE_METADATA_UNREADABLE'}
            check('package:' + name, False, 'readable package metadata and path', error)
            continue
        readable = isinstance(version, str) and bool(version.strip())
        packages[name] = {"version": version, "location": str(location)}
        if not readable:
            packages[name].update(error='Missing or empty Version metadata', error_code='PACKAGE_VERSION_UNREADABLE')
        check("package_version:" + name, readable and (expected_version is None or version == expected_version),
              expected_version or "nonempty installed version", version if readable else 'missing/empty Version metadata')
        check("package_location:" + name, location.is_relative_to(prefix),
              "inside this virtual environment", str(location))

    backends = {}
    for name in sorted(set(request.backends) | set(request.backend_names)):
        expected = request.backends.get(name)
        try:
            actual = software_version(name)
            backends[name] = actual
            check('backend_version:' + name, expected is None or actual['version'] == expected,
                  expected or 'inventory only: readable version', actual['version'])
        except (OSError, ValueError, KeyError, AttributeError, subprocess.SubprocessError) as error:
            backends[name] = {'version': None, 'error': str(error),
                              'error_code': getattr(error, 'code', 'BACKEND_CHECK_FAILED')}
            check('backend_version:' + name, False, expected or 'readable version', str(error))
    failures = [item.name for item in checks if item.status == "fail"]
    return ToolResult(
        tool="inspect_environment",
        status="failed" if failures else "succeeded",
        data={
            "summary": {"passed": len(checks) - len(failures), "differences": len(failures)},
            "tool_number": 1,
            "project_root": str(PROJECT_ROOT),
            "python_version": actual_python,
            "expected_python_version": expected_python,
            "python_executable": sys.executable,
            "python_prefix": str(prefix),
            "python_base_prefix": str(base),
            "import_paths": [str(path) for path in search_paths],
            "packages": packages,
            "backends": backends,
            "scope": "Current Python environment, package metadata and backend version/source anchors.",
        },
        checks=checks,
        errors=[ToolError(code="ENVIRONMENT_CHECK_FAILED", message=name)
                for name in failures],
    )
