use crate::types::{Address, PoolState};

#[derive(Clone, Debug, Default)]
pub struct LocalPoolCache {
    pools: std::collections::HashMap<Address, PoolState>,
}

impl LocalPoolCache {
    pub fn upsert(&mut self, pool: PoolState) {
        self.pools.insert(pool.address, pool);
    }

    pub fn get(&self, address: &Address) -> Option<&PoolState> {
        self.pools.get(address)
    }

    pub fn len(&self) -> usize {
        self.pools.len()
    }
}
