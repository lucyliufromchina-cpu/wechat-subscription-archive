#!/usr/bin/env python3
"""配置管理：目标公众号列表 + 归档目录。

配置文件：~/.config/抓订阅公众号/config.json
"""

import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "抓订阅公众号"
CONFIG_PATH = CONFIG_DIR / "config.json"
DEFAULT_ARCHIVE_DIR = Path.home() / "wechat-archive"


def load_config():
    """读取配置；不存在则返回默认。"""
    if not CONFIG_PATH.exists():
        return {
            "targets": [],
            "archive_dir": str(DEFAULT_ARCHIVE_DIR),
        }
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def add_target(name):
    cfg = load_config()
    if name in cfg["targets"]:
        print(f"[已存在] {name}")
        return cfg
    cfg["targets"].append(name)
    save_config(cfg)
    print(f"[已添加] {name}")
    return cfg


def list_targets():
    cfg = load_config()
    if not cfg["targets"]:
        print("(配置里还没有目标公众号，用 --add \"公众号名\" 添加)")
        return
    print(f"已配置 {len(cfg['targets'])} 个目标公众号：")
    for i, n in enumerate(cfg["targets"], 1):
        print(f"  {i}. {n}")
    print(f"\n归档目录：{cfg['archive_dir']}")


def get_archive_dir():
    cfg = load_config()
    return Path(cfg["archive_dir"]).expanduser()


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: config.py [list|add <name>]")
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "list":
        list_targets()
    elif cmd == "add" and len(sys.argv) >= 3:
        add_target(sys.argv[2])
    else:
        print("未知命令")
        sys.exit(1)
