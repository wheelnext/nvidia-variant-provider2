use crate::{Error, Transport};
use libffi::middle::{Arg, Cif, CodePtr, Type};
use libloading::Library;
use serde_json::{json, Value};
use std::{collections::HashMap, ffi::c_void, path::PathBuf};

pub struct NativeTransport {
    definition: Value,
    libraries: HashMap<String, Library>,
    handles: HashMap<String, (String, *mut c_void)>,
    pub overrides: HashMap<String, PathBuf>,
    pub environment: Option<HashMap<String, String>>,
    pub trace: Vec<Value>,
    next_handle: u64,
}
enum Slot {
    I32(i32),
    U32(u32),
    I64(i64),
    U64(u64),
    Handle(*mut c_void),
    Buffer(Vec<u8>),
}
impl Slot {
    fn pointer(&mut self) -> *mut c_void {
        match self {
            Self::I32(v) => v as *mut _ as _,
            Self::U32(v) => v as *mut _ as _,
            Self::I64(v) => v as *mut _ as _,
            Self::U64(v) => v as *mut _ as _,
            Self::Handle(v) => v as *mut _ as _,
            Self::Buffer(v) => v.as_mut_ptr() as _,
        }
    }
    fn arg(&self) -> Arg<'_> {
        match self {
            Self::I32(v) => Arg::new(v),
            Self::U32(v) => Arg::new(v),
            Self::I64(v) => Arg::new(v),
            Self::U64(v) => Arg::new(v),
            Self::Handle(v) => Arg::new(v),
            Self::Buffer(_) => unreachable!(),
        }
    }
}
fn native_error(message: &str, code: i64) -> Error {
    Error {
        category: "NativeError".into(),
        message: message.into(),
        code: Some(code),
    }
}
impl NativeTransport {
    pub fn new(definition: Value) -> Self {
        Self {
            definition,
            libraries: HashMap::new(),
            handles: HashMap::new(),
            overrides: HashMap::new(),
            environment: None,
            trace: vec![],
            next_handle: 0,
        }
    }
    fn library(&mut self, name: &str) -> Result<&Library, Error> {
        if !self.libraries.contains_key(name) {
            let mut paths = vec![];
            if let Some(p) = self.overrides.get(name) {
                paths.push(p.clone());
            } else {
                let platform = if cfg!(windows) { "windows" } else { "default" };
                let platforms = &self.definition["libraries"][name]["platforms"];
                let candidates = platforms
                    .get(platform)
                    .or_else(|| platforms.get("default"))
                    .and_then(Value::as_array);
                for c in candidates.into_iter().flatten() {
                    let path = if let Some(p) = c["path"].as_str() {
                        PathBuf::from(p)
                    } else {
                        let key = c["env"].as_str().unwrap();
                        let base = match &self.environment {
                            Some(env) => env.get(key).cloned(),
                            None => std::env::var(key).ok(),
                        }
                        .unwrap_or_else(|| c["default"].as_str().unwrap().into());
                        PathBuf::from(base).join(c["suffix"].as_str().unwrap())
                    };
                    let text = path.to_string_lossy();
                    if text.contains("://")
                        || text.contains('\0')
                        || text.replace('\\', "/").split('/').any(|p| p == "..")
                    {
                        return Err(Error::new("PolicyError", "Invalid library path"));
                    }
                    if (text.contains('/') || text.contains('\\')) && !path.is_absolute() {
                        return Err(Error::new(
                            "PolicyError",
                            "Relative library paths are forbidden",
                        ));
                    }
                    paths.push(path);
                }
            }
            for path in paths {
                // Trusted definitions choose local libraries; loading can execute constructors.
                if let Ok(lib) = unsafe { Library::new(path) } {
                    self.libraries.insert(name.into(), lib);
                    break;
                }
            }
        }
        self.libraries
            .get(name)
            .ok_or_else(|| native_error("Library not found", 12))
    }
}
impl Transport for NativeTransport {
    fn call(&mut self, name: &str, args: &[Value]) -> Result<Value, Error> {
        self.trace.push(json!({"function":name,"args":args}));
        let spec = self.definition["functions"][name].clone();
        let library = spec["library"].as_str().unwrap();
        let symbol = spec["symbol"].as_str().unwrap();
        // Copy code pointer; the Library remains owned by this transport for all calls.
        let code = {
            let lib = self.library(library)?;
            unsafe {
                *lib.get::<unsafe extern "C" fn()>(symbol.as_bytes())
                    .map_err(|_| native_error("Function not found", 13))?
            }
        };
        let declarations = spec["args"].as_array().unwrap();
        let mut slots = vec![];
        let mut types = vec![];
        let mut input = 0;
        for d in declarations {
            let kind = d["type"].as_str().unwrap();
            let out = d["direction"] == "out";
            let v = if out {
                Value::from(0)
            } else {
                let v = args
                    .get(input)
                    .ok_or_else(|| Error::new("TypeError", "Native argument missing"))?
                    .clone();
                input += 1;
                v
            };
            let err = || Error::new("OverflowError", "Native integer out of range");
            let slot = match kind {
                "i32" => Slot::I32(i32::try_from(v.as_i64().ok_or_else(err)?).map_err(|_| err())?),
                "u32" => Slot::U32(u32::try_from(v.as_u64().ok_or_else(err)?).map_err(|_| err())?),
                "i64" => Slot::I64(v.as_i64().ok_or_else(err)?),
                "u64" => Slot::U64(v.as_u64().ok_or_else(err)?),
                "handle" => {
                    let ptr = if out {
                        std::ptr::null_mut()
                    } else {
                        let token = v["$handle"]
                            .as_str()
                            .ok_or_else(|| Error::new("TypeError", "Invalid native handle"))?;
                        let (owner, ptr) = self
                            .handles
                            .get(token)
                            .ok_or_else(|| Error::new("TypeError", "Invalid native handle"))?;
                        if owner != library {
                            return Err(Error::new("TypeError", "Invalid native handle"));
                        }
                        *ptr
                    };
                    Slot::Handle(ptr)
                }
                "buffer" => Slot::Buffer(vec![0; d["size"].as_u64().unwrap() as usize]),
                _ => return Err(Error::new("DefinitionError", "Unsupported native type")),
            };
            types.push(if out {
                Type::pointer()
            } else {
                match kind {
                    "i32" => Type::i32(),
                    "u32" => Type::u32(),
                    "i64" => Type::i64(),
                    "u64" => Type::u64(),
                    "handle" => Type::pointer(),
                    _ => unreachable!(),
                }
            });
            slots.push(slot);
        }
        let pointers: Vec<*mut c_void> = slots.iter_mut().map(Slot::pointer).collect();
        let arguments: Vec<Arg<'_>> = slots
            .iter()
            .enumerate()
            .map(|(i, s)| {
                if declarations[i]["direction"] == "out" {
                    Arg::new(&pointers[i])
                } else {
                    s.arg()
                }
            })
            .collect();
        let return_type = spec["returns"].as_str().unwrap();
        let rt = match return_type {
            "u32" => Type::u32(),
            "i32" => Type::i32(),
            "u64" => Type::u64(),
            "i64" => Type::i64(),
            "void" => Type::void(),
            _ => unreachable!(),
        };
        let cif = Cif::new(types, rt);
        // Safety boundary: validated buffer ownership, argument storage and lifetimes;
        // actual symbol ABI correctness is the trusted definition author's responsibility.
        let result: Option<i64> = unsafe {
            let ptr = CodePtr(code as *mut c_void);
            match return_type {
                "u32" => Some(cif.call::<u32>(ptr, &arguments) as i64),
                "i32" => Some(cif.call::<i32>(ptr, &arguments) as i64),
                "u64" => Some(
                    i64::try_from(cif.call::<u64>(ptr, &arguments))
                        .map_err(|_| Error::new("OverflowError", "Native result out of range"))?,
                ),
                "i64" => Some(cif.call::<i64>(ptr, &arguments)),
                "void" => {
                    cif.call::<()>(ptr, &arguments);
                    None
                }
                _ => unreachable!(),
            }
        };
        if let Some(code) = result {
            if !spec["success"].as_array().unwrap().contains(&json!(code)) {
                return Err(native_error("Native call failed", code));
            }
        }
        let mut output = serde_json::Map::new();
        for (d, slot) in declarations.iter().zip(slots) {
            if d["direction"] != "out" {
                continue;
            }
            let v = match slot {
                Slot::I32(v) => json!(v),
                Slot::U32(v) => json!(v),
                Slot::I64(v) => json!(v),
                Slot::U64(v) => json!(v),
                Slot::Buffer(b) => {
                    let end = b
                        .iter()
                        .position(|x| *x == 0)
                        .ok_or_else(|| native_error("Unterminated native buffer", 0))?;
                    json!(String::from_utf8(b[..end].to_vec()).map_err(|_| Error::new(
                        "UnicodeDecodeError",
                        "Invalid UTF-8 native buffer"
                    ))?)
                }
                Slot::Handle(ptr) => {
                    if ptr.is_null() {
                        return Err(native_error("Null native handle", 0));
                    }
                    self.next_handle += 1;
                    let token = self.next_handle.to_string();
                    self.handles.insert(token.clone(), (library.into(), ptr));
                    json!({"$handle":token})
                }
            };
            output.insert(d["name"].as_str().unwrap().into(), v);
        }
        if spec["invalidates_handles"] == true {
            self.handles.retain(|_, (owner, _)| owner != library);
        }
        Ok(output.into())
    }
}
