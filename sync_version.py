"""
全局版本号一键同步工具

以仓库根目录 version.json 作为唯一版本号配置源（Single Source of Truth），
自动同步后端容器兜底配置、frontend/package.json、frontend/package-lock.json、
src-tauri/Cargo.toml 与 src-tauri/tauri.conf.json 等生态静态清单。

用法:
  python sync_version.py            # 按根目录 version.json 同步所有静态清单
  python sync_version.py 1.1.10     # 先更新根目录 version.json，再全仓同步
"""
import json
import re
import sys
from pathlib import Path

# 强制英文 Windows 控制台等非 UTF-8 环境安全输出中文日志
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass

BASE_DIR = Path(__file__).resolve().parent
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


def load_version_config(base_dir: Path | None = None) -> dict[str, str]:
    """读取并校验根目录 version.json 配置。"""
    root_dir = base_dir or BASE_DIR
    version_file = root_dir / "version.json"
    if not version_file.is_file():
        raise FileNotFoundError(f"未找到全局版本配置文件: {version_file}")

    raw = json.loads(version_file.read_text(encoding="utf-8"))
    version = str(raw.get("version", "")).strip().lstrip("vV")
    edition = str(raw.get("edition", "")).strip() or "Strata Edition"
    if not SEMVER_RE.match(version):
        raise ValueError(f"无效的语义化版本号: {version!r}")

    return {"version": version, "edition": edition}


def _read_raw_text(path: Path) -> str | None:
    """保留原始换行符读取文本（不把 LF 转成 CRLF 或反之）。"""
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8", newline="") as fh:
        return fh.read()


def _write_if_changed(path: Path, new_text: str) -> bool:
    """仅当文件内容发生变化时落盘，保持原始换行符，避免出现全文件换行符 diff。"""
    old_text = _read_raw_text(path)
    # 若原文件为 CRLF（例如 Windows Git Runner 自动检出结果），保持原换行符避免误判 diff
    if old_text is not None and "\r\n" in old_text and "\r\n" not in new_text:
        new_text = new_text.replace("\n", "\r\n")
    if old_text == new_text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(new_text)
    return True


def sync_all_manifests(
    base_dir: Path | None = None,
    new_version: str | None = None,
    new_edition: str | None = None,
) -> list[str]:
    """将 version.json 同步至所有需要静态字段的工具清单，返回发生变更的相对路径列表。"""
    root_dir = base_dir or BASE_DIR
    version_file = root_dir / "version.json"
    changed_files: list[str] = []

    if new_version is not None:
        clean_ver = new_version.strip().lstrip("vV")
        if not SEMVER_RE.match(clean_ver):
            raise ValueError(f"无效的目标版本号: {new_version!r}（预期形如 1.1.10）")
        current = load_version_config(root_dir) if version_file.is_file() else {"edition": "Strata Edition"}
        payload = {
            "version": clean_ver,
            "edition": (new_edition or current.get("edition") or "Strata Edition").strip(),
        }
        if _write_if_changed(version_file, json.dumps(payload, indent=2, ensure_ascii=False) + "\n"):
            changed_files.append("version.json")

    cfg = load_version_config(root_dir)
    version = cfg["version"]

    # 1. 同步后端容器 / 冻结打包兜底配置 backend/app/core/version.json
    backend_version_file = root_dir / "backend" / "app" / "core" / "version.json"
    if _write_if_changed(backend_version_file, json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"):
        changed_files.append("backend/app/core/version.json")

    # 2. 同步 frontend/package.json（仅替换根 version 字段，保持原有缩进与换行风格）
    pkg_json_path = root_dir / "frontend" / "package.json"
    pkg_text = _read_raw_text(pkg_json_path)
    if pkg_text is not None:
        new_pkg_text = re.sub(
            r'("name"\s*:\s*"[^"]+"\s*,\s*\r?\n\s*"version"\s*:\s*")[^"]+(")',
            rf"\g<1>{version}\g<2>",
            pkg_text,
            count=1,
        )
        if _write_if_changed(pkg_json_path, new_pkg_text):
            changed_files.append("frontend/package.json")

    # 3. 同步 frontend/package-lock.json（严格仅修改头部根项目与 packages[""] 两处，绝不触及三方依赖）
    lock_json_path = root_dir / "frontend" / "package-lock.json"
    lock_text = _read_raw_text(lock_json_path)
    if lock_text is not None:
        head_match = re.match(r'^([\s\S]{0,400}?"packages"\s*:\s*\{\s*\r?\n\s*""\s*:\s*\{[\s\S]{0,200}?"version"\s*:\s*")[^"]+(")', lock_text)
        if head_match:
            head_slice = lock_text[: head_match.end()]
            rest_slice = lock_text[head_match.end() :]
            updated_head = re.sub(
                r'("name"\s*:\s*"agenthub-local-frontend"\s*,\s*\r?\n\s*"version"\s*:\s*")[^"]+(")',
                rf"\g<1>{version}\g<2>",
                head_slice,
                count=2,
            )
            if _write_if_changed(lock_json_path, updated_head + rest_slice):
                changed_files.append("frontend/package-lock.json")

    # 4. 同步 src-tauri/Cargo.toml（仅替换 [package] 下首个 version）
    cargo_path = root_dir / "src-tauri" / "Cargo.toml"
    cargo_text = _read_raw_text(cargo_path)
    if cargo_text is not None:
        new_cargo_text = re.sub(
            r'(\[package\][\s\S]*?^\s*version\s*=\s*")[^"]+(")',
            rf"\g<1>{version}\g<2>",
            cargo_text,
            count=1,
            flags=re.MULTILINE,
        )
        if _write_if_changed(cargo_path, new_cargo_text):
            changed_files.append("src-tauri/Cargo.toml")

    # 5. 同步 src-tauri/tauri.conf.json
    tauri_conf_path = root_dir / "src-tauri" / "tauri.conf.json"
    tauri_text = _read_raw_text(tauri_conf_path)
    if tauri_text is not None:
        new_tauri_text = re.sub(
            r'("productName"\s*:\s*"[^"]+"\s*,\s*\r?\n\s*"version"\s*:\s*")[^"]+(")',
            rf"\g<1>{version}\g<2>",
            tauri_text,
            count=1,
        )
        if _write_if_changed(tauri_conf_path, new_tauri_text):
            changed_files.append("src-tauri/tauri.conf.json")

    return changed_files


if __name__ == "__main__":
    arg_ver = sys.argv[1] if len(sys.argv) > 1 else None
    updated = sync_all_manifests(BASE_DIR, new_version=arg_ver)
    current_cfg = load_version_config(BASE_DIR)
    print(f"当前全局版本配置: v{current_cfg['version']} ({current_cfg['edition']})")
    if updated:
        print("已同步更新清单:", ", ".join(updated))
    else:
        print("全仓清单版本号已是最新一致状态。")
