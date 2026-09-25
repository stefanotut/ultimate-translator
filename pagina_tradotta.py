"""Traduce UNA pagina di un PDF scansionato, conservando immagini e impaginazione.

Perche' una pagina alla volta
-----------------------------
Un libro illustrato scansionato non si traduce "tutto insieme": il testo e'
stampato dentro la fotografia della pagina, e ogni tentativo di rifare 232
pagine in blocco ha prodotto o un muro di testo senza immagini, o pagine con le
righe accavallate. Qui invece si traduce la pagina che l'utente sta guardando,
quando la chiede, e la si mette da parte: la seconda volta e' gratis.

Il metodo
---------
1. La fotografia originale resta come sfondo: illustrazioni e impaginazione
   non si toccano.
2. Il testo lo riconosce tesseract, chiamato DIRETTAMENTE (`--psm 12`, uscita
   TSV): da' il riquadro esatto di ogni parola. L'OCR incorporato in PyMuPDF
   non va bene: assegna a ogni parola il riquadro dell'intera riga di
   tesseract, e quando quella riga e' sporcata da un disegno vicino, o unisce
   due colonne, tutte le sue parole risultano alte tre righe o larghe mezza
   pagina. Da li' venivano le parole giganti e i paragrafi accavallati.
3. Le righe si ricompongono dalle parole, e i PARAGRAFI dalla geometria delle
   righe (vicinanza, stessa colonna, stessa altezza, rientri). Si traduce per
   paragrafo: riga per riga il modello perde il contesto e spezza le frasi.
4. Ogni riga originale si copre con una toppa del colore che ha ATTORNO
   (mediana di sei punti, non media: un pixel nero di una freccia non deve
   ingrigire la toppa). Coprire a blocchi col bianco cancellava i fondi
   colorati e le frecce.
5. L'italiano si ricompone nello stesso rettangolo del paragrafo, con lo stesso
   corpo, interlinea e allineamento, e si RIMPICCIOLISCE finche' entra: mai una
   riga sopra l'altra. Le righe singole (titoli, etichette) possono allargarsi
   verso destra fino al margine invece di farsi tagliare.

Dove va il tempo (misurato sul server, 2 vCPU lente)
----------------------------------------------------
Su tre pagine del libro di prova: tesseract 26-41 s, traduzione 15-16 s, tutto
il resto (decodifica, sfondo, toppe, resa, salvataggio) sotto 1,5 s. Quindi:
- l'OCR si puo' fare PRIMA che l'utente chieda la pagina (`--solo-ocr`, vedi
  sotto): non costa nulla e alla richiesta resta solo la traduzione;
- la traduzione va in lotti piccoli e paralleli: un lotto solo con tutta la
  pagina dura quanto il modello ci mette a scrivere l'intera pagina.

Uso da riga di comando (e' cosi' che lo chiama il portale, in un processo a
parte: se il riconoscimento mangia memoria, muore lui e non il sito):
    python pagina_tradotta.py <pdf> <numero_pagina_da_1> <uscita.png|uscita.jpg>
    python pagina_tradotta.py --solo-ocr <pdf> <numero_pagina_da_1> <uscita.png|uscita.jpg>

Con `--solo-ocr` non si traduce e non si disegna nulla: si riconosce il testo e
si salva `<uscita senza estensione>.ocr.json`. La modalita' normale, se trova
quel file accanto all'uscita, SALTA tesseract e lo usa. L'uscita e' JPEG se il
percorso finisce in .jpg/.jpeg, PNG altrimenti. Alla fine stampa su stderr una
riga con i tempi di ogni fase.
"""
import json
import os
import re
import sys
import time
import resource
import statistics
import subprocess
from contextlib import contextmanager

QUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, QUI)

DPI_OCR = 200          # per riconoscere: qui la definizione serve (la scansione E' a 200)
DPI_USCITA = 150
QUALITA_USCITA = 88    # JPEG della pagina finita, quando l'uscita e' .jpg: un terzo del PNG
MARGINE_PAGINA = 18    # punti dal bordo oltre cui un titolo non si allarga
CONFIDENZA_MINIMA = 25 # sotto, tesseract sta "leggendo" un disegno
# 12 = testo sparso con OSD: trova anche le etichette isolate dentro i disegni
# ("Brain", "odour molecule") che il modo automatico (3) salta. I paragrafi
# li ricostruiamo noi dalla geometria, quindi la sua analisi di pagina non serve.
PSM = os.environ.get("PAGINA_OCR_PSM", "12")
VERSIONE_OCR = 1       # formato del .ocr.json: se cambia, i file vecchi si ignorano

# Lotti per la traduzione di UNA pagina. Il traduttore del libro intero
# raggruppa 3500 caratteri / 20 blocchi per fare pochi viaggi; qui pero' i
# viaggi sono paralleli e conta la LATENZA: una pagina intera in un lotto solo
# costa 13-16 s (il modello scrive tutta la pagina di fila), la stessa pagina
# in lotti da 1200 caratteri / 4 blocchi 6 s (misurato su tre pagine, due
# volte ciascuna; lotti ancora piu' piccoli non guadagnano nulla).
LOTTO_CARATTERI = 1200
LOTTO_BLOCCHI = 4
BLOCCO_AUTONOMO = 200  # sotto questa lunghezza un blocco non viaggia mai da solo


class _Cronometro:
    """Somma i tempi per fase; alla fine li stampa in UNA riga su stderr."""

    def __init__(self):
        self.fasi = {}
        self.inizio = time.perf_counter()

    @contextmanager
    def misura(self, fase):
        t = time.perf_counter()
        try:
            yield
        finally:
            self.fasi[fase] = self.fasi.get(fase, 0.0) + time.perf_counter() - t

    def stampa(self, nota=""):
        totale = time.perf_counter() - self.inizio
        rss = "rss %.0fMB" % (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
        if "ocr" in self.fasi:
            # solo se tesseract e' girato davvero: altrimenti il dato dei figli e'
            # l'istantanea del fork di un aiutante minuscolo, e confonderebbe
            rss += " tesseract %.0fMB" % (resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024)
        riga = " ".join("%s %.2fs" % (k, v) for k, v in self.fasi.items())
        print("tempi: %s totale %.2fs | %s%s"
              % (riga, totale, rss, " | " + nota if nota else ""), file=sys.stderr)


def _tessdata():
    for p in ("/usr/share/tesseract-ocr/5/tessdata", "/usr/share/tesseract-ocr/4.00/tessdata",
              "/opt/homebrew/share/tessdata", "/usr/local/share/tessdata"):
        if os.path.isdir(p):
            return p
    return os.environ.get("TESSDATA_PREFIX", "")


def _colore_intorno(pix, r, sc):
    px = lambda x, y: pix.pixel(max(0, min(int(x / sc), pix.width - 1)),
                                max(0, min(int(y / sc), pix.height - 1)))[:3]
    c = []
    for (x, y) in ((r.x0 - 3, r.y0 + 1), (r.x1 + 3, r.y0 + 1), (r.x0 + 1, r.y0 - 3),
                   (r.x0 + 1, r.y1 + 3), (r.x0 - 3, r.y1 - 1), (r.x1 + 3, r.y1 - 1)):
        try:
            c.append(px(x, y))
        except Exception:
            pass
    if not c:
        return (1, 1, 1)
    return tuple(statistics.median(k[i] for k in c) / 255 for i in range(3))


def _e_testo(t):
    """Scarta quello che l'OCR "legge" nei disegni: 'ss', 'eeeeeee—', '-s----ssss'.

    Una riga vera ha lettere per almeno meta' dei suoi caratteri, almeno una
    parola di tre lettere e nessuna sfilza dello stesso carattere.
    """
    compatto = t.replace(" ", "")
    if len(compatto) < 2:
        return False
    lettere = sum(c.isalpha() for c in compatto)
    if lettere / len(compatto) < 0.5:
        return False
    if not re.search(r"[A-Za-zÀ-ÿ]{3}", t):
        return False
    if re.search(r"(.)\1{4,}", compatto):
        return False
    return True


_BORDI = r"^[\[\]|(){}<>_\-—–~=+*·•]+\s*|\s*[\[\]|(){}<>_\-—–~=+*·•]+$"


def _parole_tesseract(png):
    """[(Rect in punti, parola, conf)] leggendo il TSV di tesseract."""
    import fitz
    env = dict(os.environ)
    td = _tessdata()
    if td:
        env.setdefault("TESSDATA_PREFIX", td)
    # UN thread solo. Tesseract con OpenMP su 2 vCPU lente e condivise col
    # portale passa il tempo a far litigare i thread: misurato sulle stesse tre
    # pagine 32,4 / 29,9 / 27,3 s con i thread liberi contro 5,3 / 10,9 / 4,7 s
    # con uno solo, a parita' di risultato (TSV identico parola per parola).
    # In piu' lascia l'altra CPU al sito. setdefault: chi lancia puo' cambiarlo.
    env.setdefault("OMP_THREAD_LIMIT", "1")
    r = subprocess.run(["tesseract", png, "stdout", "--psm", PSM, "-l", "eng", "tsv"],
                       capture_output=True, text=True, errors="replace", env=env, timeout=180)
    if r.returncode != 0:
        raise RuntimeError("tesseract: " + r.stderr[-300:])
    sc = 72.0 / DPI_OCR
    fuori = []
    for riga in r.stdout.splitlines()[1:]:
        c = riga.split("\t")
        if len(c) != 12 or c[0] != "5":
            continue
        parola = c[11].strip()
        try:
            conf = float(c[10])
        except ValueError:
            conf = 0
        if not parola or conf < CONFIDENZA_MINIMA:
            continue
        x, y, w, h = (int(c[i]) for i in (6, 7, 8, 9))
        chiave = (int(c[2]), int(c[3]), int(c[4]))        # blocco, paragrafo, riga
        fuori.append((chiave, int(c[5]), fitz.Rect(x * sc, y * sc, (x + w) * sc, (y + h) * sc), parola, conf))
    return fuori


def _righe(png):
    """Le righe della pagina: [(Rect, testo)], ricomposte dalle parole."""
    import fitz
    per_riga = {}
    for chiave, n, rect, parola, conf in _parole_tesseract(png):
        per_riga.setdefault(chiave, []).append((n, rect, parola, conf))
    fuori = []
    for chiave in sorted(per_riga):
        ordinate = sorted(per_riga[chiave], key=lambda k: k[0])
        # Una "parola" sola, corta, di cui tesseract non e' sicuro, in mezzo a un
        # titolo in carattere decorativo e' quasi sempre un pezzo di lettera letto
        # male ("PERFUME" -> "AHN" sulla coda della M): coprirla cancella il titolo
        # vero. Le etichette corte ma lette bene ("Brain", conf 96) restano.
        if len(ordinate) == 1 and len(ordinate[0][2]) <= 4 and ordinate[0][3] < 70:
            continue
        parole = [(r, t) for _, r, t, _ in ordinate]
        h = statistics.median(r.height for r, _ in parole)
        # tesseract separa gia' le colonne; questo e' un paracadute per quando
        # non lo fa. Misurato: i canali fra colonne sono larghi da 1,5 altezze
        # in su, gli spazi fra parole (anche giustificate) restano sotto 0,8.
        gruppi, corrente = [], [parole[0]]
        for prec, (r, t) in zip(parole, parole[1:]):
            if r.x0 - prec[0].x1 > 1.1 * h:
                gruppi.append(corrente); corrente = []
            corrente.append((r, t))
        gruppi.append(corrente)
        for g in gruppi:
            rect = fitz.Rect(g[0][0])
            for r, _ in g[1:]:
                rect |= r
            testo = re.sub(_BORDI, "", " ".join(t for _, t in g)).strip()
            if _e_testo(testo):
                fuori.append((rect, testo))
    return fuori


def _fondi_frammenti(righe):
    """Ricuce i pezzi di una stessa riga che tesseract ha dato separati.

    In modo sparso una riga giustificata con spazi larghi puo' uscire in due
    "righe" affiancate: se stanno alla stessa altezza e a meno di UNA altezza
    di distanza sono la stessa riga, e vanno lette da sinistra a destra.
    Non di piu': a 1,6 altezze incollava la colonna centrale a quella di
    destra (i canali misurati partono da 1,5), e i paragrafi si accavallavano.
    """
    import fitz
    righe = sorted(righe, key=lambda x: (x[0].x0, x[0].y0))
    fuori = []
    for r, t in righe:
        for i, (ur, ut) in enumerate(fuori):
            h = min(r.height, ur.height)
            sovr = min(r.y1, ur.y1) - max(r.y0, ur.y0)
            if sovr < 0.6 * h:
                continue                                   # non alla stessa altezza
            if not (-0.2 * h <= r.x0 - ur.x1 <= 0.9 * h):
                continue                                   # non subito a destra
            fuori[i] = (fitz.Rect(ur) | r, ut + " " + t)
            break
        else:
            fuori.append((fitz.Rect(r), t))
    return fuori


def _unisci(righe):
    """Le righe di un paragrafo in un testo solo, ricucendo le parole spezzate."""
    fuori = ""
    for _, t in righe:
        if fuori.endswith("-") and t[:1].islower():
            fuori = fuori[:-1] + t            # "neu-" + "roni" -> "neuroni"
        elif fuori:
            fuori += " " + t
        else:
            fuori = t
    return fuori


def _allineamento(rb, righe):
    """Come sono allineate le righe: sinistra, destra, centro o giustificato."""
    if len(righe) < 2:
        return "left"
    tol = max(3.0, rb.width * 0.04)
    x0 = [r.x0 for r, _ in righe]
    x1 = [r.x1 for r, _ in righe[:-1]]      # l'ultima riga di un paragrafo e' corta
    c = [(r.x0 + r.x1) / 2 for r, _ in righe]
    d0, dc = max(x0) - min(x0), max(c) - min(c)
    d1 = (max(x1) - min(x1)) if len(x1) > 1 else 0
    if d0 <= tol and d1 <= tol and len(righe) >= 3:
        return "justify"
    if d0 <= tol:
        return "left"
    if dc <= tol:
        return "center"
    if d1 <= tol:
        return "right"
    return "left"


def _paragrafi(righe):
    """I paragrafi, ricostruiti dalla GEOMETRIA delle righe.

    Una riga si accoda al paragrafo che ha sopra di se' solo se: e' vicina
    (meno di 3/4 di riga di vuoto), si sovrappone in orizzontale (stessa
    colonna), ha piu' o meno la stessa altezza (un titolo non si fonde col
    testo) e non e' l'inizio rientrato di un altro paragrafo.
    """
    import fitz
    righe = sorted(_fondi_frammenti(righe), key=lambda x: (round(x[0].y0 / 2), x[0].x0))
    paragrafi = []
    for r, t in righe:
        h = r.height
        scelto = None
        for p in paragrafi:
            ur, _ = p["righe"][-1]
            # L'altezza di una riga dipende dalle lettere che contiene (con o
            # senza gambe): la tolleranza e' larga, un titolo e' comunque oltre.
            if abs(ur.height - h) > 0.55 * max(ur.height, h):
                continue
            vuoto = r.y0 - ur.y1
            if vuoto < -0.5 * h or vuoto > 0.75 * h:
                continue                                   # non e' la riga sotto
            sovr = min(r.x1, p["rect"].x1) - max(r.x0, p["rect"].x0)
            if sovr < 0.5 * min(r.width, p["rect"].width):
                continue                                   # altra colonna
            if len(p["righe"]) >= 2:
                if r.x0 - p["rect"].x0 > 1.5 * h:
                    continue                               # rientro: nuovo paragrafo
                if ur.x1 < p["rect"].x1 - 3 * h and r.x1 > p["rect"].x1 - 1.5 * h:
                    continue                               # la riga sopra era l'ultima
            scelto = p
            break
        if scelto:
            scelto["righe"].append((r, t))
            scelto["rect"] |= r
        else:
            paragrafi.append({"righe": [(r, t)], "rect": fitz.Rect(r)})

    paragrafi = _ricuci_paragrafi(paragrafi)
    for p in paragrafi:
        n = len(p["righe"])
        # Il corpo si ricava dalla geometria: su piu' righe comanda l'interlinea
        # (il corpo e' circa l'80% del passo), su una riga sola l'altezza delle
        # lettere.
        alt = max(r.height for r, _ in p["righe"])
        if n > 1:
            p["interlinea"] = p["rect"].height / n
            p["corpo"] = min(p["interlinea"] * 0.8, alt * 0.95)
        else:
            p["corpo"] = alt * 0.85
            p["interlinea"] = p["corpo"] * 1.2
        p["testo"] = _unisci(p["righe"])
        p["allinea"] = _allineamento(p["rect"], p["righe"])
    return paragrafi


def _ricuci_paragrafi(paragrafi):
    """Riattacca un paragrafo spezzato a meta' frase.

    Le regole geometriche (rientro, riga corta) ogni tanto tagliano un
    paragrafo in due. Il testo pero' lo dice: se il pezzo sopra non finisce
    con un punto e il pezzo sotto comincia con una minuscola, e' la stessa
    frase. Si ricuce solo se stanno nella stessa colonna, uno subito sotto
    l'altro.
    """
    import fitz
    paragrafi = sorted(paragrafi, key=lambda p: (round(p["rect"].x0 / 20), p["rect"].y0))
    fuori = []
    for p in paragrafi:
        if fuori:
            q = fuori[-1]
            sopra = q["righe"][-1][1].rstrip()
            sotto = p["righe"][0][1].lstrip()
            h = max(r.height for r, _ in q["righe"])
            passo = q["rect"].height / len(q["righe"]) if len(q["righe"]) > 1 else h * 1.4
            sovr = min(p["rect"].x1, q["rect"].x1) - max(p["rect"].x0, q["rect"].x0)
            if (sopra and sopra[-1] not in '.!?:;"”)' and sotto[:1].islower()
                    and sovr >= 0.5 * min(p["rect"].width, q["rect"].width)
                    and -0.3 * h <= p["rect"].y0 - q["rect"].y1 <= passo
                    and abs(p["righe"][0][0].height - h) <= 0.55 * max(h, p["righe"][0][0].height)):
                q["righe"] += p["righe"]
                q["rect"] = fitz.Rect(q["rect"]) | p["rect"]
                continue
        fuori.append(p)
    return fuori


def _esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;")


# ---------------------------------------------------------------------------
# Cache dell'OCR: <uscita senza estensione>.ocr.json
#
# Il riconoscimento e' la fase lenta e non dipende dalla lingua di arrivo: il
# portale puo' farlo in anticipo per le prossime pagine (gratis, niente AI) e
# alla richiesta resta la sola traduzione. Il file e' scritto per intero in un
# provvisorio e poi rinominato: chi lo legge non trova mai un JSON a meta'.
# ---------------------------------------------------------------------------

def _carica_traduzione(percorso, firma, target_lang):
    try:
        with open(percorso, "r", encoding="utf-8") as f:
            dati = json.load(f)
        if dati.get("firma") == firma and dati.get("lingua") == target_lang:
            return dati["tradotti"]
    except (OSError, ValueError, KeyError):
        pass
    return None


def _salva_traduzione(percorso, firma, target_lang, tradotti):
    tmp = "%s.%d.provvisorio" % (percorso, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"firma": firma, "lingua": target_lang, "tradotti": tradotti}, f, ensure_ascii=False)
        os.replace(tmp, percorso)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


def percorso_ocr(uscita):
    return os.path.splitext(uscita)[0] + ".ocr.json"


def _salva_ocr(percorso, pdf, n, paragrafi):
    dati = {"versione": VERSIONE_OCR, "pdf": os.path.basename(pdf), "pagina": n,
            "dpi": DPI_OCR, "psm": PSM,
            "paragrafi": [{"rect": list(p["rect"]),
                           "righe": [{"rect": list(r), "testo": t} for r, t in p["righe"]],
                           "testo": p["testo"], "allinea": p["allinea"],
                           "corpo": p["corpo"], "interlinea": p["interlinea"]}
                          for p in paragrafi]}
    # nome col PID: se il portale prepara l'OCR di questa pagina mentre un altro
    # processo la sta traducendo, ognuno scrive il suo e l'ultimo rinominato vince
    provvisorio = "%s.%d.provvisorio" % (percorso, os.getpid())
    with open(provvisorio, "w", encoding="utf-8") as f:
        json.dump(dati, f, ensure_ascii=False)
    os.replace(provvisorio, percorso)


def _carica_ocr(percorso, n):
    """I paragrafi gia' riconosciuti, o None se il file manca, e' rotto o e' di un'altra pagina."""
    import fitz
    try:
        with open(percorso, encoding="utf-8") as f:
            dati = json.load(f)
        if dati.get("versione") != VERSIONE_OCR or dati.get("pagina") != n:
            return None
        return [{"rect": fitz.Rect(*p["rect"]),
                 "righe": [(fitz.Rect(*r["rect"]), r["testo"]) for r in p["righe"]],
                 "testo": p["testo"], "allinea": p["allinea"],
                 "corpo": float(p["corpo"]), "interlinea": float(p["interlinea"])}
                for p in dati["paragrafi"]]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _lotti(testi):
    """Come dividere i paragrafi fra le chiamate al modello.

    Lotti da circa LOTTO_CARATTERI e al massimo LOTTO_BLOCCHI, da mandare in
    parallelo. E MAI un'etichetta da sola: a un lotto di un blocco solo il
    traduttore manda il testo nudo, senza i segnaposto del lotto, e davanti a
    "EMOTION" o "Neuron 1" il modello risponde ("Sono pronto, inviami il
    testo", "Non posso aiutarti...") invece di tradurre: misurato, e finirebbe
    stampato nella pagina. In compagnia di un altro blocco capisce sempre. Un
    blocco lungo invece puo' viaggiare da solo: e' testo evidente.
    """
    lotti, corrente, car = [], [], 0
    for i, t in enumerate(testi):
        pieno = car + len(t) > LOTTO_CARATTERI or len(corrente) >= LOTTO_BLOCCHI
        solo_corto = len(corrente) == 1 and len(testi[corrente[0]]) < BLOCCO_AUTONOMO
        if corrente and pieno and not solo_corto:
            lotti.append(corrente)
            corrente, car = [], 0
        corrente.append(i)
        car += len(t)
    if len(corrente) == 1 and len(testi[corrente[0]]) < BLOCCO_AUTONOMO and lotti:
        lotti[-1] += corrente            # l'ultima etichetta va col lotto prima
    elif corrente:
        lotti.append(corrente)
    return lotti


def _traduci(testi, source_lang, target_lang, provider, model):
    """I paragrafi tradotti, nello stesso ordine: un lotto per chiamata, lotti in parallelo."""
    import translator
    from concurrent.futures import ThreadPoolExecutor
    # I limiti di translator sono globali del modulo, e questo e' un processo a
    # parte (non tocca i job del portale): si alzano oltre qualunque pagina,
    # cosi' ogni lotto fatto qui e' UNA chiamata e non viene rispezzato.
    translator.BATCH_MAX_CHARS = translator.BATCH_MAX_BLOCKS = 10 ** 9
    lotti = _lotti(testi)

    def uno(lotto):
        return translator.translate_blocks([testi[i] for i in lotto], source_lang, target_lang,
                                           provider=provider, model=model)

    fuori = [None] * len(testi)
    with ThreadPoolExecutor(max_workers=len(lotti)) as pool:
        for lotto, tradotti in zip(lotti, pool.map(uno, lotti)):
            for i, t in zip(lotto, tradotti):
                fuori[i] = t
    return fuori


def _riconosci(pix, base, crono):
    """I paragrafi della pagina, con tesseract, dal pixmap RGB a DPI_OCR."""
    import fitz
    png_ocr = "%s.ocr.%d.png" % (base, os.getpid())    # col PID: due processi, due file
    with crono.misura("png_ocr"):
        # tesseract vuole un file; il grigio si ricava dal pixmap gia' decodificato
        fitz.Pixmap(fitz.csGRAY, pix).save(png_ocr)
    try:
        with crono.misura("ocr"):
            righe = _righe(png_ocr)
    finally:
        try:
            os.remove(png_ocr)
        except OSError:
            pass
    with crono.misura("paragrafi"):
        return _paragrafi(righe)


def traduci_pagina(pdf, n, uscita, provider="anthropic", model="claude-sonnet-5",
                   source_lang="English", target_lang="Italian", solo_ocr=False):
    """Scrive `uscita` (PNG o JPEG, dall'estensione) con la pagina `n` (da 1) tradotta.

    Con `solo_ocr` si ferma dopo il riconoscimento e scrive solo il .ocr.json.
    Ritorna il numero di paragrafi.
    """
    import fitz

    os.makedirs(os.path.dirname(uscita) or ".", exist_ok=True)
    base, est = os.path.splitext(uscita)
    json_ocr = percorso_ocr(uscita)
    crono = _Cronometro()

    with crono.misura("apertura"):
        d = fitz.open(pdf)
        pagina = d[n - 1]
    # UNA decodifica sola, a colori e alla definizione dell'OCR: da questo
    # pixmap si ricavano il grigio per tesseract, il colore delle toppe e lo
    # sfondo della pagina. Prima si decodificava tre volte.
    with crono.misura("decodifica"):
        pix = pagina.get_pixmap(dpi=DPI_OCR)
    sc = 72.0 / DPI_OCR

    paragrafi = None if solo_ocr else _carica_ocr(json_ocr, n)
    origine = "ocr da cache"
    if paragrafi is None:
        origine = "ocr con tesseract"
        paragrafi = _riconosci(pix, base, crono)
        _salva_ocr(json_ocr, pdf, n, paragrafi)
    if solo_ocr:
        d.close()
        crono.stampa(origine)
        return len(paragrafi)

    tradotti = []
    if paragrafi:
        # La traduzione costa soldi: si mette da parte accanto all'OCR, cosi' se
        # il processo muore DOPO aver pagato (timeout durante la resa, crash) la
        # pagina non si ripaga. Vale solo per lo stesso testo riconosciuto.
        import hashlib
        firma = hashlib.sha256("\n".join(p["testo"] for p in paragrafi).encode("utf-8")).hexdigest()
        json_trad = os.path.splitext(uscita)[0] + ".tradotto.json"
        tradotti = _carica_traduzione(json_trad, firma, target_lang)
        if tradotti is None:
            with crono.misura("traduzione"):
                tradotti = _traduci([p["testo"] for p in paragrafi], source_lang, target_lang,
                                    provider, model)
            _salva_traduzione(json_trad, firma, target_lang, tradotti)

    with crono.misura("resa"):
        out = fitz.open()
        nuova = out.new_page(width=pagina.rect.width, height=pagina.rect.height)
        # Lo sfondo e' il pixmap cosi' com'e', senza ricomprimerlo in JPEG:
        # misurato, inserirlo e renderlo a 150 DPI costa 0,16 s contro 0,49 s
        # del JPEG a 200 DPI e 0,32 s del vecchio JPEG a 140 DPI, e non
        # aggiunge artefatti. Sono ~10 MB in memoria per pochi decimi di secondo.
        nuova.insert_image(pagina.rect, pixmap=pix)

        for p, it in zip(paragrafi, tradotti):
            it = (it or "").strip() or p["testo"]
            # 1. si copre RIGA per riga col colore che c'e' attorno: cosi' un titolo
            #    su fondo colorato o un'etichetta in mezzo a un disegno non lasciano
            #    un rettangolo bianco
            for r, _ in p["righe"]:
                toppa = fitz.Rect(r.x0 - 1, r.y0 - 1, r.x1 + 1, r.y1 + 1) & nuova.rect
                if not toppa.is_empty:
                    nuova.draw_rect(toppa, color=None, fill=_colore_intorno(pix, r, sc), overlay=True)
            # 2. il paragrafo italiano nello stesso rettangolo, con lo stesso corpo e
            #    la stessa interlinea; se non entra si rimpicciolisce quanto basta
            rb = p["rect"]
            box = fitz.Rect(rb.x0 - 1, rb.y0 - 1, rb.x1 + 1, rb.y1 + 1)
            if len(p["righe"]) == 1 and len(it) > len(p["testo"]):
                # una riga sola (titolo, etichetta): meglio allargarsi verso destra
                # fino al margine che farsi rimpicciolire
                box.x1 = min(nuova.rect.width - MARGINE_PAGINA, box.x1 + box.width * 0.6)
            box &= nuova.rect
            if box.is_empty:
                continue
            corpo = min(p["corpo"], 40)
            interlinea = max(1.0, min(1.6, p["interlinea"] / corpo)) if len(p["righe"]) > 1 else 1.0
            html = ('<div style="font-family:sans-serif;font-size:%.2fpx;line-height:%.2f;'
                    'text-align:%s;margin:0;padding:0">%s</div>'
                    % (corpo, interlinea, p["allinea"], _esc(it)))
            # scale_low=0: se il testo non entra, MuPDF lo riduce da solo finche' entra
            nuova.insert_htmlbox(box, html, scale_low=0)
        pix_uscita = nuova.get_pixmap(dpi=DPI_USCITA)

    with crono.misura("salvataggio"):
        # PyMuPDF sceglie il formato dall'estensione: il provvisorio ha la stessa
        tmp = base + ".parziale" + est
        if est.lower() in (".jpg", ".jpeg"):
            pix_uscita.save(tmp, jpg_quality=QUALITA_USCITA)
        else:
            pix_uscita.save(tmp)
        os.replace(tmp, uscita)          # atomica: mai una pagina mezza scritta
    d.close(); out.close()
    crono.stampa(origine)
    return len(paragrafi)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(os.path.join(QUI, ".env"))
    solo_ocr = "--solo-ocr" in sys.argv[1:]
    argomenti = [a for a in sys.argv[1:] if a != "--solo-ocr"]
    if len(argomenti) != 3:
        sys.exit("uso: pagina_tradotta.py [--solo-ocr] <pdf> <pagina da 1> <uscita.png|.jpg>")
    pdf, n, uscita = argomenti[0], int(argomenti[1]), argomenti[2]
    quante = traduci_pagina(pdf, n, uscita, solo_ocr=solo_ocr)
    if solo_ocr:
        print("paragrafi riconosciuti: %d -> %s" % (quante, percorso_ocr(uscita)))
    else:
        print("paragrafi tradotti: %d -> %s" % (quante, uscita))
