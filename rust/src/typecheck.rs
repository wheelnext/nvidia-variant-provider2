use serde_json::Value;
use std::collections::{HashMap, HashSet};

#[derive(Clone, Default)]
struct Ty {
    kinds: HashSet<String>,
    fields: Option<HashMap<String, Ty>>,
    item: Option<Box<Ty>>,
}
impl Ty {
    fn new(k: &str) -> Self {
        Self {
            kinds: HashSet::from([k.into()]),
            ..Self::default()
        }
    }
    fn list(t: Ty) -> Self {
        Self {
            item: Some(Box::new(t)),
            ..Self::new("list")
        }
    }
}
fn merge(types: Vec<Ty>) -> Ty {
    let mut result = Ty::default();
    let mut fields: HashMap<String, Vec<Ty>> = HashMap::new();
    let mut items = vec![];
    for t in types {
        result.kinds.extend(t.kinds);
        if let Some(f) = t.fields {
            for (k, v) in f {
                fields.entry(k).or_default().push(v);
            }
        }
        if let Some(i) = t.item {
            items.push(*i);
        }
    }
    if !fields.is_empty() {
        result.fields = Some(fields.into_iter().map(|(k, v)| (k, merge(v))).collect());
    }
    if !items.is_empty() {
        result.item = Some(Box::new(merge(items)));
    }
    result
}
fn expect(t: &Ty, kinds: &[&str]) -> Result<(), String> {
    if !t.kinds.is_empty() && !kinds.iter().any(|k| t.kinds.contains(*k)) {
        Err(format!(
            "Type mismatch: expected {kinds:?}, got {:?}",
            t.kinds
        ))
    } else {
        Ok(())
    }
}
fn literal(v: &Value) -> Ty {
    match v {
        Value::Null => Ty::new("null"),
        Value::Bool(_) => Ty::new("bool"),
        Value::Number(_) => Ty::new("int"),
        Value::String(_) => Ty::new("str"),
        Value::Array(a) => Ty::list(merge(a.iter().map(literal).collect())),
        Value::Object(o) => Ty {
            fields: Some(o.iter().map(|(k, v)| (k.clone(), literal(v))).collect()),
            ..Ty::new("record")
        },
    }
}
fn placeholders(template: &str, args: &Value) -> Result<(), String> {
    let mut rest = template;
    while let Some(start) = rest.find(['{', '}']) {
        if &rest[start..start + 1] != "{" {
            return Err("Invalid format template".into());
        }
        rest = &rest[start + 1..];
        let end = rest.find('}').ok_or("Invalid format template")?;
        let name = &rest[..end];
        if name.is_empty()
            || !name
                .chars()
                .enumerate()
                .all(|(i, c)| c == '_' || c.is_ascii_alphabetic() || (i > 0 && c.is_ascii_digit()))
        {
            return Err("Invalid format template".into());
        }
        if args.get(name).is_none() {
            return Err("Missing format argument".into());
        }
        rest = &rest[end + 1..];
    }
    Ok(())
}
struct Checker<'a> {
    d: &'a Value,
    memo: HashMap<String, Ty>,
}
impl Checker<'_> {
    fn query(&mut self, name: &str) -> Result<Ty, String> {
        if let Some(t) = self.memo.get(name) {
            return Ok(t.clone());
        }
        let expr = self.d["queries"][name]["expr"].clone();
        let t = self.infer(&expr, &HashMap::new())?;
        self.memo.insert(name.into(), t.clone());
        Ok(t)
    }
    fn infer(&mut self, e: &Value, s: &HashMap<String, Ty>) -> Result<Ty, String> {
        let op = e["op"].as_str().unwrap();
        let n = |k: &str| e[k].as_str().unwrap();
        Ok(match op {
            "literal" => literal(&e["value"]),
            "const" => literal(&self.d["constants"][n("name")]),
            "ref" => s[n("name")].clone(),
            "query" => self.query(n("name"))?,
            "env" => merge(vec![Ty::new("str"), Ty::new("null")]),
            "let" => {
                let mut local = s.clone();
                for b in e["bindings"].as_array().unwrap() {
                    let t = self.infer(&b["value"], &local)?;
                    local.insert(b["name"].as_str().unwrap().into(), t);
                }
                self.infer(&e["body"], &local)?
            }
            "if" => {
                self.infer(&e["condition"], s)?;
                merge(vec![self.infer(&e["then"], s)?, self.infer(&e["else"], s)?])
            }
            "list" | "concat" => {
                let mut items = vec![];
                for v in e["items"].as_array().unwrap() {
                    let t = self.infer(v, s)?;
                    if op == "concat" {
                        expect(&t, &["list"])?;
                        items.push(t.item.map(|i| *i).unwrap_or_default());
                    } else {
                        items.push(t);
                    }
                }
                Ty::list(merge(items))
            }
            "record" => {
                let mut fields = HashMap::new();
                for (k, v) in e["fields"].as_object().unwrap() {
                    if k.starts_with('$') {
                        return Err("Reserved record field".into());
                    }
                    fields.insert(k.clone(), self.infer(v, s)?);
                }
                Ty {
                    fields: Some(fields),
                    ..Ty::new("record")
                }
            }
            "get" => {
                let t = self.infer(&e["value"], s)?;
                expect(&t, &["record"])?;
                match t.fields {
                    Some(f) => f.get(n("key")).cloned().ok_or("Unknown record field")?,
                    None => Ty::default(),
                }
            }
            "index" => {
                let t = self.infer(&e["value"], s)?;
                expect(&t, &["list"])?;
                expect(&self.infer(&e["index"], s)?, &["int"])?;
                t.item.map(|i| *i).unwrap_or_default()
            }
            "seq" => {
                let mut t = Ty::new("null");
                for x in e["items"].as_array().unwrap() {
                    t = self.infer(x, s)?;
                }
                t
            }
            "binary" => {
                let a = self.infer(&e["left"], s)?;
                let b = self.infer(&e["right"], s)?;
                if ["eq", "ne", "lt", "le", "gt", "ge"].contains(&n("kind")) {
                    Ty::new("bool")
                } else {
                    let kinds = if n("kind") == "add" {
                        vec!["int", "str", "list"]
                    } else {
                        vec!["int"]
                    };
                    expect(&a, &kinds)?;
                    expect(&b, &kinds)?;
                    if !a.kinds.is_empty() && !b.kinds.is_empty() && a.kinds.is_disjoint(&b.kinds) {
                        return Err("Incompatible arithmetic types".into());
                    }
                    merge(vec![a, b])
                }
            }
            "not" | "is_null" => {
                self.infer(&e["value"], s)?;
                Ty::new("bool")
            }
            "range" => {
                for k in ["start", "stop", "step"] {
                    expect(&self.infer(&e[k], s)?, &["int"])?;
                }
                Ty::list(Ty::new("int"))
            }
            "map" | "filter" => {
                let t = self.infer(&e["items"], s)?;
                expect(&t, &["list"])?;
                let mut local = s.clone();
                local.insert(
                    n("var").into(),
                    t.item.clone().map(|i| *i).unwrap_or_default(),
                );
                let body = self.infer(&e["body"], &local)?;
                if op == "map" {
                    Ty::list(body)
                } else {
                    t
                }
            }
            "sort" | "unique" | "unpack" => {
                let t = self.infer(&e["value"], s)?;
                expect(&t, &["list"])?;
                t
            }
            "flatten" => {
                let t = self.infer(&e["value"], s)?;
                expect(&t, &["list"])?;
                let item = t.item.map(|i| *i).unwrap_or_default();
                expect(&item, &["list"])?;
                if item.kinds.is_empty() {
                    Ty::new("list")
                } else {
                    item
                }
            }
            "split" => {
                expect(&self.infer(&e["value"], s)?, &["str"])?;
                Ty::list(Ty::new("str"))
            }
            "parse_int" => {
                expect(&self.infer(&e["value"], s)?, &["str", "int"])?;
                Ty::new("int")
            }
            "parse_version" => {
                expect(&self.infer(&e["value"], s)?, &["str"])?;
                Ty::new("version")
            }
            "version_part" => {
                expect(&self.infer(&e["value"], s)?, &["version"])?;
                Ty::new("int")
            }
            "format" => {
                placeholders(n("template"), &e["args"])?;
                for v in e["args"].as_object().unwrap().values() {
                    expect(
                        &self.infer(v, s)?,
                        &["str", "int", "bool", "null", "version"],
                    )?;
                }
                Ty::new("str")
            }
            "call" => {
                let f = self.d["functions"][n("function")].clone();
                let mut inputs = e["args"].as_array().unwrap().iter();
                let mut fields = HashMap::new();
                for arg in f["args"].as_array().unwrap() {
                    let kind = match arg["type"].as_str().unwrap() {
                        "handle" => format!("handle:{}", f["library"].as_str().unwrap()),
                        "buffer" => "str".into(),
                        _ => "int".into(),
                    };
                    if arg["direction"] == "in" {
                        expect(&self.infer(inputs.next().unwrap(), s)?, &[&kind])?;
                    } else {
                        fields.insert(arg["name"].as_str().unwrap().into(), Ty::new(&kind));
                    }
                }
                Ty {
                    fields: Some(fields),
                    ..Ty::new("record")
                }
            }
            "try" => merge(vec![
                self.infer(&e["body"], s)?,
                self.infer(&e["fallback"], s)?,
            ]),
            "finally" => {
                let t = self.infer(&e["body"], s)?;
                self.infer(&e["cleanup"], s)?;
                t
            }
            "warn" | "raise" => {
                placeholders(
                    self.d["diagnostics"][n("diagnostic")]["message"]
                        .as_str()
                        .unwrap(),
                    &e["args"],
                )?;
                for v in e["args"].as_object().unwrap().values() {
                    expect(
                        &self.infer(v, s)?,
                        &["str", "int", "bool", "null", "version"],
                    )?;
                }
                if op == "warn" {
                    Ty::new("null")
                } else {
                    Ty::default()
                }
            }
            "configs" => {
                let features = self.d["features"].as_array().unwrap().clone();
                for f in features {
                    let t = self.query(f[n("mode")].as_str().unwrap())?;
                    expect(&t, &["list"])?;
                    expect(&t.item.map(|i| *i).unwrap_or_default(), &["str"])?;
                }
                Ty::list(Ty::new("record"))
            }
            _ => return Err("Unknown operation".into()),
        })
    }
}
fn handles(t: &Ty) -> bool {
    t.kinds.iter().any(|k| k.starts_with("handle:"))
        || t.fields.as_ref().is_some_and(|f| f.values().any(handles))
        || t.item.as_ref().is_some_and(|i| handles(i))
}
pub fn validate(d: &Value) -> Result<(), String> {
    let mut checker = Checker {
        d,
        memo: HashMap::new(),
    };
    for name in d["queries"].as_object().unwrap().keys() {
        checker.query(name)?;
    }
    for name in d["exports"].as_object().unwrap().values() {
        if handles(&checker.query(name.as_str().unwrap())?) {
            return Err("Native handles cannot be exported".into());
        }
    }
    Ok(())
}
