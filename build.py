# -*- coding: utf-8 -*-
"""WeChatOneBotBridge 一键打包脚本

打包三个组件：
  - bridge.exe              来自 wechat-weflow-bridge-ob11/main.py
  - MaiBotBridgeManager.exe 来自 bridge-manager/bridge_manager.py
  - log_tail.exe            来自 bridge-manager/log_tail.py

产物：dist/WeChatOneBotBridge-v{VERSION}.zip
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BRIDGE_SRC = ROOT / "bridge"
MANAGER_SRC = ROOT / "manager"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
PYTHON = Path(sys.executable)

VERSION = "1.1.1"
PKG_NAME = f"WeChatOneBotBridge-v{VERSION}"


def step(msg: str) -> None:
    print(f"\n=== {msg} ===", flush=True)


def clean() -> None:
    for p in [DIST, BUILD]:
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
    for sub in [BRIDGE_SRC, MANAGER_SRC]:
        for d in sub.glob("__pycache__"):
            shutil.rmtree(d, ignore_errors=True)
        for f in sub.glob("*.spec"):
            f.unlink(missing_ok=True)


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print(" ".join(cmd), flush=True)
    subprocess.check_call(cmd, cwd=str(cwd) if cwd else None)


def install_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
        print(f"PyInstaller 已安装：{PyInstaller.__version__}")
    except ImportError:
        step("安装 PyInstaller")
        run([str(PYTHON), "-m", "pip", "install", "pyinstaller"])


def pyi(src: Path, name: str, workdir: Path, onefile: bool = True, windowed: bool = False, extra_args: list[str] | None = None) -> None:
    cmd = [
        str(PYTHON), "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name", name,
        "--workpath", str(BUILD / f"work-{name}"),
        "--distpath", str(DIST),
        "--specpath", str(BUILD / "specs"),
    ]
    if onefile:
        cmd.append("--onefile")
    if windowed:
        cmd.append("--windowed")
    if extra_args:
        cmd.extend(extra_args)
    cmd.append(str(src))
    run(cmd, cwd=workdir)


def build_bridge() -> None:
    step("打包 bridge.exe")
    webui_dist = ROOT / "webui" / "dist"
    if not webui_dist.exists():
        raise SystemExit(
            f"缺少前端构建产物 {webui_dist}，请先在 webui/frontend 里 npm run build"
        )
    extras = [
        "--add-data", f"{BRIDGE_SRC / 'config.example.json'};.",
        # webui 包在仓库根（bridge/ 的上一级），需要显式加入分析路径，
        # 否则 PyInstaller 找不到 `from webui.app import ...`，打出来的
        # bridge.exe 启动即报 ModuleNotFoundError: No module named 'webui'
        "--paths", str(ROOT),
        "--hidden-import", "webui",
        "--hidden-import", "webui.app",
        "--hidden-import", "webui.monitor",
        # 前端静态文件（FastAPI StaticFiles 直接读文件系统，必须作为数据打入）
        "--add-data", f"{webui_dist};webui/dist",
        # uvicorn 的 loops/protocols 等是运行时动态 import 的
        "--collect-submodules", "uvicorn",
        # 隐藏 import（PyInstaller 默认按 import 追踪，一些动态加载需要手动加）
        "--hidden-import", "websockets",
        "--hidden-import", "comtypes.gen",
        "--hidden-import", "uiautomation",
        "--hidden-import", "PIL._tkinter_finder",
    ]
    pyi(BRIDGE_SRC / "main.py", "bridge", BRIDGE_SRC, onefile=True, windowed=False, extra_args=extras)


def build_log_tail() -> None:
    step("打包 log_tail.exe")
    pyi(MANAGER_SRC / "log_tail.py", "log_tail", MANAGER_SRC, onefile=True, windowed=False)


def build_manager() -> None:
    step("打包 MaiBotBridgeManager.exe")
    extras = [
        "--hidden-import", "pystray",
        "--hidden-import", "PIL._tkinter_finder",
        "--hidden-import", "tkinter",
    ]
    pyi(MANAGER_SRC / "bridge_manager.py", "MaiBotBridgeManager", MANAGER_SRC, onefile=True, windowed=True, extra_args=extras)


def assemble_zip() -> Path:
    step("组装发布 zip")
    rel = DIST / PKG_NAME
    if rel.exists():
        shutil.rmtree(rel)
    rel.mkdir(parents=True)

    # 1) exe
    for fname in ("bridge.exe", "MaiBotBridgeManager.exe", "log_tail.exe"):
        src = DIST / fname
        if src.exists():
            shutil.copy2(src, rel / fname)

    # 2) 配置文件（先校验 JSON 合法性，避免把损坏的 config 打进发布包）
    import json
    cfg_src = BRIDGE_SRC / "config.example.json"
    try:
        json.loads(cfg_src.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise SystemExit(f"config.example.json 不是合法 JSON，已中止打包: {exc}")
    shutil.copy2(cfg_src, rel / "config.json")

    # 3) 启动脚本（双击启动管理器）
    (rel / "启动.bat").write_text(
        """@echo off
chcp 65001 >nul
start "" MaiBotBridgeManager.exe
""",
        encoding="utf-8",
    )

    # 4) WeChatOneBotBridge README（项目根的 README 是 Akasha-WeChat 历史说明，新的在 docs/）
    readme_dst = rel / "README.md"
    src_readme = ROOT / "docs" / "WeChatOneBotBridge-README.md"
    if src_readme.exists():
        shutil.copy2(src_readme, readme_dst)

    # 5) LICENSE（打包 zip 时附一份）
    if not (rel / "LICENSE").exists() and (ROOT / "LICENSE").exists():
        shutil.copy2(ROOT / "LICENSE", rel / "LICENSE")

    # 5) 打包 zip
    zip_path = DIST / f"{PKG_NAME}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in rel.rglob("*"):
            if path.is_file():
                arc = path.relative_to(DIST).as_posix()
                zf.write(path, arc)
    print(f"\n生成：{zip_path} ({zip_path.stat().st_size / 1024 / 1024:.2f} MB)")
    return zip_path


def main() -> int:
    # Windows 控制台默认 GBK，输出 emoji 会抛 UnicodeEncodeError
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    t0 = time.time()
    DIST.mkdir(exist_ok=True)
    clean()
    install_pyinstaller()
    build_bridge()
    build_log_tail()
    build_manager()
    zip_path = assemble_zip()
    print(f"\n✅ 打包完成，耗时 {time.time() - t0:.1f}s")
    print(f"   发布包：{zip_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())