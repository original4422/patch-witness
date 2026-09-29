"""unittest event recorder. Executed by path with Python -I -S."""
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest


def record(root, test_file, test_id, source_roots):
    root = Path(root)
    sys.path[:0] = [str(root / test_file.parent), *[str(root / p) for p in source_roots], str(root)]
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
            self.event("worker_exit" if issubclass(err[0], (SystemExit, KeyboardInterrupt)) else where + "_error", err)

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
        status = "worker_exit" if isinstance(exc, (SystemExit, KeyboardInterrupt)) else phase + "_error"
        data["events"].append({"status": status, "phase": phase, "exception": type(exc).__name__, "message": str(exc)[:2000]})
        data["completed"] = True
    finally:
        for name, module in sorted(sys.modules.copy().items()):
            origin = getattr(module, "__file__", None)
            if origin:
                try:
                    relative = Path(origin).resolve().relative_to(root.resolve())
                except ValueError:
                    continue
                data["imports"].append({"module": name, "path": str(relative)})
    return data


if __name__ == "__main__":
    root, filename, test_id, output, *source_roots = sys.argv[1:]
    result = record(root, Path(filename), test_id, source_roots)
    Path(output).write_text(json.dumps(result), encoding="utf-8")
