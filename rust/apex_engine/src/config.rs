#[derive(Clone, Debug, Default, PartialEq)]
pub struct EndpointConfig {
    pub titan_rpc: Option<String>,
    pub chainstack_rpc: Option<String>,
    pub alchemy_rpc: Option<String>,
    pub infura_rpc: Option<String>,
    pub fork_rpc: Option<String>,
    pub active_execution_rpc: Option<String>,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct ContractConfig {
    pub c1: String,
    pub c2: String,
    pub owner: String,
}

#[derive(Clone, Debug, PartialEq)]
pub struct RiskConfig {
    pub min_tvl_usd: f64,
    pub max_pool_usage_bps: u16,
    pub absurd_spread_bps: u16,
    pub min_profit_usd: f64,
    pub payload_sim_tolerance_bps: u16,
    pub max_revert_rate: f64,
    pub max_prediction_error: f64,
    pub max_daily_gas_loss_usd: f64,
}

impl Default for RiskConfig {
    fn default() -> Self {
        Self {
            min_tvl_usd: 10_000.0,
            max_pool_usage_bps: 1_000,
            absurd_spread_bps: 2_500,
            min_profit_usd: 0.0,
            payload_sim_tolerance_bps: 50,
            max_revert_rate: 0.01,
            max_prediction_error: 0.05,
            max_daily_gas_loss_usd: 100.0,
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct ApexConfig {
    pub chain_id: u64,
    pub execution_enabled: bool,
    pub broadcast_enabled: bool,
    pub relay_shadow_enabled: bool,
    pub zero_rpc_critical_path: bool,
    pub endpoints: EndpointConfig,
    pub contracts: ContractConfig,
    pub risk: RiskConfig,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ConfigError {
    NonPolygonChain,
    BroadcastEnabled,
    ZeroRpcCriticalPathDisabled,
    ExecutionRpcMissing,
    PublicExecutionRpcRejected,
}

impl ApexConfig {
    pub fn validate_safe_boot(&self) -> Result<(), ConfigError> {
        if self.chain_id != 137 {
            return Err(ConfigError::NonPolygonChain);
        }
        if self.broadcast_enabled {
            return Err(ConfigError::BroadcastEnabled);
        }
        if !self.zero_rpc_critical_path {
            return Err(ConfigError::ZeroRpcCriticalPathDisabled);
        }
        if self.execution_enabled {
            let rpc = self
                .endpoints
                .active_execution_rpc
                .as_deref()
                .ok_or(ConfigError::ExecutionRpcMissing)?;
            if !is_private_paid_execution_rpc(rpc) {
                return Err(ConfigError::PublicExecutionRpcRejected);
            }
        }
        Ok(())
    }
}

pub fn is_private_paid_execution_rpc(url: &str) -> bool {
    let lower = url.to_ascii_lowercase();
    !lower.contains("polygon-rpc.com")
        && (lower.contains("alchemy.com")
            || lower.contains("infura.io")
            || lower.contains("chainstack")
            || lower.contains("titanbuilder")
            || lower.contains("blastapi")
            || lower.contains("quiknode"))
}
