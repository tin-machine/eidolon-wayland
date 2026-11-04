use std::{
    io::{Read, Write},
    os::unix::net::UnixListener,
    time::{Duration, Instant},
};

fn main() -> std::io::Result<()> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args == ["--version"] {
        println!(
            "{}",
            serde_json::json!({"schema":1,"version":"eidolon-probe-bin/1", "uid":unsafe {libc::getuid()},
            "context":std::fs::read_to_string("/proc/self/attr/current").unwrap_or_default().trim_end_matches('\0').trim()})
        );
        return Ok(());
    }
    if args.len() != 2 || args[0] != "--echo" || args[1].len() > 103 {
        return Err(std::io::ErrorKind::InvalidInput.into());
    }
    let listener = UnixListener::bind(&args[1])?;
    listener.set_nonblocking(true)?;
    let deadline = Instant::now() + Duration::from_secs(3);
    let mut stream = loop {
        match listener.accept() {
            Ok((stream, _)) => break stream,
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock && Instant::now() < deadline => {
                std::thread::sleep(Duration::from_millis(5))
            }
            Err(e) => return Err(e),
        }
    };
    stream.set_read_timeout(Some(Duration::from_secs(2)))?;
    stream.set_write_timeout(Some(Duration::from_secs(2)))?;
    loop {
        let mut length = [0; 4];
        stream.read_exact(&mut length)?;
        let size = u32::from_le_bytes(length);
        if size > 4096 {
            return Err(std::io::ErrorKind::InvalidData.into());
        }
        let mut data = vec![0; size as usize];
        stream.read_exact(&mut data)?;
        stream.write_all(&length)?;
        stream.write_all(&data)?;
        if size == 0 {
            break;
        } // 固定の明示終了frame。
    }
    std::fs::remove_file(&args[1])?;
    Ok(())
}
