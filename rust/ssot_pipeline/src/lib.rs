// rust/ssot_pipeline/src/lib.rs
//
// Dual Punch SSOT pipeline — Phase 1.
//
// Standalone Rust crate implementing the locked 2-swap constant-product
// arbitrage pipeline: math, audit, degradation simulation, batch simulation,
// pipeline finalizer, and the executor boundary.

pub mod audit;
pub mod batch;
pub mod degradation;
pub mod executor_boundary;
pub mod finalizer;
pub mod math_core;
pub mod types;
