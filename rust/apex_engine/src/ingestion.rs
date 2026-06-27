use crate::types::{Address, PoolState};
use std::time::Duration;

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

    pub fn is_empty(&self) -> bool {
        self.pools.is_empty()
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum EndpointKind {
    Http,
    WebSocket,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum EndpointHealthStatus {
    Ready,
    InvalidUrl,
    WrongChain,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct EndpointHealth {
    pub url: String,
    pub kind: EndpointKind,
    pub chain_id: u64,
    pub status: EndpointHealthStatus,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TokioIngestionConfig {
    pub chain_id: u64,
    pub http_endpoints: Vec<String>,
    pub wss_endpoints: Vec<String>,
    pub poll_interval_ms: u64,
}

impl Default for TokioIngestionConfig {
    fn default() -> Self {
        Self {
            chain_id: 137,
            http_endpoints: Vec::new(),
            wss_endpoints: Vec::new(),
            poll_interval_ms: 1_000,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TokioIngestionSnapshot {
    pub chain_id: u64,
    pub http_ready: usize,
    pub wss_ready: usize,
    pub endpoints: Vec<EndpointHealth>,
    pub event_loop_ready: bool,
}

pub struct TokioIngestionSupervisor {
    config: TokioIngestionConfig,
}

impl TokioIngestionSupervisor {
    pub fn new(config: TokioIngestionConfig) -> Self {
        Self { config }
    }

    pub async fn health_snapshot(&self) -> TokioIngestionSnapshot {
        // Yield through Tokio so this path proves it is running on the async
        // runtime. Network IO is intentionally owned by higher-level runtime
        // adapters; this supervisor validates endpoint shape and chain scope
        // without touching funds or blocking the scanner.
        tokio::time::sleep(Duration::from_millis(0)).await;

        let mut endpoints = Vec::new();
        for url in &self.config.http_endpoints {
            endpoints.push(validate_endpoint(url, EndpointKind::Http, self.config.chain_id));
        }
        for url in &self.config.wss_endpoints {
            endpoints.push(validate_endpoint(url, EndpointKind::WebSocket, self.config.chain_id));
        }
        let http_ready = endpoints
            .iter()
            .filter(|endpoint| {
                endpoint.kind == EndpointKind::Http
                    && endpoint.status == EndpointHealthStatus::Ready
            })
            .count();
        let wss_ready = endpoints
            .iter()
            .filter(|endpoint| {
                endpoint.kind == EndpointKind::WebSocket
                    && endpoint.status == EndpointHealthStatus::Ready
            })
            .count();

        TokioIngestionSnapshot {
            chain_id: self.config.chain_id,
            http_ready,
            wss_ready,
            event_loop_ready: self.config.chain_id == 137 && (http_ready + wss_ready) > 0,
            endpoints,
        }
    }

    pub async fn run_forever<F>(&self, mut on_snapshot: F) -> !
    where
        F: FnMut(TokioIngestionSnapshot),
    {
        let mut interval = tokio::time::interval(Duration::from_millis(
            self.config.poll_interval_ms.max(100),
        ));
        loop {
            interval.tick().await;
            on_snapshot(self.health_snapshot().await);
        }
    }
}

fn validate_endpoint(url: &str, kind: EndpointKind, chain_id: u64) -> EndpointHealth {
    let valid_scheme = match kind {
        EndpointKind::Http => url.starts_with("https://") || url.starts_with("http://"),
        EndpointKind::WebSocket => url.starts_with("wss://") || url.starts_with("ws://"),
    };
    let status = if chain_id != 137 {
        EndpointHealthStatus::WrongChain
    } else if !valid_scheme {
        EndpointHealthStatus::InvalidUrl
    } else {
        EndpointHealthStatus::Ready
    };
    EndpointHealth {
        url: url.to_string(),
        kind,
        chain_id,
        status,
    }
}
