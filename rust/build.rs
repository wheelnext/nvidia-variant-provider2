use sha2::{Digest, Sha256};
use std::{env, fs, path::PathBuf};
fn main() {
    let root = PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap());
    let shared = if root.join("../json").is_dir() {
        root.join("../json")
    } else {
        root.join("data")
    };
    let out = PathBuf::from(env::var("OUT_DIR").unwrap());
    for (name, dest) in [
        ("nvidia.json", "definition.json"),
        ("schemas/provider-1.0.0.schema.json", "schema.json"),
        ("schemas/case-1.0.0.schema.json", "case-schema.json"),
    ] {
        let path = shared.join(name);
        println!("cargo:rerun-if-changed={}", path.display());
        let bytes = fs::read(&path).expect("Bundled JSON resource missing");
        fs::write(out.join(dest), &bytes).unwrap();
        fs::write(
            out.join(format!("{dest}.sha256")),
            format!("{:x}", Sha256::digest(&bytes)),
        )
        .unwrap();
    }
}
