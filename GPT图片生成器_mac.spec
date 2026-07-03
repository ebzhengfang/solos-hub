# -*- mode: python ; coding: utf-8 -*-

# ============================================================
# macOS 打包配置文件
#
# 使用前注意：
#   1. 确保 Mac 上已装 Python 3.13+，且 pip install 了所有依赖
#   2. customtkinter 的 datas 路径用动态获取，无需手动改
#   3. 打包命令：python3 -m PyInstaller --clean --noconfirm "GPT图片生成器_mac.spec"
#   4. 产物在 dist/GPT图片生成器.app，双击即可运行
#   5. 首次打开被 Gatekeeper 拦截 → 系统设置 → 隐私与安全性 → 仍要打开
# ============================================================

import sys
from pathlib import Path

# 动态获取 customtkinter 安装路径（跨平台兼容）
import customtkinter
ctk_path = str(Path(customtkinter.__file__).parent)

a = Analysis(
    ['image_generator_app.py'],
    pathex=[],
    binaries=[],
    datas=[(ctk_path, 'customtkinter')],
    hiddenimports=['docx', 'docx.opc', 'docx.oxml', 'openpyxl', 'openpyxl.cell', 'openpyxl.workbook', 'openpyxl.worksheet', 'et_xmlfile', 'pptx', 'pptx.util', 'pptx.enum', 'PyPDF2', 'PyPDF2.filters', 'PyPDF2.generic'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'numpy', 'numpy._core', 'numpy.core', 'numpy.lib', 'numpy.linalg',
        'numpy.fft', 'numpy.random', 'numpy.ma', 'numpy.polynomial', 'numpy.typing',
        'numpy.f2py', 'numpy.distutils', 'numpy.testing', 'numpy.matrixlib',
        'numpy.compat', 'numpy._typing', 'numpy._utils', 'numpy._globals',
        'numpy._array_api_info', 'numpy._expired_attrs_2_0', 'numpy.dtypes',
        'openpyxl.utils.dataframe',
        'cryptography', 'cffi',
        'PIL._avif', 'PIL.AvifImagePlugin',
        'setuptools', 'pkg_resources', 'pydoc_data', 'unittest',
        'xlsxwriter',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='GPT图片生成器',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
