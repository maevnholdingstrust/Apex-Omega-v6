use apex_engine::{ApexConfig, ConfigError, ContractConfig, EndpointConfig, RiskConfig};

fn base_config() -> ApexConfig {
    ApexConfig {
        chain_id: 137,
        execution_enabled: false,
        broadcast_enabled: false,
        relay_shadow_enabled: true,
        zero_rpc_critical_path: true,
        endpoints: EndpointConfig {
            active_execution_rpc: Some("https://polygon-mainnet.g.alchemy.com/v2/key".to_string()),
            ..EndpointConfig::default()
        },
        contracts: ContractConfig::default(),
        risk: RiskConfig::default(),
    }
}

#[test]
fn chain_id_137_passes_safe_boot() {
    assert_eq!(base_config().validate_safe_boot(), Ok(()));
}

#[test]
fn non_137_fails_safe_boot() {
    let mut cfg = base_config();
    cfg.chain_id = 1;
    assert_eq!(cfg.validate_safe_boot(), Err(ConfigError::NonPolygonChain));
}

#[test]
fn broadcast_fails_safe_boot() {
    let mut cfg = base_config();
    cfg.broadcast_enabled = true;
    assert_eq!(cfg.validate_safe_boot(), Err(ConfigError::BroadcastEnabled));
}

#[test]
fn zero_rpc_disabled_fails_safe_boot() {
    let mut cfg = base_config();
    cfg.zero_rpc_critical_path = false;
    assert_eq!(
        cfg.validate_safe_boot(),
        Err(ConfigError::ZeroRpcCriticalPathDisabled)
    );
}

#[test]
fn public_execution_rpc_fails_when_execution_enabled() {
    let mut cfg = base_config();
    cfg.execution_enabled = true;
    cfg.endpoints.active_execution_rpc = Some("https://polygon-rpc.com".to_string());
    assert_eq!(
        cfg.validate_safe_boot(),
        Err(ConfigError::PublicExecutionRpcRejected)
    );
}
