//! leaseをSmithay InnerPoolへ預け、遅延unmap/close後まで実mappingを計上する。
use eidolon_model::limits;
use smithay::reexports::wayland_server::backend::ClientId;
use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
};

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Usage {
    pub bytes: usize,
    pub pools: usize,
}
#[derive(Default)]
struct Book {
    total: Usage,
    clients: HashMap<ClientId, Usage>,
}
#[derive(Clone, Default)]
pub struct PoolBudget(Arc<Mutex<Book>>);

impl PoolBudget {
    pub fn usage(&self) -> Usage {
        self.0.lock().unwrap().total
    }

    pub fn reserve(&self, client: ClientId, bytes: usize) -> Result<Arc<PoolLease>, ()> {
        let mut book = self.0.lock().unwrap();
        let used = book.clients.get(&client).copied().unwrap_or_default();
        if bytes == 0
            || used.pools >= limits::CLIENT_SHM_POOLS
            || bytes > limits::CLIENT_SHM_BYTES.saturating_sub(used.bytes)
            || bytes > limits::TOTAL_SHM_BYTES.saturating_sub(book.total.bytes)
        {
            return Err(());
        }
        book.total.bytes += bytes;
        book.total.pools += 1;
        book.clients.insert(
            client.clone(),
            Usage {
                bytes: used.bytes + bytes,
                pools: used.pools + 1,
            },
        );
        Ok(Arc::new(PoolLease {
            budget: self.clone(),
            client,
            bytes: Mutex::new(bytes),
        }))
    }
}

pub struct PoolLease {
    budget: PoolBudget,
    client: ClientId,
    bytes: Mutex<usize>,
}
impl PoolLease {
    /// resize失敗時はclient切断・実unmapまで多めに計上する。成功とみなして戻さない。
    pub fn grow(&self, next: usize) -> Result<(), ()> {
        let mut bytes = self.bytes.lock().unwrap();
        let additional = next.checked_sub(*bytes).ok_or(())?;
        let mut book = self.budget.0.lock().unwrap();
        let used = book.clients.get(&self.client).ok_or(())?;
        if additional > limits::CLIENT_SHM_BYTES.saturating_sub(used.bytes)
            || additional > limits::TOTAL_SHM_BYTES.saturating_sub(book.total.bytes)
        {
            return Err(());
        }
        book.total.bytes += additional;
        book.clients.get_mut(&self.client).unwrap().bytes += additional;
        *bytes = next;
        Ok(())
    }
}
impl Drop for PoolLease {
    fn drop(&mut self) {
        let bytes = *self.bytes.get_mut().unwrap();
        let mut book = self.budget.0.lock().unwrap();
        book.total.bytes -= bytes;
        book.total.pools -= 1;
        let used = book.clients.get_mut(&self.client).unwrap();
        used.bytes -= bytes;
        used.pools -= 1;
        if used.pools == 0 {
            book.clients.remove(&self.client);
        }
    }
}
