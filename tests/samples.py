"""Small EPUB / PDF books generated on the fly for tests."""

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24.3
except ImportError:  # older PyMuPDF releases only have the fitz name
    import fitz
from ebooklib import epub

LOREM_IT = (
    "Il profumo e la memoria piu antica che abbiamo. In questo capitolo vediamo come le note di testa, "
    "le note di cuore e le note di fondo costruiscono una piramide olfattiva capace di raccontare un brand. "
    "Ogni fragranza deve parlare al cliente giusto: il marketing di un profumo non vende un liquido, "
    "vende un'emozione e una storia. Le strategie di vendita in profumeria partono dall'ascolto. "
)
LOREM_EN = (
    "Great sales teams do not push products, they solve problems. In this chapter we explore how to "
    "qualify prospects, handle objections and close the deal with confidence. Selling is a craft that "
    "rewards preparation, curiosity and follow-up. The best salespeople ask better questions. "
)


def _cover_png(label='COVER', color=(0.45, 0.22, 0.85)):
    doc = fitz.open()
    page = doc.new_page(width=400, height=600)
    page.draw_rect(page.rect, color=color, fill=color)
    page.insert_text((40, 300), label, fontsize=48, color=(1, 1, 1))
    data = page.get_pixmap().tobytes('png')
    doc.close()
    return data


def make_pdf(path, title='Marketing dei profumi', author='Giulia Rossi', pages=3, text=LOREM_IT,
             toc=('Introduzione', 'Le note olfattive', 'Vendere in profumeria')):
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(50, 50, 550, 800), f"{title} - pagina {i + 1}\n\n" + text * 3, fontsize=10)
    doc.set_metadata({'title': title or '', 'author': author or '', 'keywords': 'profumi, marketing'})
    if toc:
        doc.set_toc([[1, entry, min(i + 1, pages)] for i, entry in enumerate(toc)])
    doc.save(path)
    doc.close()
    return path


def make_epub(path, title='The Sales Playbook', author='John Smith', language='en', chapters=3,
              text=LOREM_EN, with_cover=True):
    book = epub.EpubBook()
    book.set_identifier(f'id-{title}')
    book.set_title(title)
    book.set_language(language)
    book.add_author(author)
    if with_cover:
        book.set_cover('cover.png', _cover_png(title[:10]))
    items = []
    for i in range(chapters):
        chapter = epub.EpubHtml(title=f'Chapter {i + 1}', file_name=f'chap_{i + 1}.xhtml', lang=language)
        chapter.content = f"<h1>Chapter {i + 1}: Closing deals</h1><p>{text * 4}</p>"
        book.add_item(chapter)
        items.append(chapter)
    book.toc = items
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ['nav'] + items
    epub.write_epub(path, book)
    return path


RICH_CHAPTER = '''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en">
<head><title>Chapter One</title><link rel="stylesheet" type="text/css" href="../Styles/style.css"/><style>p { color: #333; }</style></head>
<body class="chapter" id="top"><section epub:type="chapter"><h1 class="title">Chapter One</h1>
<p class="first">Hello&nbsp;<em>world</em>, sales &amp; marketing.</p>
<ul><li>Main point<ul><li>Detail point</li></ul></li></ul>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" preserveAspectRatio="none"><linearGradient id="g"/><rect width="5" height="5"/></svg>
<p>12</p></section></body></html>'''

RICH_NAV = '''<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents</title></head>
<body><nav epub:type="toc"><ol><li><a href="Text/chapter%201.xhtml">Chapter One</a></li></ol></nav></body></html>'''

RICH_NCX = '''<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head><meta name="dtb:uid" content="rich-1"/></head>
<docTitle><text>The Rich Book</text></docTitle>
<navMap><navPoint id="p1" playOrder="1"><navLabel><text>Chapter One</text></navLabel><content src="Text/chapter%201.xhtml"/></navPoint></navMap></ncx>'''

RICH_OPF = '''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="uid">rich-1</dc:identifier><dc:title>The Rich Book</dc:title><dc:language>en</dc:language><meta property="dcterms:modified">2024-01-01T00:00:00Z</meta></metadata>
<manifest>
<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
<item id="c1" href="Text/chapter%201.xhtml" media-type="application/xhtml+xml"/>
<item id="css" href="Styles/style.css" media-type="text/css"/>
</manifest>
<spine toc="ncx"><itemref idref="c1"/></spine></package>'''


def make_rich_epub(path, chapter=RICH_CHAPTER):
    """An EPUB written by hand, with the details a translation must keep (CSS, SVG, nested lists...)."""
    import zipfile

    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(zipfile.ZipInfo('mimetype'), 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        archive.writestr('META-INF/container.xml',
                         '<?xml version="1.0"?><container version="1.0" '
                         'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                         '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                         '</rootfiles></container>')
        archive.writestr('OEBPS/content.opf', RICH_OPF)
        archive.writestr('OEBPS/Text/chapter 1.xhtml', chapter)
        archive.writestr('OEBPS/nav.xhtml', RICH_NAV)
        archive.writestr('OEBPS/toc.ncx', RICH_NCX)
        archive.writestr('OEBPS/Styles/style.css', 'p { font-family: serif; }')
    return path


def make_text_pdf(path, paragraphs, fontsize=11):
    """A one-page PDF with a text block per paragraph."""
    doc = fitz.open()
    page = doc.new_page()
    y = 60
    for paragraph in paragraphs:
        page.insert_textbox(fitz.Rect(60, y, 540, y + 90), paragraph, fontsize=fontsize, fontname='helv')
        y += 120
    doc.save(path)
    doc.close()
    return path


def make_scanned_pdf(path):
    """A PDF with only an image (no text layer), like a scan."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_image(fitz.Rect(50, 50, 450, 650), stream=_cover_png('SCAN'))
    doc.save(path)
    doc.close()
    return path


def make_locked_pdf(path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 80), 'Secret sales plan for the new perfume line.', fontsize=12)
    doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw='secret', owner_pw='owner')
    doc.close()
    return path
