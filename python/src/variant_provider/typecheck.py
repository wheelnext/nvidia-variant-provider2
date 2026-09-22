"""Conservative static types: reject impossible operations before any probing.

Nullable branches retain union types. Data-dependent failures remain runtime
errors so definitions can intentionally preserve their provider's behavior.
"""

import re


class Type:
    def __init__(self, kinds=(), fields=None, item=None):
        self.kinds = set(kinds)
        self.fields = fields
        self.item = item


def merge(types):
    types = list(types)
    kinds = set().union(*(t.kinds for t in types))
    records = [t for t in types if t.fields is not None]
    fields = None
    if records:
        keys = set().union(*(t.fields for t in records))
        fields = {k: merge(t.fields[k] for t in records if k in t.fields) for k in keys}
    items = [t.item for t in types if t.item is not None]
    return Type(kinds, fields, merge(items) if items else None)


def validate_types(definition, error_type):
    def fail(message):
        raise error_type(message)

    def expect(t, *kinds):
        if t.kinds and not t.kinds.intersection(kinds):
            fail(f"Type mismatch: expected {kinds}, got {sorted(t.kinds)}")

    def literal(v):
        if v is None:
            return Type(["null"])
        if type(v) is bool:
            return Type(["bool"])
        if type(v) is int:
            return Type(["int"])
        if isinstance(v, str):
            return Type(["str"])
        if isinstance(v, list):
            return Type(["list"], item=merge(literal(x) for x in v))
        if isinstance(v, dict):
            return Type(["record"], fields={k: literal(x) for k, x in v.items()})
        fail("Unsupported literal type")

    def placeholders(template, args):
        if not re.fullmatch(r"(?:[^{}]|\{[A-Za-z_][A-Za-z_0-9]*\})*", template):
            fail("Invalid format template")
        if set(re.findall(r"\{([A-Za-z_][A-Za-z_0-9]*)\}", template)) - set(args):
            fail("Missing format argument")

    memo = {}

    def query(name):
        if name not in memo:
            memo[name] = infer(definition["queries"][name]["expr"], {})
        return memo[name]

    def infer(e, s):
        op = e["op"]

        def ev(v):
            return infer(v, s)

        if op == "literal":
            return literal(e["value"])
        if op == "const":
            return literal(definition["constants"][e["name"]])
        if op == "ref":
            return s[e["name"]]
        if op == "query":
            return query(e["name"])
        if op == "env":
            return Type(["str", "null"])
        if op == "let":
            local = dict(s)
            for b in e["bindings"]:
                local[b["name"]] = infer(b["value"], local)
            return infer(e["body"], local)
        if op == "if":
            ev(e["condition"])
            return merge([ev(e["then"]), ev(e["else"])])
        if op in ("list", "concat"):
            items = [ev(x) for x in e["items"]]
            if op == "concat":
                for t in items:
                    expect(t, "list")
                items = [t.item for t in items if t.item is not None]
            return Type(["list"], item=merge(items))
        if op == "record":
            if any(k.startswith("$") for k in e["fields"]):
                fail("Reserved record field")
            return Type(["record"], fields={k: ev(v) for k, v in e["fields"].items()})
        if op == "get":
            t = ev(e["value"])
            expect(t, "record")
            if t.fields is not None and e["key"] not in t.fields:
                fail("Unknown record field")
            return t.fields[e["key"]] if t.fields else Type()
        if op == "index":
            t = ev(e["value"])
            expect(t, "list")
            expect(ev(e["index"]), "int")
            return t.item or Type()
        if op == "seq":
            types = [ev(x) for x in e["items"]]
            return types[-1] if types else Type(["null"])
        if op == "binary":
            a, b = ev(e["left"]), ev(e["right"])
            kind = e["kind"]
            if kind in ("eq", "ne", "lt", "le", "gt", "ge"):
                return Type(["bool"])
            kinds = ("int", "str", "list") if kind == "add" else ("int",)
            expect(a, *kinds)
            expect(b, *kinds)
            if a.kinds and b.kinds and not a.kinds & b.kinds:
                fail("Incompatible arithmetic types")
            return merge([a, b])
        if op in ("not", "is_null"):
            ev(e["value"])
            return Type(["bool"])
        if op == "range":
            for k in ("start", "stop", "step"):
                expect(ev(e[k]), "int")
            return Type(["list"], item=Type(["int"]))
        if op in ("map", "filter"):
            t = ev(e["items"])
            expect(t, "list")
            body = infer(e["body"], {**s, e["var"]: t.item or Type()})
            return Type(["list"], item=body) if op == "map" else t
        if op in ("sort", "unique", "unpack"):
            t = ev(e["value"])
            expect(t, "list")
            return t
        if op == "flatten":
            t = ev(e["value"])
            expect(t, "list")
            item = t.item or Type()
            expect(item, "list")
            return item if item.kinds else Type(["list"])
        if op == "split":
            expect(ev(e["value"]), "str")
            return Type(["list"], item=Type(["str"]))
        if op == "parse_int":
            expect(ev(e["value"]), "str", "int")
            return Type(["int"])
        if op == "parse_version":
            expect(ev(e["value"]), "str")
            return Type(["version"])
        if op == "version_part":
            expect(ev(e["value"]), "version")
            return Type(["int"])
        if op == "format":
            placeholders(e["template"], e["args"])
            for v in e["args"].values():
                expect(ev(v), "str", "int", "bool", "null", "version")
            return Type(["str"])
        if op == "call":
            fn = definition["functions"][e["function"]]
            inputs = iter(e["args"])
            fields = {}
            for arg in fn["args"]:
                kind = (
                    "handle:" + fn["library"]
                    if arg["type"] == "handle"
                    else "str"
                    if arg["type"] == "buffer"
                    else "int"
                )
                if arg["direction"] == "in":
                    expect(ev(next(inputs)), kind)
                else:
                    fields[arg["name"]] = Type([kind])
            return Type(["record"], fields=fields)
        if op == "try":
            return merge([ev(e["body"]), ev(e["fallback"])])
        if op == "finally":
            t = ev(e["body"])
            ev(e["cleanup"])
            return t
        if op in ("warn", "raise"):
            placeholders(
                definition["diagnostics"][e["diagnostic"]]["message"], e["args"]
            )
            for v in e["args"].values():
                expect(ev(v), "str", "int", "bool", "null", "version")
            return Type(["null"]) if op == "warn" else Type()
        if op == "configs":
            for f in definition["features"]:
                t = query(f[e["mode"]])
                expect(t, "list")
                expect(t.item or Type(), "str")
            return Type(["list"], item=Type(["record"]))
        fail("Unknown operation")

    for name in definition["queries"]:
        query(name)

    def handles(t):
        return (
            any(k.startswith("handle:") for k in t.kinds)
            or any(handles(x) for x in (t.fields or {}).values())
            or (t.item is not None and handles(t.item))
        )

    for name in definition["exports"].values():
        if handles(query(name)):
            fail("Native handles cannot be exported")
