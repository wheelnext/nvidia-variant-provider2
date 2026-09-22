from __future__ import annotations

import copy
import ctypes
import operator
import os
import re
import sys
import threading
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import ClassVar

from packaging.version import InvalidVersion, Version

from .definition import load_definition, validate_definition

MAX_ITEMS = 100_000
MAX_STEPS = 1_000_000


class ProviderError(Exception):
    def __init__(self, category, message, code=None):
        super().__init__(message)
        self.category, self.message, self.code = category, message, code

    def wire(self):
        result = {"category": self.category, "message": self.message}
        if self.code is not None:
            result["code"] = self.code
        return result


@dataclass(frozen=True)
class VariantFeatureConfig:
    name: str
    values: list[str]
    multi_value: bool = False


def _string(value):
    if isinstance(value, dict) and "$version" in value:
        return value["$version"]
    if value is None:
        return "None"
    return str(value)


def _comparable(value):
    if isinstance(value, dict) and "$version" in value:
        return Version(value["$version"])
    if isinstance(value, list):
        return [_comparable(x) for x in value]
    return value


class ReplayTransport:
    """No native I/O; exact ordered call transcript with symbolic handles."""

    def __init__(self, calls):
        self.calls = copy.deepcopy(calls)
        self.index = 0
        self.trace = []

    def call(self, name, args):
        request = {"function": name, "args": args}
        self.trace.append(request)
        if self.index >= len(self.calls):
            raise ProviderError("ReplayError", f"Unexpected call: {name}")
        expected = self.calls[self.index]
        self.index += 1
        if request != {k: expected[k] for k in ("function", "args")}:
            raise ProviderError("ReplayError", f"Call mismatch at {self.index - 1}")
        if "error" in expected:
            raise ProviderError(**expected["error"])
        return copy.deepcopy(expected["result"])

    def assert_consumed(self):
        if self.index != len(self.calls):
            raise ProviderError("ReplayError", "Unconsumed native calls")


class NativeTransport:
    """Trusted FFI declarations only. Native calls are not a sandbox."""

    _types: ClassVar[dict[str, type]] = {
        "i32": ctypes.c_int32,
        "u32": ctypes.c_uint32,
        "i64": ctypes.c_int64,
        "u64": ctypes.c_uint64,
        "handle": ctypes.c_void_p,
    }
    _lock = threading.RLock()

    def __init__(
        self, definition, *, platform=None, environment=None, library_overrides=None
    ):
        self._definition = copy.deepcopy(definition)
        self.platform = platform or (
            "windows" if sys.platform.startswith("win") else "default"
        )
        self.environment = os.environ if environment is None else environment
        # Explicit host injection for ABI tests; never obtained from the definition.
        self.overrides = library_overrides or {}
        self.libraries = {}
        self.handles = {}
        self.next_handle = 0
        self.trace = []

    def _library(self, name):
        if name in self.libraries:
            return self.libraries[name]
        entry = self._definition["libraries"][name]
        candidates = entry["platforms"].get(
            self.platform, entry["platforms"].get("default", [])
        )
        paths = []
        if name in self.overrides:
            paths = [self.overrides[name]]
        else:
            for item in candidates:
                if "path" in item:
                    path = item["path"]
                else:
                    base = self.environment.get(item["env"], item["default"])
                    path = str(
                        (
                            PureWindowsPath
                            if self.platform == "windows"
                            else PurePosixPath
                        )(base)
                        / item["suffix"]
                    )
                if (
                    "://" in path
                    or "\x00" in path
                    or ".." in path.replace("\\", "/").split("/")
                ):
                    raise ProviderError("PolicyError", "Invalid library path")
                if ("/" in path or "\\" in path) and not (
                    PureWindowsPath(path).is_absolute()
                    or PurePosixPath(path).is_absolute()
                ):
                    raise ProviderError(
                        "PolicyError", "Relative library paths are forbidden"
                    )
                paths.append(path)
        for path in paths:
            try:
                lib = ctypes.CDLL(str(path))
                self.libraries[name] = lib
                return lib
            except OSError:
                pass
        raise ProviderError("NativeError", "Library not found", 12)

    def call(self, name, args):
        with self._lock:
            return self._call(name, args)

    def _call(self, name, args):
        self.trace.append({"function": name, "args": copy.deepcopy(args)})
        spec = self._definition["functions"][name]
        lib = self._library(spec["library"])
        try:
            function = getattr(lib, spec["symbol"])
        except AttributeError as exc:
            raise ProviderError("NativeError", "Function not found", 13) from exc
        native_args, argtypes, outputs = [], [], []
        inputs = iter(args)
        for arg in spec["args"]:
            kind = arg["type"]
            if arg["direction"] == "out":
                if kind == "buffer":
                    value = ctypes.create_string_buffer(arg["size"])
                    argtypes.append(ctypes.POINTER(ctypes.c_char))
                    native_args.append(value)
                else:
                    value = self._types[kind]()
                    argtypes.append(ctypes.POINTER(self._types[kind]))
                    native_args.append(ctypes.byref(value))
                outputs.append((arg, value))
            else:
                value = next(inputs)
                if kind == "handle":
                    token = value.get("$handle") if isinstance(value, dict) else None
                    if (
                        token not in self.handles
                        or self.handles[token][0] != spec["library"]
                    ):
                        raise ProviderError("TypeError", "Invalid native handle")
                    value = self.handles[token][1]
                elif type(value) is not int:
                    raise ProviderError("TypeError", "Native integer required")
                bits = ctypes.sizeof(self._types[kind]) * 8
                if kind != "handle" and not (
                    -(2 ** (bits - 1)) if kind.startswith("i") else 0
                ) <= value < (2 ** (bits - 1) if kind.startswith("i") else 2**bits):
                    raise ProviderError("OverflowError", "Native integer out of range")
                argtypes.append(self._types[kind])
                native_args.append(self._types[kind](value))
        function.argtypes = argtypes
        function.restype = (
            None if spec["returns"] == "void" else self._types[spec["returns"]]
        )
        result = function(*native_args)
        if spec["returns"] != "void" and result not in spec["success"]:
            raise ProviderError("NativeError", "Native call failed", result)
        output = {}
        for arg, value in outputs:
            if arg["type"] == "buffer":
                raw = bytes(value)
                if b"\x00" not in raw:
                    raise ProviderError("NativeError", "Unterminated native buffer")
                output[arg["name"]] = raw.split(b"\x00", 1)[0].decode("utf-8")
            elif arg["type"] == "handle":
                if not value.value:
                    raise ProviderError("NativeError", "Null native handle")
                self.next_handle += 1
                token = str(self.next_handle)
                self.handles[token] = (spec["library"], value.value)
                output[arg["name"]] = {"$handle": token}
            else:
                output[arg["name"]] = value.value
        if spec.get("invalidates_handles", False):
            self.handles = {
                k: v for k, v in self.handles.items() if v[0] != spec["library"]
            }
        return output


class Provider:
    def __init__(self, definition=None, *, transport=None, environment=None):
        # In-memory definitions are an explicitly trusted host API, still validated.
        self._definition = (
            validate_definition(copy.deepcopy(definition))
            if definition is not None
            else load_definition()[0]
        )
        self.environment = os.environ if environment is None else environment
        self.transport = (
            transport
            if transport is not None
            else NativeTransport(self._definition, environment=self.environment)
        )
        self.cache = {}
        self.diagnostics = []
        self.steps = 0
        self.cleanup_steps = 0
        self.in_cleanup = False
        self._lock = threading.RLock()
        self.namespace = self._definition["provider"]["namespace"]
        self.is_build_plugin = self._definition["provider"]["is_build_plugin"]

    @property
    def definition(self):
        return copy.deepcopy(self._definition)

    @classmethod
    def from_file(cls, path, **options):
        definition, _ = load_definition(path, **options)
        return cls(definition)

    def clear_cache(self, query=None):
        with self._lock:
            if query is None:
                self.cache.clear()
            else:
                self.cache.pop(query, None)

    def invoke(self, method):
        with self._lock, NativeTransport._lock:
            self.diagnostics = []
            self.steps = 0
            self.cleanup_steps = 0
            self.in_cleanup = False
            try:
                if method not in self._definition["exports"]:
                    raise ProviderError("MethodError", f"Unknown method: {method}")
                value = self._query(self._definition["exports"][method])
                return {"value": value, "warnings": copy.deepcopy(self.diagnostics)}
            except ProviderError as exc:
                return {
                    "error": exc.wire(),
                    "warnings": copy.deepcopy(self.diagnostics),
                }

    def evaluate(self, method):
        result = self.invoke(method)
        if "error" in result:
            raise ProviderError(**result["error"])
        return result["value"]

    def _query(self, name):
        if name in self.cache:
            return copy.deepcopy(self.cache[name])
        query = self._definition["queries"][name]
        result = self._eval(query["expr"], {})
        if query["cache"]:
            self.cache[name] = copy.deepcopy(result)
        return result

    def _eval(self, expr, scope):
        if self.in_cleanup:
            self.cleanup_steps += 1
        else:
            self.steps += 1
        if (self.in_cleanup and self.cleanup_steps > 10_000) or (
            not self.in_cleanup and self.steps > MAX_STEPS
        ):
            raise ProviderError("ResourceError", "Evaluation step limit exceeded")
        try:
            result = self._operation(expr, scope)
            if type(result) is int and not -(2**63) <= result < 2**63:
                raise ProviderError("OverflowError", "Integer overflow")
            if isinstance(result, (list, str, dict)) and len(result) > MAX_ITEMS:
                raise ProviderError("ResourceError", "Collection limit exceeded")
            return result
        except ProviderError:
            raise
        except InvalidVersion as exc:
            raise ProviderError("InvalidVersion", str(exc)) from exc
        except (
            ValueError,
            TypeError,
            IndexError,
            KeyError,
            ZeroDivisionError,
            OverflowError,
            UnicodeError,
        ) as exc:
            raise ProviderError(type(exc).__name__, str(exc)) from exc

    def _operation(self, e, s):
        op = e["op"]

        def ev(x):
            return self._eval(x, s)

        if op == "literal":
            return copy.deepcopy(e["value"])
        if op == "ref":
            return s[e["name"]]
        if op == "const":
            return self._definition["constants"][e["name"]]
        if op == "query":
            return self._query(e["name"])
        if op == "env":
            return self.environment.get(e["name"], e["default"])
        if op == "let":
            local = dict(s)
            for b in e["bindings"]:
                local[b["name"]] = self._eval(b["value"], local)
            return self._eval(e["body"], local)
        if op == "if":
            return ev(e["then"] if ev(e["condition"]) else e["else"])
        if op == "list":
            return [ev(x) for x in e["items"]]
        if op == "record":
            return {k: ev(e["fields"][k]) for k in sorted(e["fields"])}
        if op == "get":
            return ev(e["value"])[e["key"]]
        if op == "index":
            return ev(e["value"])[ev(e["index"])]
        if op == "seq":
            value = None
            for x in e["items"]:
                value = ev(x)
            return value
        if op == "binary":
            left, right = ev(e["left"]), ev(e["right"])
            functions = {
                "add": operator.add,
                "sub": operator.sub,
                "mul": operator.mul,
                "floor_div": operator.floordiv,
                "mod": operator.mod,
                "eq": operator.eq,
                "ne": operator.ne,
                "lt": operator.lt,
                "le": operator.le,
                "gt": operator.gt,
                "ge": operator.ge,
            }
            return functions[e["kind"]](_comparable(left), _comparable(right))
        if op == "not":
            return not ev(e["value"])
        if op == "is_null":
            return ev(e["value"]) is None
        if op == "range":
            start, stop, step = ev(e["start"]), ev(e["stop"]), ev(e["step"])
            values = range(start, stop, step)
            if len(values) > MAX_ITEMS:
                raise ProviderError("ResourceError", "Collection limit exceeded")
            return list(values)
        if op in ("map", "filter"):
            items = ev(e["items"])
            if not isinstance(items, list):
                raise ProviderError("TypeError", "List required")
            values = []
            for item in items:
                value = self._eval(e["body"], {**s, e["var"]: item})
                if op == "map":
                    values.append(value)
                elif value:
                    values.append(item)
            return values
        if op == "flatten":
            values = ev(e["value"])
            if sum(map(len, values)) > MAX_ITEMS:
                raise ProviderError("ResourceError", "Collection limit exceeded")
            return [x for items in values for x in items]
        if op == "concat":
            result = []
            for x in e["items"]:
                value = ev(x)
                if len(result) + len(value) > MAX_ITEMS:
                    raise ProviderError("ResourceError", "Collection limit exceeded")
                result.extend(value)
            return result
        if op == "sort":
            return sorted(ev(e["value"]), key=_comparable, reverse=e["reverse"])
        if op == "unique":
            result = []
            for item in ev(e["value"]):
                if item not in result:
                    result.append(item)
            return result
        if op == "split":
            return ev(e["value"]).split(e["separator"], e["maxsplit"])
        if op == "parse_int":
            return int(ev(e["value"]))
        if op == "parse_version":
            return {"$version": str(Version(ev(e["value"])))}
        if op == "version_part":
            v = Version(ev(e["value"])["$version"])
            return {"major": v.major, "minor": v.minor, "micro": v.micro}[e["part"]]
        if op == "format":
            values = {k: _string(ev(e["args"][k])) for k in sorted(e["args"])}

            def substitute(match):
                if match.group(1) not in values:
                    raise ProviderError("DefinitionError", "Unknown format placeholder")
                return values[match.group(1)]

            return re.sub(r"\{([a-zA-Z_][a-zA-Z_0-9]*)\}", substitute, e["template"])
        if op == "call":
            return self.transport.call(e["function"], [ev(x) for x in e["args"]])
        if op == "try":
            try:
                return ev(e["body"])
            except ProviderError as exc:
                if exc.category not in e["catch"]:
                    raise
                return ev(e["fallback"])
        if op == "finally":
            try:
                return ev(e["body"])
            finally:
                previous = self.in_cleanup
                self.in_cleanup = True
                try:
                    ev(e["cleanup"])
                finally:
                    self.in_cleanup = previous
        if op in ("warn", "raise"):
            spec = self._definition["diagnostics"][e["diagnostic"]]
            message = ev(
                {"op": "format", "template": spec["message"], "args": e["args"]}
            )
            if op == "raise":
                raise ProviderError(spec["category"], message)
            self.diagnostics.append({"category": spec["category"], "message": message})
            return None
        if op == "unpack":
            values = ev(e["value"])
            count = e["count"]
            if len(values) < count:
                raise ValueError(
                    f"not enough values to unpack (expected {count}, got {len(values)})"
                )
            if len(values) > count:
                raise ValueError(f"too many values to unpack (expected {count})")
            return values
        if op == "configs":
            result = []
            for feature in self._definition["features"]:
                values = self._query(feature[e["mode"]])
                if values or e["mode"] == "all":
                    result.append(
                        {
                            "name": feature["name"],
                            "values": values,
                            "multi_value": feature["multi_value"],
                        }
                    )
            return result
        raise ProviderError("DefinitionError", f"Unsupported operation: {op}")
