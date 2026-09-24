"""Build downloadable SDK source archives for the console; excludes tests and caches."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
root=Path(__file__).resolve().parent
out=root.parent/'frontend/public/sdk'
out.mkdir(parents=True,exist_ok=True)
for language,files in {'python':['pyproject.toml','nexusdesk/__init__.py'],'javascript':['package.json','index.js']}.items():
    with ZipFile(out/f'nexusdesk-{language}.zip','w',ZIP_DEFLATED) as archive:
        for file in files:archive.write(root/language/file,f'{language}/{file}')
        archive.write(root/'README.md',f'{language}/README.md')
    print(f'{language}: archive built')
