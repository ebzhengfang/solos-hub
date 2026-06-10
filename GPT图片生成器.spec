# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['image_generator_app.py'],
    pathex=[],
    binaries=[],
    datas=[('C:/Users/28953/.workbuddy/binaries/python/versions/3.13.12/Lib/site-packages/customtkinter', 'customtkinter')],
    hiddenimports=['docx', 'docx.opc', 'docx.oxml', 'openpyxl', 'openpyxl.cell', 'openpyxl.workbook', 'openpyxl.worksheet', 'et_xmlfile', 'pptx', 'pptx.util', 'pptx.enum', 'PyPDF2', 'PyPDF2.filters', 'PyPDF2.generic'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
