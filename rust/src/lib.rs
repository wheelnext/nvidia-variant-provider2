pub mod definition;
pub mod fixtures;
pub mod native;
mod typecheck;

use pep440_rs::Version;
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use std::{cmp::Ordering, collections::HashMap, str::FromStr, sync::Mutex};

pub const MAX_ITEMS: usize = 100_000;
const MAX_STEPS: usize = 1_000_000;
static NATIVE_SESSION: Mutex<()> = Mutex::new(());

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Error {
    pub category: String,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub code: Option<i64>,
}
impl Error {
    pub fn new(category: &str, message: &str) -> Self {
        Self {
            category: category.into(),
            message: message.into(),
            code: None,
        }
    }
}
impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {}", self.category, self.message)
    }
}
impl std::error::Error for Error {}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct VariantFeatureConfig {
    pub name: String,
    pub values: Vec<String>,
    pub multi_value: bool,
}
pub trait Transport {
    fn call(&mut self, name: &str, args: &[Value]) -> Result<Value, Error>;
    fn finish(&self) -> Result<(), Error> {
        Ok(())
    }
}
pub struct ReplayTransport {
    pub calls: Vec<Value>,
    pub index: usize,
    pub trace: Vec<Value>,
}
impl ReplayTransport {
    pub fn new(calls: Vec<Value>) -> Self {
        Self {
            calls,
            index: 0,
            trace: vec![],
        }
    }
}
impl Transport for ReplayTransport {
    fn call(&mut self, name: &str, args: &[Value]) -> Result<Value, Error> {
        let request = json!({"function":name,"args":args});
        self.trace.push(request.clone());
        let expected = self
            .calls
            .get(self.index)
            .ok_or_else(|| Error::new("ReplayError", &format!("Unexpected call: {name}")))?;
        self.index += 1;
        if request != json!({"function":expected["function"],"args":expected["args"]}) {
            return Err(Error::new(
                "ReplayError",
                &format!("Call mismatch at {}", self.index - 1),
            ));
        }
        if let Some(e) = expected.get("error") {
            return Err(serde_json::from_value(e.clone())
                .map_err(|_| Error::new("ReplayError", "Invalid recorded error"))?);
        }
        Ok(expected["result"].clone())
    }
    fn finish(&self) -> Result<(), Error> {
        if self.index != self.calls.len() {
            Err(Error::new("ReplayError", "Unconsumed native calls"))
        } else {
            Ok(())
        }
    }
}

pub struct Provider {
    definition: Value,
    pub environment: Option<HashMap<String, String>>,
    transport: Box<dyn Transport>,
    cache: HashMap<String, Value>,
    warnings: Vec<Value>,
    steps: usize,
    cleanup_steps: usize,
    in_cleanup: bool,
}
impl Provider {
    pub fn new(definition: Value, transport: Box<dyn Transport>) -> Result<Self, String> {
        definition::validate(&definition)?;
        Ok(Self {
            definition,
            environment: None,
            transport,
            cache: HashMap::new(),
            warnings: vec![],
            steps: 0,
            cleanup_steps: 0,
            in_cleanup: false,
        })
    }
    pub fn live(definition: Value) -> Result<Self, String> {
        let t = native::NativeTransport::new(definition.clone());
        Self::new(definition, Box::new(t))
    }
    pub fn bundled() -> Result<Self, String> {
        Self::live(definition::bundled()?)
    }
    pub fn definition(&self) -> &Value {
        &self.definition
    }
    pub fn clear_cache(&mut self) {
        self.cache.clear();
    }
    pub fn finish(&self) -> Result<(), Error> {
        self.transport.finish()
    }
    pub fn invoke(&mut self, method: &str) -> Value {
        let _session = NATIVE_SESSION.lock().unwrap_or_else(|e| e.into_inner());
        self.warnings.clear();
        self.steps = 0;
        self.cleanup_steps = 0;
        self.in_cleanup = false;
        let result = match self.definition["exports"]
            .get(method)
            .and_then(Value::as_str)
            .map(str::to_owned)
        {
            Some(q) => self.query(&q),
            None => Err(Error::new(
                "MethodError",
                &format!("Unknown method: {method}"),
            )),
        };
        match result {
            Ok(v) => json!({"value":v,"warnings":self.warnings}),
            Err(e) => json!({"error":e,"warnings":self.warnings}),
        }
    }
    pub fn evaluate(&mut self, method: &str) -> Result<Value, Error> {
        let r = self.invoke(method);
        if r.get("error").is_some() {
            Err(serde_json::from_value(r["error"].clone()).unwrap())
        } else {
            Ok(r["value"].clone())
        }
    }
    fn query(&mut self, name: &str) -> Result<Value, Error> {
        if let Some(v) = self.cache.get(name) {
            return Ok(v.clone());
        }
        let q = self.definition["queries"][name].clone();
        let value = self.eval(&q["expr"], &HashMap::new())?;
        if q["cache"] == true {
            self.cache.insert(name.into(), value.clone());
        }
        Ok(value)
    }
    fn eval(&mut self, e: &Value, s: &HashMap<String, Value>) -> Result<Value, Error> {
        if self.in_cleanup {
            self.cleanup_steps += 1;
        } else {
            self.steps += 1;
        }
        if (self.in_cleanup && self.cleanup_steps > 10_000)
            || (!self.in_cleanup && self.steps > MAX_STEPS)
        {
            return Err(Error::new(
                "ResourceError",
                "Evaluation step limit exceeded",
            ));
        }
        let v = self.operation(e, s)?;
        let size = match &v {
            Value::Array(a) => a.len(),
            Value::Object(o) => o.len(),
            Value::String(t) => t.chars().count(),
            _ => 0,
        };
        if size > MAX_ITEMS {
            return Err(Error::new("ResourceError", "Collection limit exceeded"));
        }
        Ok(v)
    }
    fn operation(&mut self, e: &Value, s: &HashMap<String, Value>) -> Result<Value, Error> {
        let name = |k: &str| e[k].as_str().unwrap_or("");
        match name("op") {
            "literal" => Ok(e["value"].clone()),
            "ref" => s
                .get(name("name"))
                .cloned()
                .ok_or_else(|| Error::new("DefinitionError", "Unknown reference")),
            "const" => Ok(self.definition["constants"][name("name")].clone()),
            "query" => self.query(name("name")),
            "env" => Ok(match &self.environment {
                Some(env) => env.get(name("name")).cloned(),
                None => std::env::var(name("name")).ok(),
            }
            .map(Value::from)
            .unwrap_or_else(|| e["default"].clone())),
            "let" => {
                let mut local = s.clone();
                for b in e["bindings"].as_array().unwrap() {
                    let v = self.eval(&b["value"], &local)?;
                    local.insert(b["name"].as_str().unwrap().into(), v);
                }
                self.eval(&e["body"], &local)
            }
            "if" => {
                let c = self.eval(&e["condition"], s)?;
                self.eval(&e[if truth(&c) { "then" } else { "else" }], s)
            }
            "list" => {
                let mut a = vec![];
                for x in e["items"].as_array().unwrap() {
                    a.push(self.eval(x, s)?);
                }
                Ok(a.into())
            }
            "record" => {
                let mut o = Map::new();
                for (k, x) in e["fields"].as_object().unwrap() {
                    o.insert(k.clone(), self.eval(x, s)?);
                }
                Ok(o.into())
            }
            "get" => {
                let v = self.eval(&e["value"], s)?;
                v.get(name("key"))
                    .cloned()
                    .ok_or_else(|| Error::new("KeyError", &repr(name("key"))))
            }
            "index" => {
                let v = self.eval(&e["value"], s)?;
                let i = int(&self.eval(&e["index"], s)?)?;
                let a = array(&v)?;
                let i = if i < 0 { a.len() as i64 + i } else { i };
                a.get(i as usize)
                    .cloned()
                    .ok_or_else(|| Error::new("IndexError", "list index out of range"))
            }
            "seq" => {
                let mut v = Value::Null;
                for x in e["items"].as_array().unwrap() {
                    v = self.eval(x, s)?;
                }
                Ok(v)
            }
            "binary" => {
                let a = self.eval(&e["left"], s)?;
                let b = self.eval(&e["right"], s)?;
                binary(name("kind"), a, b)
            }
            "not" => Ok(json!(!truth(&self.eval(&e["value"], s)?))),
            "is_null" => Ok(json!(self.eval(&e["value"], s)?.is_null())),
            "range" => {
                let a = int(&self.eval(&e["start"], s)?)?;
                let b = int(&self.eval(&e["stop"], s)?)?;
                let step = int(&self.eval(&e["step"], s)?)?;
                if step == 0 {
                    return Err(Error::new("ValueError", "range() arg 3 must not be zero"));
                }
                let mut out = vec![];
                let mut x = a;
                while if step > 0 { x < b } else { x > b } {
                    if out.len() >= MAX_ITEMS {
                        return Err(Error::new("ResourceError", "Collection limit exceeded"));
                    }
                    out.push(json!(x));
                    x = x
                        .checked_add(step)
                        .ok_or_else(|| Error::new("OverflowError", "Integer overflow"))?;
                }
                Ok(out.into())
            }
            "map" | "filter" => {
                let items = self.eval(&e["items"], s)?;
                let mut out = vec![];
                for x in array(&items)? {
                    let mut local = s.clone();
                    local.insert(name("var").into(), x.clone());
                    let v = self.eval(&e["body"], &local)?;
                    if name("op") == "map" {
                        out.push(v);
                    } else if truth(&v) {
                        out.push(x.clone());
                    }
                }
                Ok(out.into())
            }
            "flatten" => {
                let v = self.eval(&e["value"], s)?;
                let mut out = vec![];
                for a in array(&v)? {
                    extend(&mut out, array(a)?)?;
                }
                Ok(out.into())
            }
            "concat" => {
                let mut out = vec![];
                for x in e["items"].as_array().unwrap() {
                    let v = self.eval(x, s)?;
                    extend(&mut out, array(&v)?)?;
                }
                Ok(out.into())
            }
            "sort" => {
                let v = self.eval(&e["value"], s)?;
                let mut a = array(&v)?.clone();
                let mut error = None;
                a.sort_by(|a, b| {
                    match if e["reverse"] == true {
                        compare(b, a)
                    } else {
                        compare(a, b)
                    } {
                        Ok(v) => v,
                        Err(e) => {
                            error = Some(e);
                            Ordering::Equal
                        }
                    }
                });
                if let Some(e) = error {
                    return Err(e);
                }
                Ok(a.into())
            }
            "unique" => {
                let v = self.eval(&e["value"], s)?;
                let mut out = vec![];
                for x in array(&v)? {
                    if !out.contains(x) {
                        out.push(x.clone());
                    }
                }
                Ok(out.into())
            }
            "split" => {
                let v = self.eval(&e["value"], s)?;
                let t = string(&v)?;
                let sep = name("separator");
                let n = e["maxsplit"].as_i64().unwrap();
                let out: Vec<Value> = if n < 0 {
                    t.split(sep).map(Value::from).collect()
                } else {
                    t.splitn(n as usize + 1, sep).map(Value::from).collect()
                };
                Ok(out.into())
            }
            "parse_int" => {
                let v = self.eval(&e["value"], s)?;
                if v.is_number() {
                    return Ok(json!(int(&v)?));
                }
                let t = string(&v)?;
                let raw = t.trim();
                let valid = !raw.is_empty()
                    && !raw.starts_with('_')
                    && !raw.ends_with('_')
                    && !raw.contains("__")
                    && !raw.starts_with("+_")
                    && !raw.starts_with("-_");
                let parsed = if valid {
                    raw.replace('_', "").parse::<i64>().ok()
                } else {
                    None
                };
                parsed.map(|x| json!(x)).ok_or_else(|| {
                    Error::new(
                        "ValueError",
                        &format!("invalid literal for int() with base 10: {}", repr(t)),
                    )
                })
            }
            "parse_version" => {
                let v = self.eval(&e["value"], s)?;
                let t = string(&v)?;
                let version = Version::from_str(t).map_err(|_| {
                    Error::new("InvalidVersion", &format!("Invalid version: {}", repr(t)))
                })?;
                Ok(json!({"$version":version.to_string()}))
            }
            "version_part" => {
                let v = self.eval(&e["value"], s)?;
                let version = version(&v)?;
                let i = match name("part") {
                    "major" => 0,
                    "minor" => 1,
                    _ => 2,
                };
                Ok(json!(version.release().get(i).copied().unwrap_or(0)))
            }
            "format" => {
                let mut values = HashMap::new();
                for (k, v) in e["args"].as_object().unwrap() {
                    values.insert(k.clone(), display(&self.eval(v, s)?));
                }
                let mut result = String::new();
                let mut template = e["template"].as_str().unwrap();
                while let Some(start) = template.find('{') {
                    result.push_str(&template[..start]);
                    template = &template[start + 1..];
                    let end = template.find('}').unwrap();
                    result.push_str(values.get(&template[..end]).unwrap());
                    template = &template[end + 1..];
                }
                result.push_str(template);
                Ok(result.into())
            }
            "call" => {
                let mut args = vec![];
                for a in e["args"].as_array().unwrap() {
                    args.push(self.eval(a, s)?);
                }
                self.transport.call(name("function"), &args)
            }
            "try" => match self.eval(&e["body"], s) {
                Err(err)
                    if e["catch"]
                        .as_array()
                        .unwrap()
                        .contains(&json!(err.category)) =>
                {
                    self.eval(&e["fallback"], s)
                }
                r => r,
            },
            "finally" => {
                let result = self.eval(&e["body"], s);
                let previous = self.in_cleanup;
                self.in_cleanup = true;
                let cleanup = self.eval(&e["cleanup"], s);
                self.in_cleanup = previous;
                cleanup?;
                result
            }
            "warn" | "raise" => {
                let d = self.definition["diagnostics"][name("diagnostic")].clone();
                let text = self.eval(
                    &json!({"op":"format","template":d["message"],"args":e["args"]}),
                    s,
                )?;
                let category = d["category"].as_str().unwrap();
                let message = string(&text)?;
                if name("op") == "raise" {
                    Err(Error::new(category, message))
                } else {
                    self.warnings
                        .push(json!({"category":category,"message":message}));
                    Ok(Value::Null)
                }
            }
            "unpack" => {
                let v = self.eval(&e["value"], s)?;
                let a = array(&v)?;
                let count = e["count"].as_u64().unwrap() as usize;
                if a.len() < count {
                    Err(Error::new(
                        "ValueError",
                        &format!(
                            "not enough values to unpack (expected {count}, got {})",
                            a.len()
                        ),
                    ))
                } else if a.len() > count {
                    Err(Error::new(
                        "ValueError",
                        &format!("too many values to unpack (expected {count})"),
                    ))
                } else {
                    Ok(v)
                }
            }
            "configs" => {
                let mut out = vec![];
                let features = self.definition["features"].as_array().unwrap().clone();
                for f in features {
                    let v = self.query(f[name("mode")].as_str().unwrap())?;
                    if truth(&v) || name("mode") == "all" {
                        out.push(
                            json!({"name":f["name"],"values":v,"multi_value":f["multi_value"]}),
                        );
                    }
                }
                Ok(out.into())
            }
            _ => Err(Error::new("DefinitionError", "Unsupported operation")),
        }
    }
}
fn extend(out: &mut Vec<Value>, a: &[Value]) -> Result<(), Error> {
    if out.len() + a.len() > MAX_ITEMS {
        return Err(Error::new("ResourceError", "Collection limit exceeded"));
    }
    out.extend_from_slice(a);
    Ok(())
}
fn int(v: &Value) -> Result<i64, Error> {
    v.as_i64()
        .ok_or_else(|| Error::new("TypeError", "Integer required"))
}
fn array(v: &Value) -> Result<&Vec<Value>, Error> {
    v.as_array()
        .ok_or_else(|| Error::new("TypeError", "List required"))
}
fn string(v: &Value) -> Result<&str, Error> {
    v.as_str()
        .ok_or_else(|| Error::new("TypeError", "String required"))
}
fn version(v: &Value) -> Result<Version, Error> {
    Version::from_str(
        v["$version"]
            .as_str()
            .ok_or_else(|| Error::new("TypeError", "Version required"))?,
    )
    .map_err(|_| Error::new("InvalidVersion", "Invalid version"))
}
fn truth(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
        Value::Number(n) => n.as_i64() != Some(0),
    }
}
fn display(v: &Value) -> String {
    match v {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::String(s) => s.clone(),
        Value::Object(o) if o.contains_key("$version") => o["$version"].as_str().unwrap().into(),
        _ => v.to_string(),
    }
}
pub fn repr(s: &str) -> String {
    let quote = if s.contains('\'') && !s.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::from(quote);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c)
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}
fn compare(a: &Value, b: &Value) -> Result<Ordering, Error> {
    if a.get("$version").is_some() && b.get("$version").is_some() {
        return Ok(version(a)?.cmp(&version(b)?));
    }
    match (a, b) {
        (Value::Number(_), Value::Number(_)) => Ok(int(a)?.cmp(&int(b)?)),
        (Value::String(a), Value::String(b)) => Ok(a.cmp(b)),
        (Value::Array(a), Value::Array(b)) => {
            for (x, y) in a.iter().zip(b) {
                let c = compare(x, y)?;
                if c != Ordering::Equal {
                    return Ok(c);
                }
            }
            Ok(a.len().cmp(&b.len()))
        }
        _ if a == b => Ok(Ordering::Equal),
        _ => Err(Error::new("TypeError", "Incompatible comparison types")),
    }
}
fn equal(a: &Value, b: &Value) -> Result<bool, Error> {
    if a.get("$version").is_some() && b.get("$version").is_some() {
        return Ok(version(a)? == version(b)?);
    }
    match (a, b) {
        (Value::Bool(x), Value::Number(_)) => Ok(b.as_i64() == Some(i64::from(*x))),
        (Value::Number(_), Value::Bool(x)) => Ok(a.as_i64() == Some(i64::from(*x))),
        (Value::Array(a), Value::Array(b)) => {
            if a.len() != b.len() {
                return Ok(false);
            }
            for (x, y) in a.iter().zip(b) {
                if !equal(x, y)? {
                    return Ok(false);
                }
            }
            Ok(true)
        }
        _ => Ok(a == b),
    }
}
fn binary(kind: &str, a: Value, b: Value) -> Result<Value, Error> {
    if kind == "eq" || kind == "ne" {
        let eq = equal(&a, &b)?;
        return Ok(json!(if kind == "eq" { eq } else { !eq }));
    }
    if ["lt", "le", "gt", "ge"].contains(&kind) {
        let c = compare(&a, &b)?;
        return Ok(json!(match kind {
            "eq" => c == Ordering::Equal,
            "ne" => c != Ordering::Equal,
            "lt" => c == Ordering::Less,
            "le" => c != Ordering::Greater,
            "gt" => c == Ordering::Greater,
            _ => c != Ordering::Less,
        }));
    }
    if kind == "add" {
        if let (Some(a), Some(b)) = (a.as_str(), b.as_str()) {
            return Ok(json!(format!("{a}{b}")));
        }
        if let (Some(a), Some(b)) = (a.as_array(), b.as_array()) {
            let mut v = a.clone();
            extend(&mut v, b)?;
            return Ok(v.into());
        }
    }
    let a = int(&a)?;
    let b = int(&b)?;
    if (kind == "floor_div" || kind == "mod") && b == 0 {
        return Err(Error::new(
            "ZeroDivisionError",
            "integer division or modulo by zero",
        ));
    }
    let v = match kind {
        "add" => a.checked_add(b),
        "sub" => a.checked_sub(b),
        "mul" => a.checked_mul(b),
        "floor_div" | "mod" => {
            let q = a
                .checked_div(b)
                .ok_or_else(|| Error::new("OverflowError", "Integer overflow"))?;
            let r = a % b;
            let floor = if r != 0 && ((r < 0) != (b < 0)) {
                q - 1
            } else {
                q
            };
            if kind == "floor_div" {
                Some(floor)
            } else {
                Some(if r != 0 && ((r < 0) != (b < 0)) {
                    r + b
                } else {
                    r
                })
            }
        }
        _ => None,
    };
    v.map(|x| json!(x))
        .ok_or_else(|| Error::new("OverflowError", "Integer overflow"))
}
