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
    excludes=[
        # ---- numpy 全家桶（~29MB 原始）— 代码不用，PIL/openpyxl 核心不依赖 ----
        'numpy', 'numpy._core', 'numpy.core', 'numpy.lib', 'numpy.linalg',
        'numpy.fft', 'numpy.random', 'numpy.ma', 'numpy.polynomial', 'numpy.typing',
        'numpy.f2py', 'numpy.distutils', 'numpy.testing', 'numpy.matrixlib',
        'numpy.compat', 'numpy._typing', 'numpy._utils', 'numpy._globals',
        'numpy._array_api_info', 'numpy._expired_attrs_2_0', 'numpy.dtypes',
        # openpyxl 的 pandas 集成（硬依赖 numpy，我们只用 load_workbook + iter_rows）
        'openpyxl.utils.dataframe',
        # ---- cryptography + cffi（~7MB 原始）— requests 用 Python 内置 ssl ----
        'cryptography', 'cffi',
        # ---- PIL AVIF 支持（~7.5MB 原始）— API 不返回 AVIF 格式 ----
        'PIL._avif', 'PIL.AvifImagePlugin',
        # ---- 开发/测试工具（~2MB 原始）— 运行时不需要 ----
        'setuptools', 'pkg_resources', 'pydoc_data', 'unittest',
        # ---- xlsxwriter（~0.8MB 原始）— pptx 图表输出依赖，我们只提取文本 ----
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
    strip=False,  # strip=True 需要系统安装 strip 工具，未安装时 warning 可忽略
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
