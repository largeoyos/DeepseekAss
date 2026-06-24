# -*- mode: python ; coding: utf-8 -*-

datas = [('E:\\Projects\\DeepseekAss\\ui\\icon.svg', 'ui')]
binaries = []
hiddenimports = [
    'PyQt6.QtWebEngineWidgets',
    'langchain.agents',
    'langchain_core.tools',
    'langchain_openai',
    'langgraph.types',
    'langgraph.checkpoint.base',
    'llama_index.embeddings.openai',
]

a = Analysis(
    ['E:\\Projects\\DeepseekAss\\gui_main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'torch',
        'pandas',
        'scipy',
        'matplotlib',
        'sklearn',
        'transformers',
        'nltk',
        'tkinter',
        'langchain_classic',
        'onnxruntime',
        'tensorflow',
        'playwright',
        'sqlalchemy',
        'pdfminer',
        'pypdfium2',
        'grpc',
        'opentelemetry',
        'PIL',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='DeepseekAss',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='DeepseekAss',
)
