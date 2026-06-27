from pathlib import Path

import flet as ft
from app.gui.flet_app import main as flet_main

# repo 根目錄的 assets/（字型等）。用絕對路徑：flet 預設把相對 assets_dir
# 解析到「啟動腳本」所在目錄，從不同進入點啟動會各指到不同位置。
_ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets"


def main():
    ft.app(target=flet_main, assets_dir=str(_ASSETS_DIR))


if __name__ == "__main__":
    main()
