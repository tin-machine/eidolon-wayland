//! NX10: Termux所有の設定を照会する構造化契約。argvをSSH commandや診断へ埋め込まない。
use eidolon_model::ids::CommandSeq;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Mode {
    Terminal,
    Gui,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Definition {
    pub id: String,
    pub label: String,
    pub mode: Mode,
    pub executable: String,
    #[serde(default)]
    pub args: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cwd: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub interpreter: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub icon: Option<String>,
}

fn text(value: &str, max: usize) -> bool {
    !value.is_empty() && value.len() <= max && !value.chars().any(|c| c.is_control())
}
fn id(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 48
        && value.as_bytes()[0].is_ascii_alphanumeric()
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"_.-".contains(&b))
}
fn hash(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
impl Definition {
    pub fn valid(&self) -> bool {
        id(&self.id)
            && text(&self.label, 160)
            && text(&self.executable, 1024)
            && [&self.cwd, &self.interpreter, &self.icon]
                .iter()
                .all(|field| field.as_ref().is_none_or(|v| text(v, 1024)))
            && self.args.len() <= 64
            && self
                .args
                .iter()
                .all(|v| v.len() <= 2048 && !v.contains('\0'))
            && serde_json::to_vec(self).is_ok_and(|v| v.len() <= 8192)
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Operation {
    Reload { path: String },
    Page { revision: String, offset: usize },
    Inspect { definition: Definition },
}
impl Operation {
    pub fn valid(&self) -> bool {
        match self {
            Self::Reload { path } => text(path, 1024),
            Self::Page { revision, offset } => {
                hash(revision) && *offset <= 64 && offset.is_multiple_of(8)
            }
            Self::Inspect { definition } => definition.valid(),
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum Error {
    CatalogInvalid,
    CatalogLimit,
    CatalogSchema,
    CatalogDuplicateId,
    CatalogFileUnsafe,
    CatalogUnavailable,
    CatalogChanged,
    PathMissing,
    PathUnavailable,
    ExecutableNotFile,
    ExecutableDenied,
    CwdUnavailable,
    InterpreterUnavailable,
    GuiNotVerified,
    AppNotFound,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Entry {
    pub id: String,
    pub label: String,
    pub mode: Mode,
    pub icon: String,
    pub available: bool,
    pub error: Option<Error>,
}
impl Entry {
    fn valid(&self) -> bool {
        id(&self.id)
            && text(&self.label, 160)
            && matches!(
                self.icon.as_str(),
                "builtin:terminal" | "builtin:editor" | "builtin:script" | "builtin:game"
            )
            && self.available == self.error.is_none()
            && (self.mode != Mode::Gui || !self.available)
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Reply {
    pub schema: u32,
    pub nonce: String,
    pub uid: u32,
    pub request_id: CommandSeq,
    pub revision: String,
    pub builtin: bool,
    pub total: usize,
    pub offset: usize,
    pub entries: Vec<Entry>,
    pub inspected: Option<Entry>,
    pub error: Option<Error>,
}
impl Reply {
    pub(crate) fn valid(&self) -> bool {
        self.schema == 1
            && hash(&self.revision)
            && self.total <= 64
            && self.offset <= self.total
            && self.offset.is_multiple_of(8)
            && self.entries.len() <= 8
            && self.entries.iter().all(Entry::valid)
            && self
                .entries
                .iter()
                .enumerate()
                .all(|(i, a)| self.entries[..i].iter().all(|b| b.id != a.id))
            && match &self.inspected {
                Some(entry) => entry.valid() && self.entries.is_empty() && self.error.is_none(),
                None => self.entries.len() == (self.total - self.offset).min(8),
            }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn definition_keeps_arguments_structured_and_rejects_unbounded_input() {
        let mut definition: Definition = serde_json::from_value(serde_json::json!({
            "id":"script", "label":"日本語 script", "mode":"terminal", "executable":"scripts/a b.sh",
            "args":["", "$(touch /never)", "a;b", "日本語", "line\nline"]
        })).unwrap();
        assert!(definition.valid());
        assert_eq!(
            serde_json::to_value(&definition).unwrap()["args"][1],
            "$(touch /never)"
        );
        definition.args.push("\0".into());
        assert!(!definition.valid());
        assert!(
            serde_json::from_value::<Definition>(serde_json::json!({
                "id":"s", "label":"s", "mode":"terminal", "executable":"x", "shell":"bash -c x"
            }))
            .is_err()
        );
    }
}
