use std::io::{Read, Write};

fn main() -> std::io::Result<()> {
    let mut args = std::env::args().skip(1);
    match args.next().as_deref() {
        Some("--version") if args.next().is_none() => {
            println!("eidolon-helper-probe/1 uid={}", unsafe { libc::getuid() });
        }
        Some("--echo") => {
            let path = args.next().ok_or(std::io::ErrorKind::InvalidInput)?;
            if args.next().is_some() || path.len() > 103 {
                return Err(std::io::ErrorKind::InvalidInput.into());
            }
            let listener = std::os::unix::net::UnixListener::bind(&path)?;
            let (mut stream, _) = listener.accept()?;
            stream.set_read_timeout(Some(std::time::Duration::from_secs(3)))?;
            stream.set_write_timeout(Some(std::time::Duration::from_secs(3)))?;
            let mut input = [0u8; 4096];
            let size = stream.read(&mut input)?;
            stream.write_all(&input[..size])?;
            std::fs::remove_file(path)?;
        }
        Some("--ssh-client") => {
            let path = args.next().ok_or(std::io::ErrorKind::InvalidInput)?;
            if args.next().is_some() || path.len() > 103 {
                return Err(std::io::ErrorKind::InvalidInput.into());
            }
            for channel in 0..4u8 {
                let mut stream = std::os::unix::net::UnixStream::connect(&path)?;
                stream.set_read_timeout(Some(std::time::Duration::from_secs(10)))?;
                stream.set_write_timeout(Some(std::time::Duration::from_secs(10)))?;
                let payload: Vec<u8> = (0..65536)
                    .map(|i| (i as u8).wrapping_add(channel))
                    .collect();
                let mut reply = vec![0; payload.len()];
                for _ in 0..128 {
                    stream.write_all(&payload)?;
                    stream.read_exact(&mut reply)?;
                    if reply != payload {
                        return Err(std::io::ErrorKind::InvalidData.into());
                    }
                }
            }
            println!("echo-pass channels=4 bytes=33554432");
        }
        _ => return Err(std::io::ErrorKind::InvalidInput.into()),
    }
    Ok(())
}
