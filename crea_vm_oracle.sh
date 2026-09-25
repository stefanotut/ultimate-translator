#!/bin/bash
# Crea la macchina che terra' online il portale, gratis e per sempre.
#
# E' scritto per essere rilanciato quante volte serve: ogni pezzo (rete,
# gateway, sottorete, macchina) viene creato solo se non esiste gia'. Se si
# ferma a meta' — e con Oracle capita — si rilancia e riprende da dov'era.
#
# LA STRATEGIA, e perche' e' questa
# ---------------------------------
# Le macchine ARM gratuite sono contese e Oracle risponde "Out of host
# capacity" anche per giorni. La tentazione e' martellare la creazione: e'
# esattamente la cosa sbagliata, perche' l'API di creazione ha un limite di
# frequenza pensato apposta per scoraggiarlo. Chi martella non trova capacita'
# prima: si prende dei 429 e quelle richieste non arrivano nemmeno al controllo
# della capacita'. Cioe' si fanno meno tentativi utili, non di piu'.
#
# Quindi qui si fa il contrario: si CHIEDE a Oracle se c'e' posto, con
# `compute-capacity-report`, che e' una lettura e non consuma il limite delle
# creazioni, e si prova a creare solo quando la risposta e' AVAILABLE.
#
# Tre errori che avevo fatto e che sono corretti qui dentro:
#
#   Ruotare i fault domain a mano. Sembra allargare la ricerca e invece la
#   restringe: se fissi il dominio, Oracle cerca solo li'. Il testo stesso
#   dell'errore di Oracle dice di NON specificarlo, cosi' lo scheduler sceglie
#   fra tutti quelli con posto. In piu' triplicava le richieste, da cui il 429.
#
#   Trattare un timeout di rete come un errore fatale. Un singhiozzo di rete
#   non dice nulla sulla capacita': va riprovato. Prima ammazzava il ciclo dopo
#   pochi minuti, che e' il modo peggiore di fallire: sembra che stia ancora
#   provando, e invece e' fermo.
#
#   Chiedere 100 GB di disco. Non c'entra con la capacita' (sono risorse
#   diverse) ma si mangiava meta' dei 200 GB gratuiti per niente.
set -euo pipefail

NOME=traduttore
CIDR_RETE=10.0.0.0/16
CIDR_SOTTORETE=10.0.0.0/24
OCPU=2
MEMORIA_GB=6          # non 12: vedi DEPLOY.md, le macchine quasi vuote Oracle se le riprende
DISCO_GB=50           # il default; il minimo e' 47. Lascia spazio nei 200 GB gratuiti
CHIAVE_PUBBLICA=~/.ssh/id_ed25519.pub
TENTATIVI=${TENTATIVI:-2000}      # a 60s l'uno: giorni, non ore. Serviranno.
ATTESA=60                          # fra un SONDAGGIO e l'altro (lettura, non creazione)
ATTESA_429=600                     # tetto quando Oracle ci strozza

dimmi() { printf '\n\033[1m%s\033[0m\n' "$*"; }
nota()  { printf '%s %s\n' "$(date +%H:%M:%S)" "$*" >> .vm_log; }

[ -f "$CHIAVE_PUBBLICA" ] || { echo "Manca $CHIAVE_PUBBLICA"; exit 1; }

# Ogni chiamata di preparazione passa da qui: i 401 transitori (la chiave appena
# creata viene propagata un servizio alla volta) e i singhiozzi di rete si
# riprovano; un errore vero no, ritentarlo sarebbe solo aspettare per niente.
occ() {
    local n out
    for n in $(seq 1 12); do
        if out=$(oci "$@" 2>&1); then printf '%s' "$out"; return 0; fi
        case "$out" in
            *NotAuthenticated*|*"Service error:Unauthorized"*|\
            *RequestException*|*"timed out"*|*ConnectTimeout*)
                printf '  (riprovo %d/12)\n' "$n" >&2; sleep 10 ;;
            *)  printf '%s' "$out" >&2; return 1 ;;
        esac
    done
    printf '%s' "$out" >&2; return 1
}

C=$(grep '^tenancy=' ~/.oci/config | cut -d= -f2)
dimmi "Compartimento: $C"

# --- rete -------------------------------------------------------------------
VCN=$(occ network vcn list --compartment-id "$C" --all \
        --query "data[?\"display-name\"=='$NOME-rete' && \"lifecycle-state\"!='TERMINATED'].id | [0]" \
        --raw-output | grep -o 'ocid1\.vcn\.[^ "]*' | head -1 || true)
if [ -z "$VCN" ]; then
    dimmi "Creo la rete"
    VCN=$(occ network vcn create --compartment-id "$C" --display-name "$NOME-rete" \
            --cidr-blocks "[\"$CIDR_RETE\"]" --dns-label ${NOME}rete \
            --wait-for-state AVAILABLE --query 'data.id' --raw-output \
          | grep -o 'ocid1\.vcn\.[^ "]*' | head -1)
fi
echo "  rete: $VCN"

IG=$(occ network internet-gateway list --compartment-id "$C" --vcn-id "$VCN" --all \
       --query "data[0].id" --raw-output | grep -o 'ocid1\.internetgateway\.[^ "]*' | head -1 || true)
if [ -z "$IG" ]; then
    dimmi "Creo il gateway verso internet"
    IG=$(occ network internet-gateway create --compartment-id "$C" --vcn-id "$VCN" \
           --is-enabled true --display-name "$NOME-gateway" \
           --wait-for-state AVAILABLE --query 'data.id' --raw-output \
         | grep -o 'ocid1\.internetgateway\.[^ "]*' | head -1)
fi
echo "  gateway: $IG"

RT=$(occ network vcn get --vcn-id "$VCN" --query 'data."default-route-table-id"' --raw-output | grep -o 'ocid1\.routetable\.[^ "]*' | head -1)
occ network route-table update --rt-id "$RT" --force \
    --route-rules "[{\"destination\":\"0.0.0.0/0\",\"destinationType\":\"CIDR_BLOCK\",\"networkEntityId\":\"$IG\"}]" \
    >/dev/null
echo "  uscita verso internet: pronta"

# Si aprono tre porte e basta: 22 per entrare, 80 e 443 per il portale.
# La 80 non serve al sito ma al certificato: Let's Encrypt verifica di li'.
# Il portale resta comunque chiuso a chiave, l'ingresso e' l'OTP via email.
SL=$(occ network vcn get --vcn-id "$VCN" --query 'data."default-security-list-id"' --raw-output | grep -o 'ocid1\.securitylist\.[^ "]*' | head -1)
occ network security-list update --security-list-id "$SL" --force \
  --ingress-security-rules '[
    {"protocol":"6","source":"0.0.0.0/0","isStateless":false,
     "tcpOptions":{"destinationPortRange":{"min":22,"max":22}}},
    {"protocol":"6","source":"0.0.0.0/0","isStateless":false,
     "tcpOptions":{"destinationPortRange":{"min":80,"max":80}}},
    {"protocol":"6","source":"0.0.0.0/0","isStateless":false,
     "tcpOptions":{"destinationPortRange":{"min":443,"max":443}}}]' \
  --egress-security-rules '[{"protocol":"all","destination":"0.0.0.0/0","isStateless":false}]' \
  >/dev/null
echo "  regole: 22, 80, 443 in ingresso"

SUB=$(occ network subnet list --compartment-id "$C" --vcn-id "$VCN" --all \
        --query "data[?\"display-name\"=='$NOME-sottorete' && \"lifecycle-state\"!='TERMINATED'].id | [0]" \
        --raw-output | grep -o 'ocid1\.subnet\.[^ "]*' | head -1 || true)
if [ -z "$SUB" ]; then
    dimmi "Creo la sottorete"
    SUB=$(occ network subnet create --compartment-id "$C" --vcn-id "$VCN" \
            --display-name "$NOME-sottorete" --cidr-block "$CIDR_SOTTORETE" \
            --dns-label ${NOME}sub --prohibit-public-ip-on-vnic false \
            --wait-for-state AVAILABLE --query 'data.id' --raw-output \
          | grep -o 'ocid1\.subnet\.[^ "]*' | head -1)
fi
echo "  sottorete: $SUB"

IMG=$(occ compute image list --compartment-id "$C" \
        --operating-system "Canonical Ubuntu" --operating-system-version "24.04" \
        --shape VM.Standard.A1.Flex --sort-by TIMECREATED \
        --query 'data[0].id' --raw-output | grep -o 'ocid1\.image\.[^ "]*' | head -1)
[ -n "$IMG" ] || { echo "Nessuna immagine Ubuntu 24.04 ARM"; exit 1; }
dimmi "Immagine Ubuntu 24.04 ARM trovata"

AD=$(occ iam availability-domain list --compartment-id "$C" \
       --query 'data[0].name' --raw-output | tr -d '[]", ' | grep -v '^$' | head -1)
echo "  zona: $AD"

# --- macchina ---------------------------------------------------------------
GIA=$(occ compute instance list --compartment-id "$C" --all \
        --query "data[?\"display-name\"=='$NOME' && \"lifecycle-state\"!='TERMINATED'].id | [0]" \
        --raw-output | grep -o 'ocid1\.instance\.[^ "]*' | head -1 || true)

if [ -n "$GIA" ]; then
    dimmi "La macchina esiste gia'"
else
    # La scaletta: prima quella che si vuole, poi quella che si prende. Una da
    # 1 CPU si piazza molto piu' facilmente e il portale ci gira lo stesso (la
    # voce va circa la meta', non e' ferma). Meglio online a meta' potenza che
    # non online.
    cat > /tmp/forme_$$.json <<EOF
[{"instanceShape":"VM.Standard.A1.Flex","instanceShapeConfig":{"ocpus":$OCPU,"memoryInGBs":$MEMORIA_GB}},
 {"instanceShape":"VM.Standard.A1.Flex","instanceShapeConfig":{"ocpus":1,"memoryInGBs":6}}]
EOF
    trap 'rm -f /tmp/forme_$$.json' EXIT

    dimmi "Aspetto che si liberi una macchina (${OCPU}CPU/${MEMORIA_GB}GB, o 1CPU/6GB)"
    echo "Sondo ogni ${ATTESA}s con l'API di capacita' — e' una lettura, non"
    echo "consuma il limite delle creazioni. Provo a creare solo quando c'e'"
    echo "davvero posto. Progresso leggibile in .vm_log"
    nota "inizio: sondaggio ogni ${ATTESA}s"

    attesa=$ATTESA
    for n in $(seq 1 "$TENTATIVI"); do
        # Se una richiesta e' andata a buon fine ma la risposta si e' persa, la
        # macchina esiste gia': senza questo controllo se ne creerebbe una
        # seconda e si sforerebbe il gratuito.
        NATA=$(oci compute instance list --compartment-id "$C" --all \
                 --query "data[?\"display-name\"=='$NOME' && \"lifecycle-state\"!='TERMINATED'].id | [0]" \
                 --raw-output 2>/dev/null | grep -o 'ocid1\.instance\.[^ "]*' | head -1 || true)
        if [ -n "$NATA" ]; then
            GIA=$NATA; dimmi "La macchina c'e'"; break
        fi

        # 1) c'e' posto? (lettura)
        # Il filtro lo fa Oracle, non io: avevo scritto un parser a grep e non
        # riconosceva le risposte positive, cioe' avrebbe aspettato per sempre
        # anche con la macchina libera davanti. Qui si chiede direttamente
        # l'elenco delle sole taglie disponibili.
        REP=$(oci compute compute-capacity-report create --compartment-id "$C" \
                --availability-domain "$AD" --shape-availabilities file:///tmp/forme_$$.json \
                --query 'data."shape-availabilities"[?"availability-status"==`AVAILABLE`]."instance-shape-config".ocpus' \
                --raw-output 2>&1 || true)

        case "$REP" in
            *TooManyRequests*|*"429"*)
                attesa=$(( attesa * 2 )); [ "$attesa" -gt "$ATTESA_429" ] && attesa=$ATTESA_429
                nota "strozzati: rallento a ${attesa}s"; sleep "$attesa"; continue ;;
            *NotAuthenticated*|*RequestException*|*"timed out"*|*"Connection"*)
                nota "rete ballerina, riprovo"; sleep 20; continue ;;
        esac
        attesa=$ATTESA        # e' andata: si torna al ritmo normale

        # La risposta e' un elenco JSON di numeri: `[]` se non c'e' niente,
        # `[2.0]` o `[2.0, 1.0]` se c'e' posto.
        LIBERE=()
        while IFS= read -r o; do
            [ -n "$o" ] && LIBERE+=("$o")
        done < <(printf '%s' "$REP" | tr -d '[] "' | tr ',' '\n' | grep -E '^[0-9]+(\.[0-9]+)?$')

        # Il report NON e' un verdetto: e' stato smentito dai fatti. Diceva
        # OUT_OF_HOST_CAPACITY per la macchina AMD e quella macchina, lanciata
        # davvero, e' nata al primo colpo. Quindi quando dice "niente posto" si
        # prova lo stesso, ma di rado: un tentativo vero ogni 5 minuti, che e'
        # il ritmo sotto il quale scatta lo strozzatore.
        if [ ${#LIBERE[@]} -eq 0 ]; then
            [ $(( n % 10 )) -eq 1 ] && nota "giro $n: il report dice niente posto"
            if [ $(( n % 5 )) -ne 0 ]; then sleep "$attesa"; continue; fi
            nota "giro $n: provo lo stesso, il report si sbaglia"
            LIBERE=("$OCPU" "1")
        fi

        # 2) c'e' posto davvero: si prova a prenderla, subito.
        for cpu_f in "${LIBERE[@]}"; do
            cpu=${cpu_f%%.*}; [ "$cpu" = "$OCPU" ] && ram=$MEMORIA_GB || ram=6
            nota "POSTO LIBERO ${cpu}CPU/${ram}GB: provo a prenderla"
            # NIENTE --fault-domain: fissarlo restringe la ricerca a quello.
            # Il testo stesso dell'errore di Oracle dice di non specificarlo.
            if OUT=$(oci compute instance launch \
                    --compartment-id "$C" --availability-domain "$AD" \
                    --display-name "$NOME" --shape VM.Standard.A1.Flex \
                    --shape-config "{\"ocpus\":$cpu,\"memoryInGBs\":$ram}" \
                    --image-id "$IMG" --subnet-id "$SUB" --assign-public-ip true \
                    --boot-volume-size-in-gbs "$DISCO_GB" \
                    --metadata "{\"ssh_authorized_keys\":\"$(cat $CHIAVE_PUBBLICA)\"}" \
                    --query 'data.id' --raw-output 2>&1); then
                GIA=$(printf '%s' "$OUT" | grep -o 'ocid1\.instance\.[^ "]*' | head -1)
                [ -n "$GIA" ] || { echo "$OUT"; exit 1; }
                dimmi "PRESA: ${cpu} CPU / ${ram} GB"
                nota "PRESA: ${cpu}CPU/${ram}GB dopo $n giri"
                break 2
            fi
            case "$OUT" in
                *"Out of host capacity"*|*"OutOfCapacity"*)
                    nota "sfumata: qualcuno l'ha presa prima" ;;
                *TooManyRequests*|*"429"*)
                    attesa=$ATTESA_429; nota "strozzati sulla creazione" ;;
                *RequestException*|*"timed out"*|*"Connection"*|*"ServiceUnavailable"*|\
                *"InternalError"*|*"502"*|*"503"*|*"504"*)
                    nota "rete ballerina durante la creazione" ;;
                *"LimitExceeded"*|*"quota"*)
                    echo "$OUT"
                    echo "Hai gia' usato le risorse gratuite (dal 15/06/2026 il"
                    echo "tetto e' 2 OCPU / 12 GB in tutto): spegni altre macchine."
                    exit 1 ;;
                # Solo qui si muore: un errore che non so interpretare va
                # guardato, non ripetuto per giorni.
                *)  nota "ERRORE NUOVO, mi fermo:"; printf '%s\n' "$OUT" >> .vm_log
                    echo "$OUT"; exit 1 ;;
            esac
        done
        sleep "$attesa"
    done
fi

[ -n "${GIA:-}" ] || { echo "Non sono riuscito a creare la macchina."; exit 1; }

dimmi "Aspetto che si accenda"
# `oci compute instance get` NON accetta --wait-for-state (lo accettano solo
# altri comandi): si aspetta a mano, altrimenti lo script muore proprio qui,
# con la macchina appena presa.
for _ in $(seq 1 60); do
    ST=$(occ compute instance get --instance-id "$GIA" \
           --query 'data."lifecycle-state"' --raw-output | tr -dc 'A-Z')
    [ "$ST" = "RUNNING" ] && break
    sleep 10
done

IP=$(occ compute instance list-vnics --instance-id "$GIA" \
       --query 'data[0]."public-ip"' --raw-output | tr -d '"' | tail -1)
dimmi "ACCESA — indirizzo: $IP"
nota "ACCESA: $IP"
echo "Entra con:  ssh ubuntu@$IP"
# NON si scrive in .vm_ip: li' c'e' l'indirizzo della macchina che tiene il
# portale online adesso, e sovrascriverlo manderebbe il prossimo deploy sulla
# macchina sbagliata. Questa e' la ARM, arrivata dopo: si annuncia a parte.
printf '%s\n' "$IP" > .vm_ip_arm
echo
echo "Questa e' la macchina ARM (CPU vera): e' quella dove va la VOCE."
echo "Il portale sta sull'altra e li' resta finche' non lo si sposta apposta."
