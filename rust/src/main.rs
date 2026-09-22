use serde_json::{json, Value};
use std::{env, fs, path::Path};
use variant_provider::{definition, fixtures, Provider};
fn run() -> Result<(), String> {
    let args: Vec<String> = env::args().skip(1).collect();
    let command = args.first().map(String::as_str).unwrap_or("help");
    let flag = |name: &str| {
        args.iter()
            .position(|a| a == name)
            .and_then(|i| args.get(i + 1))
            .map(String::as_str)
    };
    if command == "help" {
        println!("variant-provider-rs validate|invoke|replay [--definition PATH --signature PATH --trust PATH --allow-unsigned] [--method NAME|--case PATH]");
        return Ok(());
    }
    if command == "inventory" {
        let paths = fixtures::inventory(Path::new(flag("--cases").ok_or("--cases required")?))?;
        let names: Vec<_> = paths
            .iter()
            .map(|p| p.file_name().unwrap().to_string_lossy().into_owned())
            .collect();
        println!("{}", json!({"cases":names}));
        return Ok(());
    }
    if command == "replay" {
        let results =
            fixtures::run_case(Path::new(flag("--case").ok_or("--case required")?), false)?;
        println!("{}", json!({"results":results}));
        return Ok(());
    }
    let trust = match flag("--trust") {
        Some(p) => definition::strict_json(&fs::read(p).map_err(|e| e.to_string())?)?,
        None => json!([]),
    };
    let d = match flag("--definition") {
        Some(p) => definition::load(
            Path::new(p),
            flag("--signature").map(Path::new),
            &trust,
            args.iter().any(|a| a == "--allow-unsigned"),
        )?,
        None => definition::bundled()?,
    };
    if command == "validate" {
        println!(
            "{}",
            json!({"valid":true,"provider":d["provider"],"format_version":d["format_version"],"data_version":d["data_version"],"definition_sha256":definition::digest(&match flag("--definition"){Some(p)=>fs::read(p).map_err(|e|e.to_string())?,None=>definition::DEFINITION.as_bytes().to_vec()}),"schema_sha256":definition::digest(definition::SCHEMA.as_bytes())})
        );
        return Ok(());
    }
    if command != "invoke" {
        return Err("Unknown command".into());
    }
    let mut p = Provider::live(d)?;
    let methods = flag("--method")
        .unwrap_or("get_supported_configs")
        .split(',');
    let results: Vec<Value> = methods.map(|m| p.invoke(m)).collect();
    println!("{}", json!({"results":results}));
    Ok(())
}
fn main() {
    if let Err(e) = run() {
        eprintln!("{e}");
        std::process::exit(2);
    }
}
