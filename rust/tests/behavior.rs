use std::path::PathBuf;
use variant_provider::fixtures;
#[test]
fn shared_behavior_corpus() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../json/tests/cases");
    let paths = fixtures::inventory(&root).unwrap();
    let failures: Vec<_> = paths
        .iter()
        .filter_map(|p| fixtures::run_case(p, true).err())
        .collect();
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}
