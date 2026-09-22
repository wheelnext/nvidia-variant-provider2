use serde_json::Value;
use std::{fs, path::PathBuf, process::Command};
use variant_provider::{definition, native::NativeTransport, Provider, Transport};
#[test]
fn real_ffi_matches_shared_fixtures() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../json");
    let directory =
        std::env::temp_dir().join(format!("variant-provider-native-{}", std::process::id()));
    fs::create_dir_all(&directory).unwrap();
    let source = root.join("tests/native/probe.c");
    let library = directory.join(if cfg!(windows) {
        "probe.dll"
    } else if cfg!(target_os = "macos") {
        "libprobe.dylib"
    } else {
        "libprobe.so"
    });
    let output = if cfg!(windows) {
        Command::new("cl")
            .args(["/nologo", "/LD"])
            .arg(&source)
            .arg(format!("/Fe:{}", library.display()))
            .arg(format!("/Fo:{}/", directory.display()))
            .arg("/link")
            .arg(format!("/IMPLIB:{}/probe.lib", directory.display()))
            .output()
            .unwrap()
    } else {
        Command::new(std::env::var("CC").unwrap_or("cc".into()))
            .arg(if cfg!(target_os = "macos") {
                "-dynamiclib"
            } else {
                "-shared"
            })
            .arg("-fPIC")
            .arg(&source)
            .arg("-o")
            .arg(&library)
            .output()
            .unwrap()
    };
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    for (name, libname) in [
        ("detected_12070", "management"),
        ("native_example", "probe"),
    ] {
        let case: Value = serde_json::from_slice(
            &fs::read(root.join(format!("tests/cases/{name}.json"))).unwrap(),
        )
        .unwrap();
        let d = definition::load(
            &root.join(case["definition"].as_str().unwrap()),
            None,
            &serde_json::json!([]),
            true,
        )
        .unwrap();
        let mut native = NativeTransport::new(d.clone());
        native.overrides.insert(libname.into(), library.clone());
        let mut p = Provider::new(d, Box::new(native)).unwrap();
        p.environment = Some(Default::default());
        for step in case["steps"].as_array().unwrap() {
            assert_eq!(
                p.invoke(step["method"].as_str().unwrap()),
                step["expected"],
                "{name}"
            );
        }
    }
    let d = definition::load(
        &root.join("tests/definitions/native-example.json"),
        None,
        &serde_json::json!([]),
        true,
    )
    .unwrap();
    let mut native = NativeTransport::new(d);
    native.overrides.insert("probe".into(), library);
    let old = native.call("open", &[]).unwrap()["handle"].clone();
    native.call("close", &[]).unwrap();
    let fresh = native.call("open", &[]).unwrap()["handle"].clone();
    assert_ne!(old, fresh);
    assert!(native.call("read", &[old, serde_json::json!(1)]).is_err());
    native.call("close", &[]).unwrap();
    drop(native);
    fs::remove_dir_all(directory).unwrap();
}
