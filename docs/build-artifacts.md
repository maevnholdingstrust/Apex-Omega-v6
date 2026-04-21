# Rust / Cargo Build Artifacts

This document explains the Rust build output directory, what lives inside it,
why it should not be committed to version control, and what developers need to
know to work with the Rust extension in this repository.

---

## The `target/` directory

When you run `cargo build`, `cargo test`, or `maturin develop` (used here to
compile the Python extension), Cargo writes all generated output to the
`target/` directory at the repository root.  This includes:

| Path | What it contains |
|---|---|
| `target/debug/` | Debug-mode compiled libraries, binaries, and incremental artifacts |
| `target/release/` | Release-mode equivalents |
| `target/wheels/` | Python wheel packages produced by `maturin build` |
| `target/.rustc_info.json` | Compiler-fingerprint file (see below) |
| `target/CACHEDIR.TAG` | Standard cache-directory marker (see [Cache Directory Tagging Spec](https://bford.info/cachedir/)) |
| `target/debug/.fingerprint/` | Per-crate rebuild-decision metadata |

The entire `target/` directory is listed in `.gitignore` and **must never be
committed**.  It is regenerated automatically on every build and its contents
are specific to the local machine, OS, and compiler version.

---

## `target/.rustc_info.json`

### What is it?

`target/.rustc_info.json` is a small JSON file written by Cargo the first time
(and after each toolchain upgrade) it invokes `rustc`.  It records a
**fingerprint of the Rust compiler** so that Cargo can detect when the
toolchain has changed and trigger a full rebuild of all crates.

A typical file looks like:

```json
{
  "rustc_fingerprint": 10800193640284659637,
  "outputs": {
    "7971740275564407648": {
      "success": true,
      "status": "",
      "code": 0,
      "stdout": "___\nlib___.rlib\n...\ntarget_arch=\"x86_64\"\n...",
      "stderr": ""
    },
    "17747080675513052775": {
      "success": true,
      "status": "",
      "code": 0,
      "stdout": "rustc 1.94.1 (e408947bf 2026-03-25)\nbinary: rustc\n...",
      "stderr": ""
    }
  },
  "successes": {}
}
```

Key fields:

| Field | Meaning |
|---|---|
| `rustc_fingerprint` | A hash that uniquely identifies this `rustc` binary on this host |
| `outputs` | Cached results of `rustc` invocations used for environment probing (e.g. target cfg flags, version string) |
| `successes` | Additional cached success flags (usually empty) |

### How is it generated?

Cargo generates this file automatically.  You do not need to create or edit it
manually.  It is recreated whenever:

- `cargo build` / `cargo test` / `maturin develop` is run for the first time in
  a fresh clone, OR
- The installed Rust toolchain changes (e.g. `rustup update`).

### Should it be committed?

**No.**  Like all files under `target/`, it is machine-specific build output.
Committing it would:

- Create spurious diffs every time the toolchain is updated or the build is run
  on a different machine.
- Potentially expose information about the build environment (OS, architecture,
  exact compiler hash) publicly.
- Confuse incremental-build logic for other developers.

### What should developers do locally?

Nothing special.  The file is covered by the `target/` entry in `.gitignore`
and Cargo will create it automatically when you build for the first time.

```bash
# Build the Rust extension (first time or after toolchain update)
maturin develop          # development wheel in-place
# or
cargo build              # raw Rust build without Python packaging
```

---

## CI / Build instructions

Continuous-integration workflows in `.github/workflows/` do **not** cache or
restore `target/`.  Each CI run starts with a clean compile.  This is
intentional: it avoids stale artifacts from a previous run silently masking
build failures.

If build times become a concern the CI workflows can be updated to use
[`actions/cache`](https://github.com/actions/cache) with a cache key derived
from `Cargo.lock` and the OS/toolchain, for example:

```yaml
- uses: actions/cache@v4
  with:
    path: target/
    key: ${{ runner.os }}-cargo-${{ hashFiles('Cargo.lock') }}
    restore-keys: |
      ${{ runner.os }}-cargo-
```

This caching is opt-in and intentionally absent from the current workflow
definitions.

---

## See also

- [The Cargo Book — Build cache](https://doc.rust-lang.org/cargo/guide/build-cache.html)
- [The Cargo Book — Build scripts](https://doc.rust-lang.org/cargo/reference/build-scripts.html)
- [maturin documentation](https://www.maturin.rs/)
