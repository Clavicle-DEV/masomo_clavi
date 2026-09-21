from pathlib import Path
from PIL import Image

root = Path(__file__).resolve().parent
light_path = root / 'static' / 'images' / 'shield-icon-light.png'
if not light_path.exists():
    raise FileNotFoundError(f'Missing source icon: {light_path}')

base = Image.open(light_path).convert('RGBA')

# Keep the same design across all non-logo icon assets.
for rel_path, size in [
    ('static/images/shield-icon.png', (512, 512)),
    ('static/images/shield-icon-dark.png', (512, 512)),
    ('static/images/icon-192.png', (192, 192)),
    ('static/images/icon-512.png', (512, 512)),
    ('static/images/favicon-512.png', (512, 512)),
]:
    out = root / rel_path
    Image.open(light_path).convert('RGBA').resize(size, Image.LANCZOS).save(out)

# Create favicon.ico using the same shield style.
base.resize((256, 256), Image.LANCZOS).save(root / 'static' / 'favicon.ico', format='ICO')

# Ensure Clavis mark version matches the same style.
svg = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" role="img" aria-labelledby="title desc">
  <title id="title">Clavis</title>
  <desc id="desc">A green shield containing a cream C and gold learning bars</desc>
  <path fill="#14301F" stroke="#C6922A" stroke-width="5" d="M64 7 112 25v34c0 28-19 49-48 62C35 108 16 87 16 59V25L64 7Z"/>
  <path fill="#F7F1DF" d="M72 35c-4-4-9-6-15-6-14 0-24 11-24 25s10 25 24 25c6 0 11-2 15-6v10c-5 4-11 6-18 6-19 0-34-15-34-35s15-35 34-35c7 0 13 2 18 6v10Z"/>
  <path fill="#C6922A" d="M48 54h34v7H48zM51 65h7v25h-7zM61 65h7v31h-7zM71 65h7v25h-7z"/>
  <circle cx="88" cy="87" r="5" fill="#C6922A"/>
</svg>
'''
(root / 'static' / 'images' / 'clavis-mark.svg').write_text(svg, encoding='utf-8')

print('Updated non-logo app icons and favicon to match the light shield style. Logo PNG was left untouched.')
