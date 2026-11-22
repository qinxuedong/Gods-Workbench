use std::{env, fs, path::PathBuf};

fn generated_windows_icon() -> PathBuf {
    const SIZE: u32 = 32;
    const IMAGE_BYTES: u32 = SIZE * SIZE * 4 + SIZE * 4;
    const GOLD: [u8; 4] = [223, 195, 132, 255];
    const DARK: [u8; 4] = [9, 10, 13, 255];
    const ORANGE: [u8; 4] = [251, 90, 21, 255];

    let mut dib = Vec::with_capacity(40 + IMAGE_BYTES as usize);
    dib.extend_from_slice(&40_u32.to_le_bytes());
    dib.extend_from_slice(&(SIZE as i32).to_le_bytes());
    dib.extend_from_slice(&((SIZE * 2) as i32).to_le_bytes());
    dib.extend_from_slice(&1_u16.to_le_bytes());
    dib.extend_from_slice(&32_u16.to_le_bytes());
    dib.extend_from_slice(&0_u32.to_le_bytes());
    dib.extend_from_slice(&IMAGE_BYTES.to_le_bytes());
    dib.extend_from_slice(&0_i32.to_le_bytes());
    dib.extend_from_slice(&0_i32.to_le_bytes());
    dib.extend_from_slice(&0_u32.to_le_bytes());
    dib.extend_from_slice(&0_u32.to_le_bytes());

    for y in (0..SIZE).rev() {
        for x in 0..SIZE {
            let border = x < 2 || x >= 30 || y < 2 || y >= 30;
            let letter_g = ((y >= 7 && y < 10 && x >= 7 && x < 21)
                || (x >= 6 && x < 9 && y >= 8 && y < 24)
                || (y >= 22 && y < 25 && x >= 7 && x < 21)
                || (x >= 18 && x < 21 && y >= 16 && y < 23)
                || (y >= 15 && y < 18 && x >= 13 && x < 21))
                && !(y == 7 && x == 7)
                && !(y == 24 && x == 7);
            let apostrophe = (y >= 7 && y <= 8 && x >= 25 && x <= 26)
                || (y >= 9 && y <= 10 && x >= 24 && x <= 25)
                || (y >= 11 && y <= 12 && x >= 23 && x <= 24);
            let [red, green, blue, alpha] = if apostrophe {
                ORANGE
            } else if border || letter_g {
                GOLD
            } else {
                DARK
            };
            dib.extend_from_slice(&[blue, green, red, alpha]);
        }
    }
    dib.resize(dib.len() + (SIZE * 4) as usize, 0);

    let mut icon = Vec::with_capacity(22 + dib.len());
    icon.extend_from_slice(&0_u16.to_le_bytes());
    icon.extend_from_slice(&1_u16.to_le_bytes());
    icon.extend_from_slice(&1_u16.to_le_bytes());
    icon.extend_from_slice(&[SIZE as u8, SIZE as u8, 0, 0]);
    icon.extend_from_slice(&1_u16.to_le_bytes());
    icon.extend_from_slice(&32_u16.to_le_bytes());
    icon.extend_from_slice(&(dib.len() as u32).to_le_bytes());
    icon.extend_from_slice(&22_u32.to_le_bytes());
    icon.extend_from_slice(&dib);

    let path = PathBuf::from(env::var_os("OUT_DIR").expect("OUT_DIR is set by Cargo"))
        .join("workbench.ico");
    fs::write(&path, icon).expect("write generated Windows icon");
    path
}

fn main() {
    let attributes = tauri_build::Attributes::new()
        .app_manifest(tauri_build::AppManifest::new().commands(&[
            "discover_lan",
            "last_server",
            "connect_to_server",
            "launch_local_backend",
            "get_diagnostics",
            "pick_client_folder",
            "pick_client_upload_files",
            "read_client_file",
            "get_client_setting",
            "set_client_setting",
        ]))
        .windows_attributes(
            tauri_build::WindowsAttributes::new().window_icon_path(generated_windows_icon()),
        );
    tauri_build::try_build(attributes).expect("failed to run tauri-build");
}
