// FILE LOCALE — non fa parte di foliate-js.
//
// Perche' esiste.
// Alcune estensioni di Chrome sostituiscono il costruttore `Blob` della pagina
// per infilare il proprio codice dentro ogni documento che il sito costruisce.
// Su un Chrome reale abbiamo misurato questo comportamento:
//
//   Blob.name === 'n'                      (non e' piu' il costruttore nativo)
//   new Blob([xhtml], {type:'application/xhtml+xml'})
//        -> SyntaxError: insertAdjacentHTML ... markup is invalid XML
//   new Blob([xhtml], {type:'text/html'})
//        -> riesce, ma da 95 byte ne restituisce 5066: 4971 byte iniettati
//
// I capitoli di un EPUB sono esattamente `application/xhtml+xml`. Risultato:
// ogni capitolo falliva, foliate ingoiava l'errore (paginator.js, #goTo, il
// `catch` che restituisce {}) e il lettore restava bianco senza un solo errore
// visibile. Ore perse a cercare il guasto nella libreria e nel browser.
//
// La via d'uscita: `new Response(dati).blob()` produce un Blob nativo senza
// passare dal costruttore sostituito. Contenuto identico, tipo corretto,
// nessuna iniezione.
//
// Nota: quando `Blob` e' quello vero si usa la strada normale, che e' piu'
// veloce; la deviazione scatta solo quando serve davvero.

const blobNativo = (() => {
    try { return /\[native code\]/.test(String(Blob)) } catch { return false }
})()

/** Il costruttore Blob della pagina e' stato sostituito da un'estensione? */
export const blobManomesso = () => !blobNativo

const aByte = async parte => {
    if (parte instanceof ArrayBuffer) return new Uint8Array(parte)
    if (ArrayBuffer.isView(parte))
        return new Uint8Array(parte.buffer, parte.byteOffset, parte.byteLength)
    // Copre stringhe e Blob senza toccare il costruttore manomesso.
    return new Uint8Array(await new Response(parte).arrayBuffer())
}

/**
 * Come `new Blob(parti, opzioni)`, ma resistente al costruttore sostituito.
 * Restituisce sempre una Promise.
 */
export const creaBlob = async (parti, opzioni = {}) => {
    const elenco = Array.isArray(parti) ? parti : [parti]
    const tipo = opzioni?.type
    // Senza un tipo il costruttore manomesso non interferisce: nessuna deviazione.
    if (blobNativo || !tipo) return new Blob(elenco, opzioni)

    const pezzi = await Promise.all(elenco.map(aByte))
    let totale = 0
    for (const p of pezzi) totale += p.byteLength
    const tutto = new Uint8Array(totale)
    let i = 0
    for (const p of pezzi) { tutto.set(p, i); i += p.byteLength }
    return new Response(tutto, { headers: { 'Content-Type': tipo } }).blob()
}
