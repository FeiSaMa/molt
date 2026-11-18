use molt_kernel::{manifest, state::State};
use serde_json::json;
use std::{
    fs,
    path::PathBuf,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

fn elapsed_ms(start: Instant) -> u128 {
    start.elapsed().as_millis()
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let records = std::env::args()
        .nth(1)
        .map(|value| value.parse::<usize>())
        .transpose()?
        .unwrap_or(10_000);
    let object_bytes = std::env::args()
        .nth(2)
        .map(|value| value.parse::<usize>())
        .transpose()?
        .unwrap_or(1024 * 1024 * 1024);
    let stamp = SystemTime::now().duration_since(UNIX_EPOCH)?.as_nanos();
    let root: PathBuf = std::env::temp_dir().join(format!("molt-kernel-bench-{stamp}"));
    fs::create_dir(&root)?;
    let result = (|| -> Result<_, Box<dyn std::error::Error>> {
        let state = State::new(&root);
        state.init()?;
        let payload = vec![0x5a; object_bytes];
        let started = Instant::now();
        let object_hash = state.object_put(&payload)?;
        let object_write_ms = elapsed_ms(started);
        let started = Instant::now();
        let read_back = molt_kernel::cas::Cas::new(&root).get(&object_hash)?;
        let object_read_ms = elapsed_ms(started);
        assert_eq!(read_back.len(), object_bytes);

        let empty_manifest = manifest::create(json!({}), &molt_kernel::cas::Cas::new(&root))?;
        let started = Instant::now();
        for index in 0..records {
            state.record_append(
                "bench.record",
                "2026-11-16T00:00:00Z",
                &empty_manifest,
                json!({"index":index}),
            )?;
        }
        let record_elapsed = started.elapsed();
        let records_per_second = if record_elapsed.is_zero() {
            0.0
        } else {
            records as f64 / record_elapsed.as_secs_f64()
        };
        Ok(json!({
            "records":records,
            "record_append_ms":record_elapsed.as_millis(),
            "records_per_second":records_per_second,
            "object_bytes":object_bytes,
            "object_write_ms":object_write_ms,
            "object_read_ms":object_read_ms,
            "object_write_mib_per_second":object_bytes as f64 / (1024.0 * 1024.0) / (object_write_ms.max(1) as f64 / 1000.0),
            "object_read_mib_per_second":object_bytes as f64 / (1024.0 * 1024.0) / (object_read_ms.max(1) as f64 / 1000.0),
            "elapsed_ms":Duration::from_millis(object_write_ms as u64 + object_read_ms as u64 + record_elapsed.as_millis() as u64).as_millis(),
        }))
    })();
    let cleanup = fs::remove_dir_all(&root);
    let output = result?;
    cleanup?;
    println!("{output}");
    Ok(())
}
