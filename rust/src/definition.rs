use base64::{
    engine::general_purpose::{STANDARD, URL_SAFE},
    Engine,
};
use ed25519_dalek::{Signature, VerifyingKey};
use serde::de::{self, MapAccess, SeqAccess, Visitor};
use serde::{Deserialize, Deserializer};
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::{HashMap, HashSet},
    fmt, fs,
    path::Path,
};

pub const PAYLOAD_TYPE: &str = "application/vnd.variant-provider.definition.v1+json";
pub const DEFINITION: &str = include_str!(concat!(env!("OUT_DIR"), "/definition.json"));
pub const SCHEMA: &str = include_str!(concat!(env!("OUT_DIR"), "/schema.json"));
const MAX_BYTES: usize = 4 * 1024 * 1024;

struct Strict(Value);
impl<'de> Deserialize<'de> for Strict {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct V;
        impl<'de> Visitor<'de> for V {
            type Value = Strict;
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                write!(f, "strict JSON")
            }
            fn visit_bool<E: de::Error>(self, v: bool) -> Result<Strict, E> {
                Ok(Strict(v.into()))
            }
            fn visit_i64<E: de::Error>(self, v: i64) -> Result<Strict, E> {
                Ok(Strict(v.into()))
            }
            fn visit_u64<E: de::Error>(self, v: u64) -> Result<Strict, E> {
                if v > i64::MAX as u64 {
                    Err(E::custom("JSON integer out of range"))
                } else {
                    Ok(Strict(v.into()))
                }
            }
            fn visit_f64<E: de::Error>(self, _: f64) -> Result<Strict, E> {
                Err(E::custom("Only integer JSON numbers are supported"))
            }
            fn visit_str<E: de::Error>(self, v: &str) -> Result<Strict, E> {
                Ok(Strict(v.into()))
            }
            fn visit_string<E: de::Error>(self, v: String) -> Result<Strict, E> {
                Ok(Strict(v.into()))
            }
            fn visit_unit<E: de::Error>(self) -> Result<Strict, E> {
                Ok(Strict(Value::Null))
            }
            fn visit_none<E: de::Error>(self) -> Result<Strict, E> {
                Ok(Strict(Value::Null))
            }
            fn visit_seq<A: SeqAccess<'de>>(self, mut a: A) -> Result<Strict, A::Error> {
                let mut out = vec![];
                while let Some(v) = a.next_element::<Strict>()? {
                    out.push(v.0);
                }
                Ok(Strict(out.into()))
            }
            fn visit_map<A: MapAccess<'de>>(self, mut a: A) -> Result<Strict, A::Error> {
                let mut out = Map::new();
                while let Some((k, v)) = a.next_entry::<String, Strict>()? {
                    if out.insert(k, v.0).is_some() {
                        return Err(de::Error::custom("Duplicate key"));
                    }
                }
                Ok(Strict(out.into()))
            }
        }
        d.deserialize_any(V)
    }
}
pub fn strict_json(bytes: &[u8]) -> Result<Value, String> {
    if bytes.len() > MAX_BYTES {
        return Err("JSON exceeds 4 MiB limit".into());
    }
    let v: Strict = serde_json::from_slice(bytes).map_err(|e| e.to_string())?;
    fn check(v: &Value, n: usize) -> bool {
        n <= 64
            && match v {
                Value::Object(o) => o.values().all(|x| check(x, n + 1)),
                Value::Array(a) => a.iter().all(|x| check(x, n + 1)),
                _ => true,
            }
    }
    if !check(&v.0, 0) {
        return Err("JSON exceeds nesting limit".into());
    }
    Ok(v.0)
}
pub fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
pub fn bundled() -> Result<Value, String> {
    if digest(DEFINITION.as_bytes())
        != include_str!(concat!(env!("OUT_DIR"), "/definition.json.sha256"))
    {
        return Err("Bundled digest mismatch".into());
    }
    let d = strict_json(DEFINITION.as_bytes())?;
    validate(&d)?;
    Ok(d)
}
pub fn validate(d: &Value) -> Result<(), String> {
    static VALIDATOR: std::sync::OnceLock<jsonschema::Validator> = std::sync::OnceLock::new();
    let validator = VALIDATOR.get_or_init(|| {
        let schema: Value = serde_json::from_str(SCHEMA).expect("Bundled schema JSON");
        jsonschema::draft202012::new(&schema).expect("Bundled schema")
    });
    validator
        .validate(d)
        .map_err(|e| format!("Schema error: {e}"))?;
    let queries = d["queries"].as_object().unwrap();
    let mut edges: HashMap<String, HashSet<String>> = queries
        .keys()
        .map(|k| (k.clone(), HashSet::new()))
        .collect();
    fn walk(
        e: &Value,
        scope: &HashSet<String>,
        d: &Value,
        deps: &mut HashSet<String>,
    ) -> Result<(), String> {
        let op = e["op"].as_str().unwrap();
        let name = e["name"].as_str().unwrap_or("");
        match op {
            "ref" if !scope.contains(name) => {
                return Err(format!("Unknown local reference: {name}"))
            }
            "query" => {
                if d["queries"].get(name).is_none() {
                    return Err(format!("Unknown query: {name}"));
                }
                deps.insert(name.into());
            }
            "const" if d["constants"].get(name).is_none() => return Err("Unknown constant".into()),
            "call" => {
                let n = e["function"].as_str().unwrap();
                let f = d["functions"].get(n).ok_or("Unknown function")?;
                let count = f["args"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .filter(|a| a["direction"] == "in")
                    .count();
                if e["args"].as_array().unwrap().len() != count {
                    return Err("Native argument count mismatch".into());
                }
            }
            "configs" => {
                for f in d["features"].as_array().unwrap() {
                    deps.insert(f[e["mode"].as_str().unwrap()].as_str().unwrap().into());
                }
            }
            "warn" | "raise"
                if d["diagnostics"]
                    .get(e["diagnostic"].as_str().unwrap())
                    .is_none() =>
            {
                return Err("Unknown diagnostic".into());
            }
            _ => {}
        }
        if op == "let" {
            let mut local = scope.clone();
            for b in e["bindings"].as_array().unwrap() {
                let n = b["name"].as_str().unwrap();
                if local.contains(n) {
                    return Err("Duplicate local binding".into());
                }
                walk(&b["value"], &local, d, deps)?;
                local.insert(n.into());
            }
            return walk(&e["body"], &local, d, deps);
        }
        if op == "map" || op == "filter" {
            walk(&e["items"], scope, d, deps)?;
            let mut local = scope.clone();
            local.insert(e["var"].as_str().unwrap().into());
            return walk(&e["body"], &local, d, deps);
        }
        fn children(
            v: &Value,
            scope: &HashSet<String>,
            d: &Value,
            deps: &mut HashSet<String>,
        ) -> Result<(), String> {
            match v {
                Value::Object(o) => {
                    if o.get("op").is_some_and(Value::is_string) {
                        walk(v, scope, d, deps)?;
                    } else {
                        for x in o.values() {
                            children(x, scope, d, deps)?;
                        }
                    }
                }
                Value::Array(a) => {
                    for x in a {
                        children(x, scope, d, deps)?;
                    }
                }
                _ => {}
            }
            Ok(())
        }
        for (k, v) in e.as_object().unwrap() {
            if !(op == "literal" && k == "value") {
                children(v, scope, d, deps)?;
            }
        }
        Ok(())
    }
    for (name, q) in queries {
        walk(&q["expr"], &HashSet::new(), d, edges.get_mut(name).unwrap())?;
    }
    fn visit(
        n: &str,
        edges: &HashMap<String, HashSet<String>>,
        active: &mut HashSet<String>,
        done: &mut HashSet<String>,
    ) -> Result<(), String> {
        if active.contains(n) {
            return Err("Cyclic query dependency".into());
        }
        if done.contains(n) {
            return Ok(());
        }
        if active.len() >= 64 {
            return Err("Query dependency depth exceeded".into());
        }
        active.insert(n.into());
        for child in edges.get(n).ok_or("Unknown query")? {
            visit(child, edges, active, done)?;
        }
        active.remove(n);
        done.insert(n.into());
        Ok(())
    }
    let mut done = HashSet::new();
    for n in queries.keys() {
        visit(n, &edges, &mut HashSet::new(), &mut done)?;
    }
    fn query_depth(name: &str, d: &Value, memo: &mut HashMap<String, usize>) -> usize {
        if let Some(n) = memo.get(name) {
            return *n;
        }
        let depth = expression_depth(&d["queries"][name]["expr"], d, memo);
        memo.insert(name.into(), depth);
        depth
    }
    fn expression_depth(e: &Value, d: &Value, memo: &mut HashMap<String, usize>) -> usize {
        match e["op"].as_str().unwrap() {
            "literal" => 1,
            "query" => 1 + query_depth(e["name"].as_str().unwrap(), d, memo),
            "configs" => {
                1 + d["features"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|f| query_depth(f[e["mode"].as_str().unwrap()].as_str().unwrap(), d, memo))
                    .max()
                    .unwrap_or(0)
            }
            _ => {
                1 + e
                    .as_object()
                    .unwrap()
                    .values()
                    .map(|v| child_depth(v, d, memo))
                    .max()
                    .unwrap_or(0)
            }
        }
    }
    fn child_depth(v: &Value, d: &Value, memo: &mut HashMap<String, usize>) -> usize {
        match v {
            Value::Object(o) if o.get("op").is_some_and(Value::is_string) => {
                expression_depth(v, d, memo)
            }
            Value::Object(o) => o
                .values()
                .map(|v| child_depth(v, d, memo))
                .max()
                .unwrap_or(0),
            Value::Array(a) => a.iter().map(|v| child_depth(v, d, memo)).max().unwrap_or(0),
            _ => 0,
        }
    }
    let mut depths = HashMap::new();
    for name in queries.keys() {
        if query_depth(name, d, &mut depths) > 64 {
            return Err("Expanded expression depth exceeded".into());
        }
    }
    for n in d["exports"].as_object().unwrap().values() {
        if !queries.contains_key(n.as_str().unwrap()) {
            return Err("Unknown exported query".into());
        }
    }
    for f in d["functions"].as_object().unwrap().values() {
        if d["libraries"].get(f["library"].as_str().unwrap()).is_none() {
            return Err("Unknown library".into());
        }
        let mut names = HashSet::new();
        for a in f["args"].as_array().unwrap() {
            if !names.insert(a["name"].as_str().unwrap()) {
                return Err("Duplicate native argument name".into());
            }
            if a["type"] == "buffer"
                && (a["direction"] != "out" || a["size"].as_u64().unwrap_or(0) == 0)
            {
                return Err("Invalid output buffer".into());
            }
        }
    }
    let mut features = HashSet::new();
    for f in d["features"].as_array().unwrap() {
        if !features.insert(f["name"].as_str().unwrap()) {
            return Err("Duplicate feature".into());
        }
        for key in ["all", "supported"] {
            if !queries.contains_key(f[key].as_str().unwrap()) {
                return Err("Unknown feature query".into());
            }
        }
    }
    fn reserved(v: &Value) -> bool {
        match v {
            Value::Object(o) => o.iter().any(|(k, x)| k.starts_with('$') || reserved(x)),
            Value::Array(a) => a.iter().any(reserved),
            _ => false,
        }
    }
    fn literals(v: &Value) -> bool {
        match v {
            Value::Object(o) => {
                if o.get("op") == Some(&Value::from("literal")) {
                    reserved(&v["value"])
                } else {
                    o.values().any(literals)
                }
            }
            Value::Array(a) => a.iter().any(literals),
            _ => false,
        }
    }
    if reserved(&d["constants"]) || literals(&d["queries"]) {
        return Err("Reserved value tag".into());
    }
    crate::typecheck::validate(d)?;
    Ok(())
}
pub fn pae(payload: &[u8]) -> Vec<u8> {
    let mut v = format!(
        "DSSEv1 {} {} {} ",
        PAYLOAD_TYPE.len(),
        PAYLOAD_TYPE,
        payload.len()
    )
    .into_bytes();
    v.extend_from_slice(payload);
    v
}
fn decode(s: &str) -> Result<Vec<u8>, String> {
    STANDARD
        .decode(s)
        .or_else(|_| URL_SAFE.decode(s))
        .map_err(|e| e.to_string())
}
pub fn verify(envelope: &[u8], trust: &Value) -> Result<Vec<u8>, String> {
    let e = strict_json(envelope)?;
    let o = e.as_object().ok_or("Invalid envelope")?;
    if o.len() != 3 || e["payloadType"] != PAYLOAD_TYPE {
        return Err("Invalid signature envelope or payload type".into());
    }
    let payload = decode(e["payload"].as_str().ok_or("Missing payload")?)?;
    let mut accepted = vec![];
    for s in e["signatures"].as_array().ok_or("Missing signatures")? {
        if s.as_object()
            .ok_or("Invalid signature")?
            .keys()
            .any(|k| k != "sig" && k != "keyid")
        {
            return Err("Invalid signature record".into());
        }
        let signature =
            Signature::from_slice(&decode(s["sig"].as_str().ok_or("Missing signature")?)?)
                .map_err(|e| e.to_string())?;
        for entry in trust.as_array().ok_or("Trust must be an array")? {
            let bytes: [u8; 32] = decode(entry["public_key"].as_str().ok_or("Missing key")?)?
                .try_into()
                .map_err(|_| "Invalid key size")?;
            let key = VerifyingKey::from_bytes(&bytes).map_err(|e| e.to_string())?;
            if key.verify_strict(&pae(&payload), &signature).is_ok() {
                accepted.push(entry);
            }
        }
    }
    if accepted.is_empty() {
        return Err("No trusted valid signature".into());
    }
    let d = strict_json(&payload)?;
    if !accepted.iter().any(|e| {
        e["provider"] == d["provider"]["id"]
            && e["namespace"] == d["provider"]["namespace"]
            && d["data_version"].as_u64().unwrap_or(0)
                >= e["minimum_data_version"].as_u64().unwrap_or(1)
            && (e["sha256"].is_null() || e["sha256"] == digest(&payload))
    }) {
        return Err("Signature trust scope or data version mismatch".into());
    }
    Ok(payload)
}
pub fn load(
    path: &Path,
    signature: Option<&Path>,
    trust: &Value,
    allow_unsigned: bool,
) -> Result<Value, String> {
    let payload = fs::read(path).map_err(|e| e.to_string())?;
    if let Some(sig) = signature {
        if verify(&fs::read(sig).map_err(|e| e.to_string())?, trust)? != payload {
            return Err("Signed payload differs from definition bytes".into());
        }
    } else if !allow_unsigned {
        return Err("External definitions require a trusted signature".into());
    }
    let d = strict_json(&payload)?;
    validate(&d)?;
    Ok(d)
}
