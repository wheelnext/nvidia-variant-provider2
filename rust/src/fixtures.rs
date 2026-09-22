//! Language-neutral conformance case reader, shared by tests and CLI.
use crate::{definition, Provider, ReplayTransport};
use serde_json::{json, Value};
use std::{
    fs,
    path::{Path, PathBuf},
    sync::OnceLock,
};
pub fn read_case(path: &Path) -> Result<Value, String> {
    let case = definition::strict_json(&fs::read(path).map_err(|e| e.to_string())?)?;
    static VALIDATOR: OnceLock<jsonschema::Validator> = OnceLock::new();
    let validator = VALIDATOR.get_or_init(|| {
        jsonschema::draft202012::new(
            &serde_json::from_str(include_str!(concat!(env!("OUT_DIR"), "/case-schema.json")))
                .unwrap(),
        )
        .unwrap()
    });
    validator.validate(&case).map_err(|e| e.to_string())?;
    Ok(case)
}
pub fn inventory(directory: &Path) -> Result<Vec<PathBuf>, String> {
    let mut paths = vec![];
    for entry in fs::read_dir(directory).map_err(|e| e.to_string())? {
        let path = entry.map_err(|e| e.to_string())?.path();
        if path.extension().is_some_and(|e| e == "json") {
            read_case(&path)?;
            paths.push(path);
        }
    }
    paths.sort();
    if paths.is_empty() {
        return Err("No shared cases discovered".into());
    }
    Ok(paths)
}
pub fn run_case(path: &Path, check: bool) -> Result<Vec<Value>, String> {
    let case = read_case(path)?;
    let root = path
        .parent()
        .and_then(Path::parent)
        .and_then(Path::parent)
        .ok_or("Expected json/tests/cases layout")?;
    let d = definition::load(
        &root.join(case["definition"].as_str().unwrap()),
        None,
        &json!([]),
        true,
    )?;
    let mut p = Provider::new(
        d,
        Box::new(ReplayTransport::new(
            case["calls"].as_array().unwrap().clone(),
        )),
    )?;
    p.environment =
        Some(serde_json::from_value(case["environment"].clone()).map_err(|e| e.to_string())?);
    let mut results = vec![];
    for step in case["steps"].as_array().unwrap() {
        if let Some(e) = step.get("environment") {
            p.environment = Some(serde_json::from_value(e.clone()).map_err(|e| e.to_string())?);
        }
        if step["clear_cache"] == true {
            p.clear_cache();
        }
        let result = p.invoke(step["method"].as_str().unwrap());
        if check && result != step["expected"] {
            return Err(format!(
                "{} / {}\nactual: {}\nexpected: {}",
                case["id"], step["method"], result, step["expected"]
            ));
        }
        results.push(result);
    }
    p.finish().map_err(|e| e.to_string())?;
    Ok(results)
}
