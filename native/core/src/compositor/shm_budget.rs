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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::compositor::protocol::{ClientState, ProtocolState};
    use smithay::reexports::wayland_server::Display;
    use std::os::unix::net::UnixStream;

    fn client(display: &Display<ProtocolState>) -> (ClientId, UnixStream) {
        let (server, peer) = UnixStream::pair().unwrap();
        let client = display
            .handle()
            .insert_client(server, Arc::new(ClientState::default()))
            .unwrap();
        (client.id(), peer)
    }

    #[test]
    fn nine_window_pools_fit_but_count_stays_bounded_until_last_reference_drops() {
        let display = Display::<ProtocolState>::new().unwrap();
        let (client, _peer) = client(&display);
        let budget = PoolBudget::default();
        // Nine observed foot pools are about 3MiB each, below the unchanged 64MiB client cap.
        let mut leases = (0..9)
            .map(|_| budget.reserve(client.clone(), 3 * 1024 * 1024).unwrap())
            .collect::<Vec<_>>();
        let retained = leases[0].clone();
        for _ in leases.len()..limits::CLIENT_SHM_POOLS {
            leases.push(budget.reserve(client.clone(), 1).unwrap());
        }
        assert!(budget.reserve(client.clone(), 1).is_err());
        leases.remove(0);
        assert!(budget.reserve(client.clone(), 1).is_err());
        drop(retained);
        leases.push(budget.reserve(client, 1).unwrap());
        drop(leases);
        assert_eq!(budget.usage(), Usage::default());
    }

    #[test]
    fn larger_pool_count_never_expands_client_or_global_byte_allowance() {
        let display = Display::<ProtocolState>::new().unwrap();
        let (first, _a) = client(&display);
        let (second, _b) = client(&display);
        let (third, _c) = client(&display);
        let budget = PoolBudget::default();
        let a = budget
            .reserve(first.clone(), limits::CLIENT_SHM_BYTES)
            .unwrap();
        assert!(budget.reserve(first, 1).is_err());
        assert!(a.grow(limits::CLIENT_SHM_BYTES + 1).is_err());
        let b = budget.reserve(second, limits::CLIENT_SHM_BYTES).unwrap();
        assert_eq!(budget.usage().bytes, limits::TOTAL_SHM_BYTES);
        assert!(budget.reserve(third.clone(), 1).is_err());
        drop(a);
        let c = budget.reserve(third, 1).unwrap();
        assert_eq!(budget.usage().bytes, limits::CLIENT_SHM_BYTES + 1);
        drop((b, c));
        assert_eq!(budget.usage(), Usage::default());
    }
}
