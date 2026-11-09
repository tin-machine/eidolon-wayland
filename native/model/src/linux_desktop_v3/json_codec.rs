//! Duplicate keys are rejected before constructing Value. No floats, BOM,
//! nonfinite numbers, lone surrogates or values outside signed i64.
use super::{Error, Result, require};
use serde::{Deserialize, de};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, fmt, fmt::Write};

struct Strict(Value);
impl<'de> Deserialize<'de> for Strict {
    fn deserialize<D: de::Deserializer<'de>>(d: D) -> std::result::Result<Self, D::Error> {
        struct Visitor;
        impl<'de> de::Visitor<'de> for Visitor {
            type Value = Strict;
            fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.write_str("bounded strict JSON")
            }
            fn visit_bool<E: de::Error>(self, v: bool) -> std::result::Result<Strict, E> {
                Ok(Strict(Value::Bool(v)))
            }
            fn visit_i64<E: de::Error>(self, v: i64) -> std::result::Result<Strict, E> {
                Ok(Strict(v.into()))
            }
            fn visit_u64<E: de::Error>(self, v: u64) -> std::result::Result<Strict, E> {
                i64::try_from(v)
                    .map(|n| Strict(n.into()))
                    .map_err(|_| E::custom("CONTRACT_INVALID"))
            }
            fn visit_f64<E: de::Error>(self, _: f64) -> std::result::Result<Strict, E> {
                Err(E::custom("CONTRACT_INVALID"))
            }
            fn visit_str<E: de::Error>(self, v: &str) -> std::result::Result<Strict, E> {
                Ok(Strict(v.into()))
            }
            fn visit_string<E: de::Error>(self, v: String) -> std::result::Result<Strict, E> {
                Ok(Strict(v.into()))
            }
            fn visit_unit<E: de::Error>(self) -> std::result::Result<Strict, E> {
                Ok(Strict(Value::Null))
            }
            fn visit_none<E: de::Error>(self) -> std::result::Result<Strict, E> {
                Ok(Strict(Value::Null))
            }
            fn visit_seq<A: de::SeqAccess<'de>>(
                self,
                mut seq: A,
            ) -> std::result::Result<Strict, A::Error> {
                let mut items = vec![];
                while let Some(Strict(v)) = seq.next_element()? {
                    items.push(v);
                }
                Ok(Strict(Value::Array(items)))
            }
            fn visit_map<A: de::MapAccess<'de>>(
                self,
                mut map: A,
            ) -> std::result::Result<Strict, A::Error> {
                let mut items = serde_json::Map::new();
                while let Some(key) = map.next_key::<String>()? {
                    if items.contains_key(&key) {
                        return Err(de::Error::custom("CONTRACT_INVALID"));
                    }
                    let Strict(value) = map.next_value()?;
                    items.insert(key, value);
                }
                Ok(Strict(Value::Object(items)))
            }
        }
        d.deserialize_any(Visitor)
    }
}
pub fn parse_json(raw: &[u8], limit: usize) -> Result<Value> {
    require(!raw.is_empty() && raw.len() <= limit, "CONTRACT_INVALID")?;
    let Strict(v) = serde_json::from_slice(raw).map_err(|_| Error("CONTRACT_INVALID"))?;
    canonical_json(&v)?;
    Ok(v)
}

fn string(v: &str, out: &mut String) {
    out.push('"');
    for c in v.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\u{8}' => out.push_str("\\b"),
            '\t' => out.push_str("\\t"),
            '\n' => out.push_str("\\n"),
            '\u{c}' => out.push_str("\\f"),
            '\r' => out.push_str("\\r"),
            c if !(' '..='~').contains(&c) => {
                let mut units = [0; 2];
                for unit in c.encode_utf16(&mut units) {
                    write!(out, "\\u{unit:04x}").expect("string write");
                }
            }
            c => out.push(c),
        }
    }
    out.push('"');
}
fn encode(v: &Value, out: &mut String, depth: usize) -> Result<()> {
    require(depth <= 32, "CONTRACT_INVALID")?;
    match v {
        Value::Null => out.push_str("null"),
        Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        Value::Number(n) => out.push_str(&n.as_i64().ok_or(Error("CONTRACT_INVALID"))?.to_string()),
        Value::String(s) => string(s, out),
        Value::Array(a) => {
            out.push('[');
            for (i, item) in a.iter().enumerate() {
                if i != 0 {
                    out.push(',');
                }
                encode(item, out, depth + 1)?;
            }
            out.push(']');
        }
        Value::Object(m) => {
            out.push('{');
            for (i, (key, value)) in m.iter().collect::<BTreeMap<_, _>>().iter().enumerate() {
                require(depth < 32, "CONTRACT_INVALID")?;
                if i != 0 {
                    out.push(',');
                }
                string(key, out);
                out.push(':');
                encode(value, out, depth + 1)?;
            }
            out.push('}');
        }
    }
    Ok(())
}
pub fn canonical_json(v: &Value) -> Result<Vec<u8>> {
    let mut out = String::new();
    encode(v, &mut out, 0)?;
    Ok(out.into_bytes())
}
pub(super) fn hash_bytes(v: &[u8]) -> String {
    Sha256::digest(v)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}
pub fn digest(v: &Value) -> Result<String> {
    Ok(hash_bytes(&canonical_json(v)?))
}
