"""unittest event recorder. Executed by path with Python -I -S."""
import hashlib
import importlib.machinery
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest


class SourceError(ImportError):
    pass


class SnapshotPackages:
    """Only declared packages: resolve from their physical snapshot directories."""
    def __init__(self, roots, packages):
        self.locations = {}
        self.errors = []
        for name in packages:
            if name in sys.modules:
                self.reject(name, "already loaded before source selection")
            location = next((root / name for root in roots if (root / name / "__init__.py").is_file()), None)
            if location is None:
                self.reject(name, "regular package missing from snapshot")
            self.locations[name] = location.resolve()

    def reject(self, name, reason):
        message = f"{name}: {reason}"
        self.errors.append(message)
        raise SourceError(message)

    def find_spec(self, fullname, path=None, target=None):
        top, *parts = fullname.split(".")
        if top not in self.locations:
            return None
        parent = self.locations[top].joinpath(*parts[:-1]) if parts else self.locations[top].parent
        spec = importlib.machinery.PathFinder.find_spec(fullname, [str(parent)])
        if spec is None or not isinstance(spec.loader, importlib.machinery.SourceFileLoader):
            self.reject(fullname, "Python source missing from snapshot")
        self.check(fullname, spec.origin, spec.submodule_search_locations)
        return spec

    def check(self, name, origin, paths=None):
        top = name.split(".")[0]
        location = self.locations[top]
        if not origin or not Path(origin).resolve().is_relative_to(location):
            self.reject(name, "module origin is outside snapshot package")
        if paths is not None and any(not Path(p).resolve().is_relative_to(location) for p in paths):
            self.reject(name, "package search path is outside snapshot package")


def environment_identity(environment):
    directories = environment["dependency_dirs"]
    distributions = []
    for index, directory in enumerate(directories):
        for dist in importlib.metadata.distributions(path=[directory]):
            metadata = dist.read_text("METADATA") or dist.read_text("PKG-INFO") or ""
            distributions.append({"directory": index, "name": dist.metadata["Name"], "version": dist.version,
                                  "metadata_sha256": hashlib.sha256(metadata.encode()).hexdigest()})
    return {"python": {"implementation": sys.implementation.name, "version": sys.version.split()[0],
                       "executable_sha256": hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()},
            "dependency_directory_count": len(directories),
            "distributions": sorted(distributions, key=lambda item: (item["directory"], item["name"] or ""))}


def record(root, test_file, test_id, source_roots, environment=None):
    root = Path(root)
    roots = [root / test_file.parent, *[root / p for p in source_roots], root]
    sys.path[:0] = [str(p) for p in roots]
    scope = None
    data = {"protocol": 1, "test_id": test_id, "collected": 0, "tests_run": 0,
            "events": [], "completed": False, "imports": []}
    phase = "import"

    class Result(unittest.TestResult):
        def event(self, status, error=None):
            item = {"status": status, "phase": phase}
            if error:
                item["exception"] = error[0].__name__
                item["message"] = str(error[1])[:2000]
            data["events"].append(item)

        def addSuccess(self, test):
            super().addSuccess(test)
            self.event("pass")

        def addFailure(self, test, err):
            super().addFailure(test, err)
            self.event("assertion_failure" if phase == "test" and issubclass(err[0], AssertionError) else phase + "_error", err)

        def addError(self, test, err):
            super().addError(test, err)
            where = phase
            # unittest runs module/class fixtures outside the TestCase hooks.
            if str(test).startswith(("setUpClass (", "setUpModule (")):
                where = "setup"
            elif str(test).startswith(("tearDownClass (", "tearDownModule (")):
                where = "teardown"
            self.event("source_error" if issubclass(err[0], SourceError) else
                       "worker_exit" if issubclass(err[0], (SystemExit, KeyboardInterrupt)) else where + "_error", err)

        def addSkip(self, test, reason):
            super().addSkip(test, reason)
            self.event("skip")

        def addExpectedFailure(self, test, err):
            super().addExpectedFailure(test, err)
            self.event("expected_failure", err)

        def addUnexpectedSuccess(self, test):
            super().addUnexpectedSuccess(test)
            self.event("unexpected_success")

        def addSubTest(self, test, subtest, err):
            super().addSubTest(test, subtest, err)
            if err:
                if issubclass(err[0], (SystemExit, KeyboardInterrupt)):
                    status = "worker_exit"
                else:
                    status = "assertion_failure" if phase == "test" and issubclass(err[0], AssertionError) else phase + "_error"
                self.event(status, err)

    def instrument(suite):
        for test in suite:
            if isinstance(test, unittest.TestSuite):
                instrument(test)
                continue
            for method, stage in (("_callSetUp", "setup"), ("_callTestMethod", "test"),
                                  ("_callTearDown", "teardown"), ("_callCleanup", "cleanup")):
                original = getattr(test, method)

                def wrapped(self, *args, original=original, stage=stage, **kwargs):
                    nonlocal phase
                    phase = stage
                    return original(*args, **kwargs)

                setattr(test, method, types.MethodType(wrapped, test))

    try:
        if environment:
            phase = "environment"
            if sys.version_info < (3, 11):
                raise RuntimeError("Python 3.11+ is required")
            data["environment"] = environment_identity(environment)
            scope = SnapshotPackages(roots, environment["packages"])
            sys.meta_path.insert(0, scope)
            sys.path.extend(environment["dependency_dirs"])
            phase = "import"
        spec = importlib.util.spec_from_file_location("witness_test", root / test_file)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        phase = "collection"
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromName(test_id, module)
        data["collected"] = suite.countTestCases()
        if loader.errors:
            data["events"].append({"status": "collection_error", "phase": phase})
        elif data["collected"] != 1:
            data["events"].append({"status": "zero_tests" if not data["collected"] else "multiple_tests", "phase": phase})
        else:
            instrument(suite)
            phase = "setup"
            result = Result()
            suite.run(result)
            data["tests_run"] = result.testsRun
        data["completed"] = True
    except BaseException as exc:
        status = "source_error" if isinstance(exc, SourceError) else "worker_exit" if isinstance(exc, (SystemExit, KeyboardInterrupt)) else phase + "_error"
        data["events"].append({"status": status, "phase": phase, "exception": type(exc).__name__, "message": str(exc)[:2000]})
        data["completed"] = True
    finally:
        if scope:
            for name, module in sys.modules.copy().items():
                if name.split(".")[0] in scope.locations:
                    try:
                        scope.check(name, getattr(module, "__file__", None), getattr(module, "__path__", None))
                        scope.check(name, getattr(getattr(module, "__spec__", None), "origin", None))
                    except SourceError:
                        pass
            for message in dict.fromkeys(scope.errors):
                data["events"].append({"status": "source_error", "phase": "source", "message": message})
            sys.meta_path.remove(scope)
        for name, module in sorted(sys.modules.copy().items()):
            origin = getattr(module, "__file__", None)
            if origin:
                try:
                    relative = Path(origin).resolve().relative_to(root.resolve())
                except ValueError:
                    continue
                item = {"module": name, "path": str(relative)}
                if scope and name.split(".")[0] in scope.locations:
                    item["sha256"] = hashlib.sha256(Path(origin).read_bytes()).hexdigest()
                data["imports"].append(item)
    return data


if __name__ == "__main__":
    root, filename, test_id, output, options = sys.argv[1:]
    options = json.loads(options)
    result = record(root, Path(filename), test_id, **options)
    Path(output).write_text(json.dumps(result), encoding="utf-8")
