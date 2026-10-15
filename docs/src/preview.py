"""Hoja de contactos de un PDF (para revisar la maquetación). Uso: python preview.py archivo.pdf salida.png [pág1,pág2]"""
import sys

import pypdfium2 as pdfium
from PIL import Image

pdf = pdfium.PdfDocument(sys.argv[1])
pages = [int(p) - 1 for p in sys.argv[3].split(",")] if len(sys.argv) > 3 else range(len(pdf))
scale = 1.2 if len(sys.argv) > 3 else 0.32
imgs = [pdf[i].render(scale=scale).to_pil() for i in pages]
cols = 2 if len(sys.argv) > 3 else 6
w, h = imgs[0].size
rows = (len(imgs) + cols - 1) // cols
sheet = Image.new("RGB", (cols * (w + 8), rows * (h + 8)), "#9aa3ad")
for n, im in enumerate(imgs):
    sheet.paste(im, ((n % cols) * (w + 8), (n // cols) * (h + 8)))
sheet.save(sys.argv[2])
print(sheet.size)
