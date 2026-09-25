"""Small EPUB / PDF books generated on the fly for tests."""

import fitz  # PyMuPDF
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
