use apex_engine::{
    EndpointHealthStatus, TokioIngestionConfig, TokioIngestionSupervisor,
};

#[tokio::test]
async fn tokio_ingestion_health_accepts_polygon_http_and_wss() {
    let supervisor = TokioIngestionSupervisor::new(TokioIngestionConfig {
        chain_id: 137,
        http_endpoints: vec!["https://polygon.drpc.org".to_string()],
        wss_endpoints: vec!["wss://polygon-mainnet.g.alchemy.com/v2/key".to_string()],
        poll_interval_ms: 100,
    });

    let snapshot = supervisor.health_snapshot().await;

    assert!(snapshot.event_loop_ready);
    assert_eq!(snapshot.http_ready, 1);
    assert_eq!(snapshot.wss_ready, 1);
    assert!(snapshot
        .endpoints
        .iter()
        .all(|endpoint| endpoint.status == EndpointHealthStatus::Ready));
}

#[tokio::test]
async fn tokio_ingestion_health_fails_closed_on_wrong_chain() {
    let supervisor = TokioIngestionSupervisor::new(TokioIngestionConfig {
        chain_id: 1,
        http_endpoints: vec!["https://polygon.drpc.org".to_string()],
        wss_endpoints: vec!["wss://polygon-mainnet.g.alchemy.com/v2/key".to_string()],
        poll_interval_ms: 100,
    });

    let snapshot = supervisor.health_snapshot().await;

    assert!(!snapshot.event_loop_ready);
    assert!(snapshot
        .endpoints
        .iter()
        .all(|endpoint| endpoint.status == EndpointHealthStatus::WrongChain));
}

#[tokio::test]
async fn tokio_ingestion_health_rejects_mismatched_url_schemes() {
    let supervisor = TokioIngestionSupervisor::new(TokioIngestionConfig {
        chain_id: 137,
        http_endpoints: vec!["wss://not-http".to_string()],
        wss_endpoints: vec!["https://not-wss".to_string()],
        poll_interval_ms: 100,
    });

    let snapshot = supervisor.health_snapshot().await;

    assert!(!snapshot.event_loop_ready);
    assert_eq!(snapshot.http_ready, 0);
    assert_eq!(snapshot.wss_ready, 0);
    assert!(snapshot
        .endpoints
        .iter()
        .all(|endpoint| endpoint.status == EndpointHealthStatus::InvalidUrl));
}
