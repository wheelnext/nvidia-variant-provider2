use serde_json::{json, Value};
use std::{fs, path::PathBuf};
use variant_provider::{definition, Provider, ReplayTransport};
fn example() -> Value {
    definition::strict_json(
        &fs::read(
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("../json/tests/definitions/example.json"),
        )
        .unwrap(),
    )
    .unwrap()
}
#[test]
fn strict_parsing() {
    for s in [r#"{"a":1,"a":2}"#, r#"{"a":NaN}"#, r#"{"a":1.2}"#] {
        assert!(definition::strict_json(s.as_bytes()).is_err());
    }
}
#[test]
fn rejects_unknown_versions_operations_and_cycles() {
    let mut d = example();
    d["format_version"] = json!("2.0.0");
    assert!(definition::validate(&d).is_err());
    let mut d = example();
    d["queries"]["all"]["expr"] = json!({"op":"eval","source":"x"});
    assert!(definition::validate(&d).is_err());
    let mut d = example();
    d["queries"]["all"]["expr"] = json!({"op":"query","name":"all"});
    assert!(definition::validate(&d).is_err());
    let mut d = example();
    d["queries"]["all"]["expr"] = json!({"op":"configs","mode":"all"});
    assert!(definition::validate(&d).is_err());
}
#[test]
fn rejects_invalid_types_and_handle_forgery() {
    let mut d = example();
    d["queries"]["all"]["expr"] = json!({"op":"range","start":{"op":"literal","value":"bad"},"stop":{"op":"literal","value":1},"step":{"op":"literal","value":1}});
    assert!(definition::validate(&d).is_err());
    let mut d = example();
    d["queries"]["all"]["expr"] =
        json!({"op":"record","fields":{"$handle":{"op":"literal","value":"1"}}});
    assert!(definition::validate(&d).is_err());
}
#[test]
fn independent_instances_and_json_updates() {
    let d = example();
    let mut a = Provider::new(d.clone(), Box::new(ReplayTransport::new(vec![]))).unwrap();
    let mut changed = d;
    changed["constants"]["values"] = json!(["ultra", "safe"]);
    changed["data_version"] = json!(2);
    let mut b = Provider::new(changed, Box::new(ReplayTransport::new(vec![]))).unwrap();
    assert_eq!(
        a.evaluate("get_all_configs").unwrap()[0]["values"],
        json!(["fast", "portable"])
    );
    assert_eq!(
        b.evaluate("get_all_configs").unwrap()[0]["values"],
        json!(["ultra", "safe"])
    );
}
#[test]
fn resource_limits() {
    let mut d = example();
    d["queries"]["probe"] = json!({"cache":false,"expr":{"op":"range","start":{"op":"literal","value":0},"stop":{"op":"literal","value":100001},"step":{"op":"literal","value":1}}});
    d["exports"]["probe"] = json!("probe");
    let mut p = Provider::new(d, Box::new(ReplayTransport::new(vec![]))).unwrap();
    assert_eq!(p.invoke("probe")["error"]["category"], "ResourceError");
}
#[test]
fn local_signature_and_tampering() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../json");
    let trust =
        definition::strict_json(&fs::read(root.join("signatures/development-trust.json")).unwrap())
            .unwrap();
    let bytes = fs::read(root.join("signatures/nvidia.dsse.json")).unwrap();
    assert_eq!(
        definition::verify(&bytes, &trust).unwrap(),
        fs::read(root.join("nvidia.json")).unwrap()
    );
    let mut e = definition::strict_json(&bytes).unwrap();
    e["payloadType"] = json!("application/json");
    assert!(definition::verify(&serde_json::to_vec(&e).unwrap(), &trust).is_err());
    assert!(definition::verify(&bytes, &json!([])).is_err());
    let mut scope = trust.clone();
    scope[0]["namespace"] = json!("wrong");
    assert!(definition::verify(&bytes, &scope).is_err());
    let mut floor = trust;
    floor[0]["minimum_data_version"] = json!(2);
    assert!(definition::verify(&bytes, &floor).is_err());
}

#[test]
fn literal_format_and_stable_sort() {
    let mut d = example();
    d["queries"]["probe"] = json!({"cache":false,"expr":{"op":"format","template":"{a}/{b}","args":{"a":{"op":"literal","value":"{b}"},"b":{"op":"literal","value":"end"}}}});
    d["exports"]["probe"] = json!("probe");
    let mut p = Provider::new(d.clone(), Box::new(ReplayTransport::new(vec![]))).unwrap();
    assert_eq!(p.evaluate("probe").unwrap(), "{b}/end");
    d["queries"]["probe"]["expr"] = json!({"op":"sort","reverse":true,"value":{"op":"list","items":[{"op":"parse_version","value":{"op":"literal","value":"12.0"}},{"op":"parse_version","value":{"op":"literal","value":"12"}},{"op":"parse_version","value":{"op":"literal","value":"13"}}]}});
    let mut p = Provider::new(d, Box::new(ReplayTransport::new(vec![]))).unwrap();
    assert_eq!(
        p.evaluate("probe").unwrap(),
        json!([{"$version":"13"},{"$version":"12.0"},{"$version":"12"}])
    );
}
#[test]
fn bounded_query_depth() {
    let mut d = example();
    for i in 0..70 {
        let expr = if i < 69 {
            json!({"op":"query","name":format!("q{}",i+1)})
        } else {
            json!({"op":"literal","value":1})
        };
        d["queries"][format!("q{i}")] = json!({"cache":false,"expr":expr});
    }
    assert!(definition::validate(&d).is_err());
}

#[test]
fn record_and_format_keys_can_be_op() {
    let mut d = example();
    d["queries"]["probe"] = json!({"cache":false,"expr":{"op":"record","fields":{"op":{"op":"format","template":"{op}","args":{"op":{"op":"literal","value":"ordinary"}}}}}});
    d["exports"]["probe"] = json!("probe");
    let mut p = Provider::new(d, Box::new(ReplayTransport::new(vec![]))).unwrap();
    assert_eq!(p.evaluate("probe").unwrap(), json!({"op":"ordinary"}));
}
