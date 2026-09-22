# Provider definition format 1.0.0

The authoritative machine-readable specification is
[`provider-1.0.0.schema.json`](../json/schemas/provider-1.0.0.schema.json). Its
`$id` is `urn:variant-provider:schema:definition:1.0.0`. It uses JSON Schema
Draft 2020-12. A definition's `$schema` must equal that identifier; it is never
a URL the engine fetches. Unknown versions, fields, and operators are rejected.

`format_version` is exactly `"1.0.0"`. `data_version` is a positive integer,
initially `1`, independent of the format version. Increment it for every
released content change. No migration, alternate format version, or
forward-compatible unknown-operator interpretation is provided. The fixture
format also has one schema, `case-1.0.0.schema.json`.

## Top-level fields

| Field         | Meaning                                                    |
| ------------- | ---------------------------------------------------------- |
| `provider`    | `id`, property `namespace`, and `is_build_plugin`          |
| `constants`   | Named JSON data used by expressions                        |
| `libraries`   | Local library candidates grouped by platform               |
| `functions`   | Typed C calls and success status codes                     |
| `queries`     | Named `{cache, expr}` declarations                         |
| `features`    | Ordered `{name, multi_value, all, supported}` declarations |
| `exports`     | Public method name to query name                           |
| `diagnostics` | Named `{category, message}` templates                      |

Namespace and feature names use PEP 825's lowercase ASCII identifier syntax.
This schema defines provider instructions, not wheel `variant.json` metadata.
[PEP 825](https://peps.python.org/pep-0825/) leaves the provider acquisition
interface to a subsequent specification.

## Expressions

Every expression is a JSON object with an `op` discriminator. Expressions are
structured data; strings are never parsed as programs. All fields listed below
are required unless the schema explicitly makes them optional.

| Operation               | Fields and semantics                                                   |
| ----------------------- | ---------------------------------------------------------------------- |
| `literal`               | `value`: JSON data; reserved `$` object keys prohibited                |
| `ref`, `const`, `query` | `name`: local binding, constant, or named query                        |
| `env`                   | `name`, `default`: string/null; missing differs from empty string      |
| `let`                   | Ordered `bindings: [{name,value}]`, then `body`; no duplicate bindings |
| `if`                    | `condition`, `then`, `else`; only the selected branch evaluates        |
| `list`, `seq`, `concat` | `items`; construct list, return last evaluation, concatenate lists     |
| `record`                | `fields`: named expressions; fields cannot start with `$`              |
| `get`                   | `value`, `key`: access a record field                                  |
| `index`                 | `value`, `index`: list index, including negative indices               |
| `binary`                | `kind`, `left`, `right`; arithmetic or comparison                      |
| `not`, `is_null`        | `value`; boolean negation or explicit null test                        |
| `range`                 | `start`, exclusive `stop`, nonzero `step`                              |
| `map`, `filter`         | `items`, local `var`, `body`; preserve iteration order                 |
| `flatten`               | `value`; flatten one list level                                        |
| `sort`                  | `value`, `reverse`; stable scalar or lexicographic list sorting        |
| `unique`                | `value`; preserve first occurrence                                     |
| `split`                 | `value`, nonempty `separator`, `maxsplit`; negative means unlimited    |
| `parse_int`             | `value`; signed integer conversion                                     |
| `parse_version`         | `value`; PEP 440 version value                                         |
| `version_part`          | `value`, `part` (`major`, `minor`, `micro`)                            |
| `format`                | `template`, `args`; `{identifier}` substitution only                   |
| `call`                  | `function`, ordered input `args`; returns output-argument record       |
| `try`                   | `body`, exact category list `catch`, lazy `fallback`                   |
| `finally`               | `body`, `cleanup`; cleanup error takes precedence                      |
| `warn`, `raise`         | `diagnostic`, interpolation `args`                                     |
| `unpack`                | `value`, `count`; enforce Python-style tuple-unpacking length          |
| `configs`               | `mode` (`all` or `supported`); evaluate ordered feature queries        |

Binary kinds: `add`, `sub`, `mul`, `floor_div`, `mod`, `eq`, `ne`, `lt`, `le`,
`gt`, `ge`. Addition also supports strings and lists. Integer division rounds
down; modulo follows the divisor's sign. Version comparison is version-object
ordering, not version-specifier matching. Null is distinct from every non-null
value. Empty collections/strings, zero, false, and null are false in conditions.

`configs` keeps feature declaration order and each query's list order. Supported
features with empty lists are omitted; all-config enumeration includes them.
Record fields and format arguments evaluate in lexicographic key order in both
engines. Use `seq`/`let` arrays when specifying an explicit call order.
Substituted format values are literal text and are never substituted a second
time.

## Native calls

Each function specifies `library`, `symbol`, `abi: "C"`, `args`, `returns`, and
`success`. Arguments have `name`, `type`, and `direction` (`in` or `out`). Types
are `i32`, `u32`, `i64`, `u64`, `handle`, and output-only `buffer`. Buffers
require `size` and decode NUL-terminated UTF-8. Return types are integer status
types or `void`. Non-success status codes become `NativeError` with the numeric
code.

Handles come only from native outputs, remain associated with their library, and
cannot be dereferenced by JSON. A function may set `invalidates_handles: true`
for session teardown. Native declarations do not support callbacks, structures,
varargs, executable byte buffers, or raw address literals.

Library `platforms.windows` overrides `platforms.default` on Windows. Each
candidate is either `{path}` or `{env, default, suffix}`. Environment
substitution is explicit. Only absolute paths or bare system library names are
accepted; URLs, NULs, traversal, and paths relative to the current directory are
rejected. Candidates are tried in declaration order. Host-only library overrides
support native tests; they are not JSON instructions.

## Portable bounds

Strict parsing rejects duplicate keys, floating-point/non-finite JSON numbers,
invalid UTF-8, integers outside signed 64-bit range, documents over 4 MiB, and
nesting beyond 64. Runtime arithmetic is checked signed 64-bit. FFI declarations
perform additional per-type bounds checks. Collection/string results are bounded
to 100,000 elements/characters; native buffers to 1 MiB. Each invocation permits
one million evaluated operations and a separate total allowance of 10,000
cleanup operations. These limits are host code, not values a definition can
increase.

Native calls may block or crash; these interpreter limits do not sandbox them.
Inputs outside these bounds deliberately produce engine errors rather than
attempting unbounded reference execution.
