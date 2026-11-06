// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::{Arc, Mutex};
use tauri::{Emitter, Manager};
use tauri_plugin_shell::ShellExt;
use tauri_plugin_shell::process::{CommandChild, CommandEvent};

struct AppState {
    backend_child: Arc<Mutex<Option<CommandChild>>>,
    backend_port: Arc<Mutex<u16>>,
}

#[tauri::command]
fn get_backend_port(state: tauri::State<AppState>) -> u16 {
    *state.backend_port.lock().unwrap()
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .manage(AppState {
            backend_child: Arc::new(Mutex::new(None)),
            backend_port: Arc::new(Mutex::new(0)),
        })
        .setup(|app| {
            let state = app.state::<AppState>();
            let child_arc = state.backend_child.clone();
            let port_arc = state.backend_port.clone();
            let app_handle = app.handle().clone();

            // 传入当前 Tauri 宿主进程 PID，供 Python Sidecar 做孤儿进程自毁监听
            let current_pid = std::process::id().to_string();
            let sidecar_cmd = app.shell()
                .sidecar("geminihub-backend")
                .unwrap()
                .env("TAURI_PARENT_PID", current_pid);

            let (mut rx, child) = sidecar_cmd.spawn().expect("启动 Python Sidecar 失败");
            *child_arc.lock().unwrap() = Some(child);

            // 异步监听 Python 标准输出，捕获动态端口号并通知前端
            tauri::async_runtime::spawn(async move {
                while let Some(event) = rx.recv().await {
                    if let CommandEvent::Stdout(line_bytes) = event {
                        let line = String::from_utf8_lossy(&line_bytes);
                        if let Some(port_str) = line.strip_prefix("GEMINI_HUB_PORT=") {
                            if let Ok(port) = port_str.trim().parse::<u16>() {
                                *port_arc.lock().unwrap() = port;
                                let _ = app_handle.emit("backend-ready", port);
                            }
                        }
                    }
                }
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                // 窗口彻底销毁时清理 Sidecar 守护进程
                let state = window.state::<AppState>();
                if let Some(child) = state.backend_child.lock().unwrap().take() {
                    let _ = child.kill();
                }
            }
        })
        .invoke_handler(tauri::generate_handler![get_backend_port])
        .run(tauri::generate_context!())
        .expect("运行 Tauri 桌面容器失败");
}

