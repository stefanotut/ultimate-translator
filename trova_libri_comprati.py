#!/usr/bin/env python3
"""
ULTIMATE TRANSLATOR - Trova sul Mac i libri di profumeria che hai e li carica nella libreria

Cerca i PDF e gli EPUB dei 437 libri della lista di profumeria in Download,
Documenti, Scrivania, iCloud Drive, Libri, la libreria di Calibre, Dropbox,
Google Drive e OneDrive (e nelle cartelle che indichi), li riconosce dal nome
del file o dai dati scritti dentro il file (titolo e autore), anche con il
titolo dell'edizione italiana, e li carica nella libreria, ognuno nella sua
cartella: Profumeria/Materie prime, Profumeria/Storia e profumi antichi, ...

I file originali non vengono toccati: in ~/Downloads/Libri di profumeria
trovati si preparano dei collegamenti (o delle copie) e import_books.py, che
deve stare nella stessa cartella di questo script, li carica. I libri gia
nella libreria vengono saltati, quindi si puo' rilanciare quando si vuole,
per esempio dopo aver scaricato altri acquisti dal tuo account dell'editore.

Esempi:
    python3 trova_libri_comprati.py                          # cerca e carica (chiede il codice libreria)
    python3 trova_libri_comprati.py --solo-cerca             # mostra cosa trova, senza caricare
    python3 trova_libri_comprati.py "/Volumes/Disco/Libri"   # cerca anche in questa cartella

Serve solo la libreria standard di Python.
"""

import os
import re
import sys
import json
import shutil
import zipfile
import argparse
import subprocess
import unicodedata
from difflib import SequenceMatcher

DEFAULT_SERVER = 'https://89-168-31-56.nip.io'
HOME = os.path.expanduser('~')
STAGING = os.path.join(HOME, 'Downloads', 'Libri di profumeria trovati')
MARKER = '.ultimate-translator'
ROOTS = ['Downloads', 'Documents', 'Desktop', 'Books', 'Libri', 'Calibre Library', 'Dropbox', 'Google Drive',
         'OneDrive', 'Library/CloudStorage', 'Library/Mobile Documents/com~apple~CloudDocs',
         'Library/Mobile Documents/iCloud~com~apple~iBooks/Documents']
SUPPORTED = ('.pdf', '.epub')
SKIPPED_DIRS = {'node_modules', '__pycache__', 'venv', 'site-packages', '__MACOSX', '$RECYCLE.BIN',
                'System Volume Information'}
MIN_SIZE = 20 * 1024
SF_DATALESS = 0x40000000           # file di iCloud non ancora scaricato: niente lettura dei metadati
CATEGORIES = {1: 'Materie prime', 2: 'Singole materie prime', 3: 'Storia e profumi antichi',
              4: 'Maison, vintage e flaconi', 5: 'Profumieri', 6: 'Tecnica e composizione',
              7: "Scienza e cultura dell'olfatto", 8: 'Libri italiani', 9: 'Romanzi'}

# [categoria, [titolo originale, titolo dell'edizione italiana...], autore, anno]: la lista di profumeria
CATALOGUE = json.loads(r'''[[1,["Odorographia: A Natural History of Raw Materials and Drugs Used in the Perfume Industry"],"John Charles Sawer","1892"],[1,["Die ätherischen Öle"],"Eduard Gildemeister, Friedrich Hoffmann","1899"],[1,["The Chemistry of Essential Oils and Artificial Perfumes"],"Ernest J. Parry","1899"],[1,["Les huiles essentielles et leurs principaux constituants"],"Eugène Charabot, Justin Dupont, Léon Pillet","1899"],[1,["Les parfums artificiels"],"Eugène Charabot","1900"],[1,["Die Riechstoffe"],"Georg Cohn","1904"],[1,["Perfumes, Cosmetics and Soaps"],"William A. Poucher","1923"],[1,["Parry's Cyclopaedia of Perfumery"],"Ernest J. Parry","1925"],[1,["Les parfums naturels: essences concrètes, résinoïdes, huiles et pommades aux fleurs"],"Yves-René Naves, Gabriel Mazuyer","1939"],[1,["The Essential Oils"],"Ernest Guenther","1948"],[1,["The Givaudan Index: Specifications of Synthetics and Isolates for Perfumery"],"Givaudan-Delawanna","1949"],[1,["Perfumery Synthetics and Isolates"],"Paul Z. Bedoukian","1951"],[1,["Perfume and Flavor Materials of Natural Origin"],"Steffen Arctander","1960"],[1,["Perfume and Flavor Chemicals (Aroma Chemicals)"],"Steffen Arctander","1969"],[1,["Fenaroli's Handbook of Flavor Ingredients"],"Thomas E. Furia, Nicolò Bellanca (dalle opere di Giovanni Fenaroli)","1971"],[1,["Technologie et chimie des parfums naturels"],"Yves-René Naves","1974"],[1,["Monographs on Fragrance Raw Materials"],"D. L. J. Opdyke (a cura di)","1979"],[1,["Essential Oils"],"Brian M. Lawrence","1979"],[1,["Cultivation and Utilization of Aromatic Plants"],"C. K. Atal, B. M. Kapur (a cura di)","1982"],[1,["Fragrance Chemistry: The Science of the Sense of Smell"],"Ernest T. Theimer (a cura di)","1982"],[1,["Common Fragrance and Flavor Materials"],"Kurt Bauer, Dorothea Garbe, Horst Surburg, Johannes Panten","1985"],[1,["Riechstoffe und Geruchssinn: Die molekulare Welt der Düfte"],"Günther Ohloff","1990"],[1,["The Encyclopedia of Essential Oils","Enciclopedia degli oli essenziali"],"Julia Lawless","1992"],[1,["Душистые вещества и другие продукты для парфюмерии"],"Л. А. Хейфиц, В. М. Дашунин","1994"],[1,["Essential Oil Safety"],"Robert Tisserand, Tony Balacs","1995"],[1,["Identification of Essential Oil Components by Gas Chromatography/Mass Spectrometry"],"Robert P. Adams","1995"],[1,["Flavours and Fragrances of Plant Origin"],"J. J. W. Coppen","1995"],[1,["合成香料"],"印藤元一","1996"],[1,["Essential Oil Crops"],"E. A. Weiss","1997"],[1,["Fragrances: Beneficial and Adverse Effects"],"Peter J. Frosch, Jeanne Duus Johansen, Ian R. White (a cura di)","1998"],[1,["The Chemistry of Fragrances"],"David H. Pybus, Charles S. Sell","1999"],[1,["Terpene: Aromen, Düfte, Pharmaka, Pheromone"],"Eberhard Breitmaier","1999"],[1,["Plant Resources of South-East Asia No. 19: Essential-oil Plants"],"L. P. A. Oyen, Nguyen Xuan Dung (a cura di)","1999"],[1,["375 Essential Oils and Hydrosols"],"Jeanne Rose","1999"],[1,["最新 香料の事典"],"荒井綜一 et al. (a cura di)","2000"],[1,["Natural Aromatic Materials: Odours & Origins"],"Tony Burfield","2000"],[1,["Advances in Flavours and Fragrances: From the Sensation to the Synthesis"],"Karl A. D. Swift (a cura di)","2002"],[1,["Chemistry and Technology of Flavors and Fragrances"],"David J. Rowe (a cura di)","2005"],[1,["Perspectives in Flavor and Fragrance Research"],"Philip Kraft, Karl A. D. Swift (a cura di)","2005"],[1,["Natural Flavors and Fragrances: Chemistry, Analysis, and Production"],"Carl Frey, Russell Rouseff (a cura di)","2005"],[1,["香りの百科事典"],"谷田貝光克 et al. (a cura di)","2005"],[1,["Meaningful Scents Around the World"],"Roman Kaiser","2006"],[1,["Flavours and Fragrances: Chemistry, Bioprocessing and Sustainability"],"Ralf G. Berger (a cura di)","2007"],[1,["Understanding Fragrance Chemistry"],"Charles S. Sell","2008"],[1,["Current Topics in Flavor and Fragrance Research"],"Philip Kraft, Karl A. D. Swift (a cura di)","2008"],[1,["Extraction Technologies for Medicinal and Aromatic Plants"],"S. S. Handa, S. P. S. Khanuja, G. Longo, D. D. Rakesh (a cura di)","2008"],[1,["Handbook of Essential Oils: Science, Technology, and Applications"],"K. Hüsnü Can Başer, Gerhard Buchbauer (a cura di)","2010"],[1,["Scent of the Vanishing Flora"],"Roman Kaiser","2011"],[1,["Scent and Chemistry: The Molecular World of Odors"],"Günther Ohloff, Wilhelm Pickenhagen, Philip Kraft","2011"],[1,["La chimie des huiles essentielles: tradition et innovation"],"Farid Chemat, Xavier Fernandez (a cura di)","2012"],[1,["香料の科学"],"長谷川香料株式会社","2013"],[1,["Chemistry and the Sense of Smell"],"Charles S. Sell","2014"],[1,["Harvest to Hydrosol"],"Ann Harman","2015"],[1,["Fundamentals of Fragrance Chemistry"],"Charles S. Sell","2019"],[1,["Atlas de botanique parfumée","Atlante di botanica profumata"],"Jean-Claude Ellena","2020"],[1,["De la plante à l'essence. Un tour du monde des matières à parfum"],"Collectif Nez","2021"],[1,["Scent: A Natural History of Fragrance"],"Elise Vernon Pearlstine","2022"],[1,["Atlas de botanique parfumée. Les complémentaires"],"Jean-Claude Ellena","2025"],[1,["Botanique olfactive: sentir la nature au fil des saisons"],"autore non accertato","2025"],[2,["Floating Gold: A Natural (and Unnatural) History of Ambergris"],"Christopher Kemp","2012"],[2,["Historia moschi"],"Lucas Schröck","1682"],[2,["On the Scent: Conserving Musk Deer - The Uses of Musk and Europe's Role in Its Trade"],"Volker Homes","1999"],[2,["Scent from the Garden of Paradise: Musk and the Medieval Islamic World"],"Anya H. King","2017"],[2,["Castorologia explicans castoris animalis naturam et usum medico-chemicum"],"Johannes Marius, Johann Franck","1685"],[2,["Agarwood: Science Behind the Fragrance"],"Rozi Mohamed (a cura di)","2016"],[2,["Oud in Perfumery"],"Collectif Nez","s.d."],[2,["They Came for Sandalwood"],"Dorothy Shineberg","1967"],[2,["Le santal en parfumerie"],"Collectif Nez","s.d."],[2,["Indian Sandalwood: A Compendium"],"A. N. Arunkumar et al. (a cura di)","2022"],[2,["Vetiveria: The Genus Vetiveria"],"Massimo Maffei (a cura di)","2002"],[2,["Le Vétiver en parfumerie"],"Collectif Nez","s.d."],[2,["Le Patchouli en parfumerie"],"Collectif Nez","s.d."],[2,["Frankincense and Myrrh: A Study of the Arabian Incense Trade"],"Nigel Groom","1981"],[2,["Weihrauch und Myrrhe: Kulturgeschichte und wirtschaftliche Bedeutung"],"Dieter Martinetz, Karlheinz Lohs, Jörg Janzen","1988"],[2,["Frankincense & Myrrh: Through the Ages"],"Martin Watt, Wanda Sellar","1996"],[2,["Monograph on Benzoin (Balsamic Resin from Styrax Species)"],"Masakazu Kashio, Dennis V. Johnson (a cura di)","2001"],[2,["The Land of Incense"],"Juris Zarins","2001"],[2,["Frankincense: Oman's Gift to the World"],"Juliet Highet","2006"],[2,["Food for the Gods: New Light on the Ancient Incense Trade"],"David Peacock, David Williams (a cura di)","2007"],[2,["Biology of Genus Boswellia"],"Ahmed Al-Harrasi et al. (a cura di)","2019"],[2,["A History of the Use of Incense in Divine Worship"],"E. G. Cuthbert F. Atchley","1909"],[2,["香道 歴史と文学"],"三條西公正","1971"],[2,["The Book of Incense: Enjoying the Traditional Art of Japanese Scents"],"Kiyoko Morita","1992"],[2,["The Trail of Time: Shih-chien ti tsu-chi - Time Measurement with Incense in East Asia"],"Silvio A. Bedini","1994"],[2,["香と日本人"],"稲坂良弘","1999"],[2,["Kodo: The Way of Incense"],"David Pybus","2001"],[2,["香道の歴史事典"],"神保博行","2003"],[2,["Incense: Rituals, Mystery, Lore"],"Gina Hyams","2004"],[2,["中国香文化"],"傅京亮","2008"],[2,["香の文化史 日本における沈香需要の歴史"],"松原睦","2012"],[2,["Rhodologia, seu philosophico-medica generosae rosae descriptio"],"Johann Karl Rosenberg","1628"],[2,["Rhodologia"],"John Charles Sawer","1894"],[2,["The Rose: A True History"],"Jennifer Potter","2010"],[2,["La Rose de Damas en parfumerie"],"Collectif Nez","2019"],[2,["Le jasmin grandiflorum en parfumerie"],"Collectif Nez","2023"],[2,["Tuberose in Perfumery"],"Collectif Nez","s.d."],[2,["La Fleur d'oranger en parfumerie"],"Collectif Nez","2020"],[2,["Narcissus"],"Collectif Nez","2019"],[2,["Narcissus and Daffodil"],"Gordon R. Hanks (a cura di)","2002"],[2,["L'Iris en parfumerie"],"Collectif Nez","s.d."],[2,["Violets: The History and Cultivation of Scented Violets"],"Roy E. Coombs","1981"],[2,["Geranium and Pelargonium"],"Maria Lis-Balchin (a cura di)","2002"],[2,["The Scent of Orchids"],"Roman Kaiser","1993"],[2,["Lavender: The Genus Lavandula"],"Maria Lis-Balchin (a cura di)","2002"],[2,["The Genus Lavandula"],"Tim Upson, Susyn Andrews","2004"],[2,["Distiller la lavande à Barrême"],"autore non accertato","s.d."],[2,["Citrus: The Genus Citrus"],"Giovanni Dugo, Angelo Di Giacomo (a cura di)","2002"],[2,["Citrus: A History"],"Pierre Laszlo","2007"],[2,["Citrus Oils: Composition, Advanced Analytical Techniques, Contaminants, and Biological Activity"],"Giovanni Dugo, Luigi Mondello (a cura di)","2011"],[2,["Citrus bergamia: Bergamot and its Derivatives"],"Giovanni Dugo, Ivana Bonaccorsi (a cura di)","2013"],[2,["The Land Where Lemons Grow"],"Helena Attlee","2014"],[2,["Vanilla: Travels in Search of the Ice Cream Orchid"],"Tim Ecott","2004"],[2,["Vanilla: The Cultural History of the World's Favorite Flavor and Fragrance"],"Patricia Rain","2004"],[2,["Vanilla"],"Eric Odoux, Michel Grisoni (a cura di)","2010"],[2,["Vanilla Orchids: Natural History and Cultivation"],"Ken Cameron","2011"],[2,["Handbook of Vanilla Science and Technology"],"Daphna Havkin-Frenkel, Faith C. Belanger (a cura di)","2011"],[2,["Crocologia"],"Johann Ferdinand Hertodt","1671"],[2,["Saffron: Crocus sativus L."],"Moshe Negbi (a cura di)","1999"],[2,["Secrets of Saffron: The Vagabond Life of the World's Most Seductive Spice"],"Pat Willard","2001"],[2,["Black Pepper: Piper nigrum"],"P. N. Ravindran (a cura di)","2000"],[2,["Cardamom"],"P. N. Ravindran, K. J. Madhusoodanan (a cura di)","2002"],[2,["Cinnamon and Cassia: The Genus Cinnamomum"],"P. N. Ravindran, K. Nirmal Babu, M. Shylaja (a cura di)","2004"],[2,["Ginger"],"P. N. Ravindran, K. Nirmal Babu (a cura di)","2005"],[2,["Mint: The Genus Mentha"],"Brian M. Lawrence (a cura di)","2007"],[2,["Essential Oil-Bearing Grasses: The Genus Cymbopogon"],"Anand Akhila (a cura di)","2010"],[2,["The Scents of Eden: A Narrative of the Spice Trade"],"Charles Corn","1998"],[2,["Nathaniel's Nutmeg","L'isola della noce moscata"],"Giles Milton","1999"],[2,["Dangerous Tastes: The Story of Spices"],"Andrew Dalby","2000"],[2,["Spice: The History of a Temptation"],"Jack Turner","2004"],[2,["The Spice Route: A History"],"John Keay","2005"],[2,["Pepper: A History of the World's Most Influential Spice"],"Marjorie Shaffer","2013"],[2,["The Scent Trail"],"Celia Lyttelton","2007"],[2,["Fragrant: The Secret Life of Scent"],"Mandy Aftel","2014"],[2,["Cueilleur d'essences. Aux sources des parfums du monde","Il cercatore di essenze. Viaggio alle origini del profumo"],"Dominique Roques","2021"],[3,["Parfümrezepte und kultische Texte aus Assur"],"Erich Ebeling","1950"],[3,["Περὶ ὀσμῶν (De odoribus)"],"Teofrasto","c. 300 a.C."],[3,["Naturalis historia","Storia naturale"],"Plinio il Vecchio","c. 77 d.C."],[3,["De materia medica"],"Dioscoride","I sec. d.C."],[3,["Kitāb Kīmiyāʾ al-ʿiṭr wa-t-taṣʿīdāt"],"al-Kindī","IX sec."],[3,["香譜"],"洪芻 Hong Chu","XII sec."],[3,["Trotula"],"anonimo salernitano","XII sec."],[3,["陳氏香譜"],"陳敬 Chen Jing","XIII sec."],[3,["Liber de arte distillandi de simplicibus"],"Hieronymus Brunschwig","1500"],[3,["Thesaurus Euonymi Philiatri de remediis secretis"],"Conrad Gessner","1552"],[3,["Magiae naturalis"],"Giovan Battista Della Porta","1558"],[3,["De distillatione libri IX"],"Giovan Battista Della Porta","1608"],[3,["香乘"],"周嘉冑 Zhou Jiazhou","XVII sec."],[3,["Le Parfumeur françois"],"Simon Barbe","1693"],[3,["Le Parfumeur royal"],"Simon Barbe","1699"],[3,["Traité raisonné de la distillation"],"Antoine Hornot (Déjean)","1753"],[3,["Chimie du goût et de l'odorat"],"Polycarpe Poncelet","1755"],[3,["Traité des odeurs, suite du Traité de la distillation"],"Antoine Hornot (Déjean)","1764"],[3,["Toilette de Flore"],"Pierre-Joseph Buc'hoz","1771"],[3,["L'Art du parfumeur, ou Traité complet de la préparation des parfums"],"D. J. Fargeon","1801"],[3,["The British Perfumer"],"Charles Lillie","1822"],[3,["Manuel du parfumeur"],"Madame Gacon-Dufour","1825"],[3,["The Art of Perfumery, and Method of Obtaining the Odors of Plants"],"George William Septimus Piesse","1855"],[3,["The Book of Perfumes"],"Eugène Rimmel","1865"],[3,["The Toilet and Cosmetic Arts in Ancient and Modern Times"],"Arnold J. Cooley","1866"],[3,["Die Parfumerie-Fabrikation"],"George William Askinson","1876"],[3,["Die Fabrikation der ätherischen Oele"],"George William Askinson","1876"],[3,["A Short History of the Art of Distillation"],"R. J. Forbes","1948"],[3,["Ancient Egyptian Materials and Industries"],"Alfred Lucas","1926"],[3,["The Mystery and Lure of Perfume"],"C. J. S. Thompson","1927"],[3,["Studies in Ancient Technology, vol. III"],"R. J. Forbes","1955"],[3,["Chemistry and Chemical Technology in Ancient Mesopotamia"],"Martin Levey","1959"],[3,["Studies in Indian Cultural History"],"P. K. Gode","1961"],[3,["The Golden Peaches of Samarkand: A Study of T'ang Exotics"],"Edward H. Schafer","1963"],[3,["A History of Scent"],"Roy Genders","1972"],[3,["Les Jardins d'Adonis. La mythologie des aromates en Grèce","I giardini di Adone. I miti della seduzione erotica"],"Marcel Detienne","1972"],[3,["Fragrance: The Story of Perfume from Cleopatra to Chanel"],"Edwin T. Morris","1984"],[3,["Incense in Ancient Israel"],"Kjeld Nielsen","1986"],[3,["Parfums et aromates de l'Antiquité"],"Paul Faure","1987"],[3,["Histoire du parfum","Storia del profumo"],"Ghislaine Pillivuyt","1988"],[3,["An Ancient Egyptian Herbal"],"Lise Manniche","1989"],[3,["Odeurs de sainteté. La mythologie chrétienne des aromates"],"Jean-Pierre Albert","1990"],[3,["Irdische Düfte - himmlische Lust. Eine Kulturgeschichte der Duftstoffe"],"Günther Ohloff","1992"],[3,["Historia del perfume: aromas de leyenda, creaciones de lujo"],"Rosa González","1994"],[3,["Science and the Secrets of Nature: Books of Secrets in Medieval and Early Modern Culture"],"William Eamon","1994"],[3,["Le Livre du parfum"],"Élisabeth Barillé, Catherine Laroze","1995"],[3,["Duft. Kulturgeschichte des Parfums"],"Comité Français du Parfum","1996"],[3,["El fascinante mundo de los perfumes"],"AA.VV.","1996"],[3,["Perfume: The Art and Science of Scent"],"Cathy Newman","1998"],[3,["Sacred Luxuries: Fragrance, Aromatherapy, and Cosmetics in Ancient Egypt"],"Lise Manniche","1999"],[3,["Scents of Time"],"Edwin T. Morris","1999"],[3,["Le Parfum à travers les siècles","Storia dei profumi. Dagli dèi dell'Olimpo al cyber-profumo"],"Brigitte Munier","2003"],[3,["Le Parfum, des origines à nos jours"],"Annick Le Guérer","2005"],[3,["Parfum. Eine sinnliche Kulturgeschichte"],"Renate Lohse-Jasper","2005"],[3,["Perfume: Joy, Obsession, Scandal, Sin"],"Richard Stamelman","2006"],[3,["Scenting Salvation: Ancient Christianity and the Olfactory Imagination"],"Susan Ashbrook Harvey","2006"],[3,["Une histoire mondiale du parfum"],"Marie-Christine Grasse et al.","2007"],[3,["The Essence of Perfume"],"Roja Dove","2008"],[3,["Parfums de l'Antiquité. La rose et l'encens en Méditerranée"],"Annie Verbanck-Piérard, Natacha Massar, Dominique Frère (a cura di)","2008"],[3,["The Ephemeral History of Perfume: Scent and Sense in Early Modern England"],"Holly Dugan","2011"],[3,["Les Parfums: histoire, anthologie, dictionnaire"],"Élisabeth de Feydeau","2011"],[3,["The Aroma of Righteousness: Scent and Seduction in Rabbinic Life and Literature"],"Deborah A. Green","2011"],[3,["Sandalwood and Carrion: Smell in Indian Religion and Culture"],"James McHugh","2012"],[3,["Les Huiles parfumées en Méditerranée occidentale et en Gaule"],"Dominique Frère, Laurent Hugot (a cura di)","2012"],[3,["L'Herbier de Marie-Antoinette"],"Élisabeth de Feydeau, Alain Baraton","2012"],[3,["La Fabrique des parfums. Naissance d'une industrie de luxe"],"Eugénie Briot","2015"],[3,["Smell and the Ancient Senses"],"Mark Bradley (a cura di)","2015"],[3,["Grasse: l'usine à parfums"],"AA.VV. (Ville d'art et d'histoire de Grasse, Inventaire général)","2015"],[3,["Daughters of Alchemy: Women and Scientific Culture in Early Modern Italy"],"Meredith K. Ray","2015"],[3,["Sacred Scents in Early Christianity and Islam"],"Mary Thurlkill","2016"],[3,["Scents and Sensibility: Perfume in Victorian Literary Culture"],"Catherine Maxwell","2017"],[3,["Historia del perfume. Relatos olfativos del pasado"],"Clara Buedo","2024"],[3,["Les Voluptueuses. Une histoire parfumée des femmes de légende"],"Élisabeth de Feydeau","2025"],[3,["Der parfümierte Mann"],"Paul Divjak","s.d."],[3,["Erotik des Parfums. Geschichte und Praxis der schönen Düfte"],"Andrea Hurton","s.d."],[3,["Parfümpflanzen. Eine kleine Geschichte der Parfümerie"],"Serge Schall","s.d."],[4,["Parfums: Le Guide"],"Luca Turin","1992"],[4,["The Perfume Handbook"],"Nigel Groom","1992"],[4,["Fabulous Fragrances"],"Jan Moran","1994"],[4,["Perfume: The Creation and Allure of Classic Fragrances"],"Susan Irvine","1995"],[4,["Perfume Legends: French Feminine Fragrances"],"Michael Edwards","1996"],[4,["The Perfume Guide"],"Susan Irvine","2000"],[4,["Perfumes: The Guide"],"Luca Turin, Tania Sanchez","2008"],[4,["The Little Book of Perfumes"],"Luca Turin, Tania Sanchez","2011"],[4,["The Perfume Bible"],"Josephine Fairley, Lorna McKay","2014"],[4,["Perfumes: The Guide 2018"],"Luca Turin, Tania Sanchez","2018"],[4,["Perfume: In Search of Your Signature Scent"],"Neil Chapman","2019"],[4,["Dictionnaire amoureux du parfum"],"Élisabeth de Feydeau","2021"],[4,["Coty: parfumeur et visionnaire"],"Élisabeth Barillé","1995"],[4,["Lancôme"],"Jacqueline Demornex","2000"],[4,["Chanel Perfume"],"Françoise Aveline","2003"],[4,["Lanvin"],"Dean L. Merceron","2007"],[4,["Paul Poiret and His Rosine Perfumes"],"Christie Mayer Lefkowith","2007"],[4,["The Secret of Chanel No. 5: The Intimate History of the World's Most Famous Perfume"],"Tilar J. Mazzeo","2010"],[4,["On Perfume Making"],"Frédéric Malle","2011"],[4,["N°5 Culture Chanel"],"Jean-Louis Froment","2013"],[4,["Jean Patou: une vie sur mesure"],"Emmanuelle Polle","2013"],[4,["Dior: The Perfumes"],"Chandler Burr","2014"],[4,["Perfume: A Century of Scents"],"Lizzie Ostrom","2015"],[4,["Bulgari: The Perfume of Gems"],"autore non accertato","2018"],[4,["Der Duft der Imperien. Chanel No 5 und Rotes Moskau","Il profumo degli imperi. Chanel N° 5 e Rosso Mosca"],"Karl Schlögel","2020"],[4,["Éditions de Parfums Frédéric Malle: The First Twenty Years"],"Frédéric Malle","2020"],[4,["Miss Dior: A Story of Courage and Couture"],"Justine Picardie","2021"],[4,["Eau de Cologne: Farina 1709"],"Andrea Dalmus","2022"],[4,["Une histoire de parfums: 1880–2020"],"Yohan Cervi","2022"],[4,["Lancôme Paris: Domaine de la Rose"],"autore non accertato","2023"],[4,["Louis Vuitton: A Perfume Atlas"],"Jacques Cavallier-Belletrud, Lionel Paillès","2024"],[4,["Fragonard: The Perfume of Provence"],"Alain Stella","2026"],[4,["Guerlain"],"Élisabeth Barillé","s.d."],[4,["Guerlain: An Imperial Icon"],"autore non accertato","s.d."],[4,["Paris, Capital of Guerlain"],"autore non accertato","s.d."],[4,["Robertet"],"Élisabeth de Feydeau","s.d."],[4,["34H"],"Diptyque","s.d."],[4,["Cologne - Wiege der Eau de Cologne"],"M. Eckstein","s.d."],[4,["Baccarat: Les Flacons à Parfum / The Perfume Bottles"],"Cristalleries de Baccarat","1986"],[4,["Perfume and Scent Bottle Collecting"],"Jean Sloan","1986"],[4,["Perfume, Cologne, and Scent Bottles"],"Jacquelyne Y. Jones-North","1986"],[4,["Commercial Perfume Bottles"],"Jacquelyne Y. Jones-North","1987"],[4,["René Lalique 1860–1945, maître-verrier: analyse de l'œuvre et catalogue raisonné"],"Félix Marcilhac","1989"],[4,["Lalique Perfume Bottles"],"Mary Lou Utt, Glenn Utt, Patricia Bayer","1990"],[4,["Czechoslovakian Perfume Bottles and Boudoir Accessories"],"Jacquelyne Y. Jones-North","1990"],[4,["Commercial Fragrance Bottles"],"Joanne Dubbs Ball, Dorothy Hehl Torem","1993"],[4,["The Art of Perfume: Discovering and Collecting Perfume Bottles"],"Christie Mayer Lefkowith","1994"],[4,["Miniature Perfume Bottles"],"Glinda Bowman","1994"],[4,["Fragrance Bottle Masterpieces"],"Joanne Dubbs Ball, Dorothy Hehl Torem","1996"],[4,["More Miniature Perfume Bottles"],"Glinda Bowman","1996"],[4,["L'image publicitaire des parfums"],"Mariette Julien","1997"],[4,["Perfume Presentation: 100 Years of Artistry"],"Ken Leach","1997"],[4,["Secrets of Aromatic Jewelry"],"Annette Green, Linda Dyett","1998"],[4,["Miller's Perfume Bottles: A Collector's Guide"],"Madeleine Marsh","1999"],[4,["Masterpieces of the Perfume Industry"],"Christie Mayer Lefkowith","2000"],[4,["Collectible Miniature Perfume Bottles"],"Anne Breton","2001"],[4,["Scent and Subversion: Decoding a Century of Provocative Perfume"],"Barbara Herman","2013"],[4,["From the Belle Époque to the Roaring Twenties"],"autore non accertato","2016"],[4,["Le parfum s'affiche: quand les artistes réinventent la publicité"],"AA.VV. (Musée International de la Parfumerie)","2023"],[4,["Vintage Perfumes"],"Jan Moran","s.d."],[5,["Souvenirs et parfums: mémoires d'un parfumeur"],"Constantin Weriguine","1965"],[5,["L'Intimité du parfum"],"Edmond Roudnitska, Odile Moréno, René Bourdon","1974"],[5,["L'Esthétique en question: introduction à une esthétique de l'odorat"],"Edmond Roudnitska","1977"],[5,["Le Parfum"],"Edmond Roudnitska","1980"],[5,["Une vie au service du parfum"],"Edmond Roudnitska","1991"],[5,["Serge Lutens"],"Serge Lutens","1998"],[5,["Les Sens du parfum","I sensi e l'essenza del profumo"],"Guy Robert","2000"],[5,["Les Routes de mes parfums"],"Jean-Paul Guerlain","2002"],[5,["Le Parfum"],"Jean-Claude Ellena","2007"],[5,["Parfums d'amour"],"Jean-Paul Guerlain","2010"],[5,["Journal d'un parfumeur","Viaggio sentimentale tra i profumi del mondo. Il diario di un profumiere d'eccellenza"],"Jean-Claude Ellena","2011"],[5,["Berlin à Paris"],"Serge Lutens","2012"],[5,["Jo Malone: My Story"],"Jo Malone","2016"],[5,["L'Écrivain d'odeurs"],"Jean-Claude Ellena","2017"],[5,["Aphorismes d'un parfumeur"],"Dominique Ropion, Marie-Bénédicte Gauthier","2017"],[5,["In Sensorium: Notes for My People"],"Tanaïs","2022"],[5,["L'odeur des jours: une vie de parfumeur"],"autore non accertato","2023"],[5,["The Sense of Scent"],"Mathilde Laurent","s.d."],[5,["Estée: A Success Story"],"Estée Lauder","1985"],[5,["The Emperor of Scent: A Story of Perfume, Obsession, and the Last Mystery of the Senses"],"Chandler Burr","2003"],[5,["Jean-Louis Fargeon, parfumeur de Marie-Antoinette"],"Élisabeth de Feydeau","2004"],[5,["The Perfect Scent: A Year Inside the Perfume Industry in Paris and New York"],"Chandler Burr","2008"],[5,["François Coty: Fragrance, Power, Money"],"Roulhac B. Toledano, Elizabeth Z. Coty","2009"],[5,["The Perfume Lover: A Personal History of Scent"],"Denyse Beaulieu","2012"],[5,["Coming to My Senses: A Story of Perfume, Pleasure, and an Unlikely Bride"],"Alyssa Harad","2012"],[5,["Sentir, ressentir"],"Collectif Nez","2019"],[5,["The Ghost Perfumer: Creed, Lies, & the Scent of the Century"],"Gabe Oppenheim","2022"],[5,["Germaine Cellier: l'audace d'une parfumeuse"],"Béatrice Égémar, Sandrine Revel","2023"],[5,["Le Roman des Guerlain, parfumeurs de Paris"],"Élisabeth de Feydeau","s.d."],[6,["Handbuch der gesamten Parfumerie und Kosmetik"],"Fritz Winter","1927"],[6,["The Science and Art of Perfumery"],"Edward Sagarin","1945"],[6,["Die psychologischen Grundlagen der Parfümerie"],"Paul Jellinek","1951"],[6,["The Practice of Modern Perfumery"],"Paul Jellinek","1954"],[6,["Handbuch der Kosmetika und Riechstoffe"],"Hermann Janistyn","1969"],[6,["Perfumery Technology: Art, Science, Industry"],"Marcel Billot, F. V. Wells","1975"],[6,["The Use of Fragrance in Consumer Products"],"J. Stephan Jellinek","1975"],[6,["Perfumes: Art, Science and Technology"],"Peter M. Müller, Dietmar Lamparsky (a cura di)","1991"],[6,["Flower Oils and Floral Compounds in Perfumery"],"Danute Pajaujis Anonis","1993"],[6,["Perfumery: Practice and Principles"],"Robert R. Calkin, J. Stephan Jellinek","1994"],[6,["Introduction to Perfumery"],"Tony Curtis, David G. Williams","1994"],[6,["香料と調香の基礎知識"],"中島基貴","1995"],[6,["Perfumery: Techniques in Evolution"],"Arcadi Boix Camps","2000"],[6,["Brand Sense"],"Martin Lindstrom","2005"],[6,["Fragrance for Personal Care"],"AA.VV. (Allured)","2008"],[6,["Sensory Marketing: Research on the Sensuality of Products"],"Aradhna Krishna (a cura di)","2010"],[6,["Perfume Engineering: Design, Performance and Classification"],"Miguel A. Teixeira, Oscar Rodríguez, Paula Gomes, Vera Mata, Alírio E. Rodrigues","2013"],[6,["Le Grand Livre du parfum. Pour une culture olfactive"],"Collectif Nez","2018"],[6,["Designing with Smell: Practices, Techniques and Challenges"],"Victoria Henshaw et al. (a cura di)","2018"],[6,["The Formulation and Preparation of Cosmetics, Fragrances and Flavors"],"Louis Appell","s.d."],[6,["Fragrance Applications: A Survival Guide"],"Stephen J. Herman","s.d."],[6,["The Perfumer's Handbook"],"Experimental Perfume Club","s.d."],[6,["The Fragrance Manual"],"Michael Edwards","1984"],[6,["Classification des parfums et terminologie"],"Société Française des Parfumeurs","1984"],[6,["The H&R Book: Fragrance Guide - Feminine Notes"],"Haarmann & Reimer","1984"],[6,["The H&R Book: Fragrance Guide - Masculine Notes"],"Haarmann & Reimer","1985"],[6,["Atlas of Odor Character Profiles"],"Andrew Dravnieks","1985"],[6,["Sensory Evaluation Techniques"],"Morten Meilgaard, Gail Vance Civille, B. Thomas Carr","1987"],[6,["Perfumes, Splashes & Colognes"],"Nancy M. Booth","1997"],[6,["Essence and Alchemy","Essenze e alchimia. Il libro dei profumi"],"Mandy Aftel","2001"],[6,["Aroma: The Magic of Essential Oils in Food & Fragrance"],"Mandy Aftel, Daniel Patterson","2004"],[6,["Scents & Sensibilities: Creating Solid Perfumes for Well-Being"],"Mandy Aftel","2005"],[6,["Listening to Scent: An Olfactory Journey with Aromatic Plants and Their Extracts"],"Jennifer Peace Rhind","2014"],[6,["Perfume: The Art and Craft of Fragrance"],"Karen Gilbert","2014"],[6,["Homemade Perfume"],"Anya McCoy","2018"],[6,["Foundations of Natural Perfumery"],"Ayala Moriel","s.d."],[7,["The Subtle Sense"],"Ralph Bienfang","1946"],[7,["The Sense of Smell"],"Roy Bedichek","1960"],[7,["The Perception of Odors"],"Trygg Engen","1982"],[7,["Perfumery: The Psychology and Biology of Fragrance"],"Steve Van Toller, George H. Dodd (a cura di)","1988"],[7,["A Natural History of the Senses"],"Diane Ackerman","1990"],[7,["Odor Sensation and Memory"],"Trygg Engen","1991"],[7,["Fragrance: The Psychology and Biology of Perfume"],"Steve Van Toller, George H. Dodd (a cura di)","1992"],[7,["Verborgen verleider: psychologie van de reuk","Il seduttore segreto. Psicologia dell'olfatto"],"Piet Vroon, Anton van Amerongen, Hans de Vries","1994"],[7,["Handbook of Olfaction and Gustation"],"Richard L. Doty (a cura di)","1995"],[7,["Jacobson's Organ and the Remarkable Nature of Smell"],"Lyall Watson","1999"],[7,["Éloge de l'odorat"],"André Holley","1999"],[7,["Learning to Smell: Olfactory Perception from Neurobiology to Behavior"],"Donald A. Wilson, Richard J. Stevenson","2006"],[7,["The Secret of Scent: Adventures in Perfume and the Science of Smell"],"Luca Turin","2006"],[7,["The Scent of Desire: Discovering Our Enigmatic Sense of Smell"],"Rachel Herz","2007"],[7,["What the Nose Knows: The Science of Scent in Everyday Life"],"Avery Gilbert","2008"],[7,["Das Maiglöckchen-Phänomen"],"Hanns Hatt, Regine Dee","2008"],[7,["Neurogastronomy: How the Brain Creates Flavor and Why It Matters"],"Gordon M. Shepherd","2012"],[7,["Olfactory Cognition"],"Gesualdo M. Zucco, Rachel S. Herz, Benoist Schaal (a cura di)","2012"],[7,["Odorat et goût: de la neurobiologie des sens chimiques aux applications"],"Roland Salesse, Rémi Gervais (a cura di)","2012"],[7,["Fragrance and Wellbeing: Plant Aromatics and Their Influence on the Psyche"],"Jennifer Peace Rhind","2013"],[7,["Springer Handbook of Odor"],"Andrea Buettner (a cura di)","2017"],[7,["Nose Dive: A Field Guide to the World's Smells"],"Harold McGee","2020"],[7,["Smellosophy: What the Nose Tells the Mind"],"A. S. Barwich","2020"],[7,["Smell: A Very Short Introduction"],"Matthew Cobb","2020"],[7,["Alles Geruchssache"],"Bettina M. Pause","2020"],[7,["Sensehacking"],"Charles Spence","2021"],[7,["Revelations in Air: A Guidebook to Smell"],"Jude Stewart","2021"],[7,["L'arte di vivere annusando"],"Bill S. Hansson","s.d."],[7,["Le Miasme et la jonquille. L'odorat et l'imaginaire social, XVIIIe-XIXe siècles","Storia sociale degli odori. XVIII e XIX secolo"],"Alain Corbin","1982"],[7,["Le Propre et le sale. L'hygiène du corps depuis le Moyen Âge","Lo sporco e il pulito"],"Georges Vigarello","1985"],[7,["Les Pouvoirs de l'odeur","I poteri dell'odore"],"Annick Le Guérer","1988"],[7,["Aroma: The Cultural History of Smell"],"Constance Classen, David Howes, Anthony Synnott","1994"],[7,["Tasten, Riechen, Schmecken: Eine Ästhetik der anästhesierten Sinne"],"Mădălina Diaconu","2005"],[7,["The Smell Culture Reader"],"Jim Drobnick (a cura di)","2006"],[7,["Sensing the Past: Seeing, Hearing, Smelling, Tasting, and Touching in History"],"Mark M. Smith","2007"],[7,["Philosophie de l'odorat"],"Chantal Jaquet","2010"],[7,["Parfums"],"Philippe Claudel","2012"],[7,["Past Scents: Historical Perspectives on Smell"],"Jonathan Reinarz","2014"],[7,["L'Art olfactif contemporain"],"Chantal Jaquet (a cura di)","2015"],[7,["La Civilisation des odeurs (XVIe-début XIXe siècle)"],"Robert Muchembled","2017"],[7,["Smell and History: A Reader"],"Mark M. Smith (a cura di)","2018"],[7,["Smell in Eighteenth-Century England: A Social Sense"],"William Tullett","2019"],[7,["Art Scents: Exploring the Aesthetics of Smell and the Olfactory Arts"],"Larry Shiner","2020"],[7,["Perfume"],"Megan Volpert","2021"],[7,["Smell and the Past: Noses, Archives, Narratives"],"William Tullett","2023"],[7,["Scents and Sound: An Unexpected Association"],"Annick Le Guérer, Bruno Fourn","s.d."],[8,["Nuovo receptario"],"Collegio dei medici di Firenze","1499"],[8,["Experimenti de la Ex.ma S.r Caterina da Furlj"],"Caterina Sforza","c. 1500"],[8,["Notandissimi secreti de l'arte profumatoria"],"Giovanventura Rosetti","1555"],[8,["Secreti del reverendo donno Alessio Piemontese"],"Alessio Piemontese (attr. Girolamo Ruscelli)","1555"],[8,["I secreti de la signora Isabella Cortese"],"Isabella Cortese","1561"],[8,["Gli ornamenti delle donne"],"Giovanni Marinello","1562"],[8,["Manuale del profumiere"],"Antonio Rossi","1902"],[8,["L'arte dei profumi"],"Luigi Giannelli","s.d."],[8,["Aromi, profumi e balsami naturali"],"Patrizia Garzena, Marina Tadiello","s.d."],[8,["Crea la tua essenza"],"autore non accertato","s.d."],[8,["Alla ricerca dei cosmetici perduti"],"Paolo Rovesti","1975"],[8,["Alla ricerca della cosmesi dei popoli primitivi"],"Paolo Rovesti","1976"],[8,["Alla ricerca dei profumi perduti"],"Paolo Rovesti","1980"],[8,["Le officine dei sensi"],"Piero Camporesi","1985"],[8,["Un agrume molto colto: il bergamotto, mito e storia"],"Carlo Mangiola, Giuseppe Polimeni (a cura di)","1997"],[8,["Profumi d'Arabia"],"Alessandra Avanzini (a cura di)","1997"],[8,["Aromatica. Essenze, profumi e spezie tra Oriente e Occidente"],"D'Amore, Pennestri","2003"],[8,["I profumi di Afrodite e il segreto dell'olio"],"Maria Rosaria Belgiorno","2007"],[8,["Il profumo nel mondo antico. Con la prima traduzione italiana del «Sugli odori» di Teofrasto"],"Giuseppe Squillace","2010"],[8,["I giardini di Saffo. Profumi e aromi nella Grecia antica"],"Giuseppe Squillace","2014"],[8,["Le lacrime di Mirra. Miti e luoghi dei profumi nel mondo antico"],"Giuseppe Squillace","2015"],[8,["I balsami di Afrodite"],"Giuseppe Squillace","2015"],[8,["All'inizio era il profumo. Storia personale, e universale, dei profumi"],"Aldo Nove","2016"],[8,["Gli inganni di Cleopatra. Fonti per lo studio dei profumi antichi"],"Giuseppe Squillace","2022"],[8,["Profumi ed essenze alla corte dei califfi"],"Caterina Greppi","2023"],[8,["Per fumum. Profumi e odori tra storia e letteratura"],"AA.VV.","2023"],[8,["Viaggio nella storia del profumo. Collezione Storp"],"Marco Vidal","2025"],[8,["Il profumo dell'anima"],"Lorenzo Nucci","2025"],[8,["Il profumo"],"Cristina Penco","2025"],[8,["Il profumo degli dèi. Fragranze del Mediterraneo antico"],"Silvia Ferrara, Laura Bellinato","2026"],[8,["Storia del Bergamotto di Reggio Calabria"],"P. Amato","s.d."],[8,["Naso d'autore. L'odorato nella cultura e nella storia letteraria italiana"],"autore non accertato","s.d."],[8,["A lume di naso"],"AA.VV.","s.d."],[8,["La farmacia di Santa Maria Novella"],"S. Giovannini, G. Mancini","1987"],[8,["Officina Profumo-Farmaceutica di Santa Maria Novella"],"autore non accertato","1990"],[8,["L'Officina Profumo-farmaceutica di Santa Maria Novella in Firenze: sette secoli di storia e di arte"],"G. Mancini (a cura di)","1994"],[8,["Il profumo. Cultura, storia e tecniche"],"Lorenzo Villoresi","1995"],[8,["L'arte del bagno. Una guida al più sensuale rito del corpo"],"Lorenzo Villoresi","2001"],[8,["Leggere il profumo"],"Ornella Pastorelli, Silvio Levi","2005"],[8,["Collezione Magnani. I flaconi"],"Chiara Squarcina, Monica Magnani (a cura di)","2018"],[8,["Il senso perfetto. Mai sottovalutare il naso"],"Anna D'Errico","2019"],[8,["Manuale della grande profumeria italiana. Cinquant'anni di essenze straordinarie"],"Marika Vecchiattini","2020"],[8,["Profumi. Un viaggio attorno al mondo degli odori, della profumeria, della memoria"],"Paola Bottai","2021"],[8,["Profumo di niente. Perdere l'olfatto e riscoprire i propri sensi"],"Anna D'Errico","2021"],[8,["La formula del sentire. Atlante psico-emotivo del mondo delle fragranze"],"Marco Martello","2024"],[9,["À rebours","Controcorrente"],"Joris-Karl Huysmans","1884"],[9,["Jitterbug Perfume"],"Tom Robbins","1984"],[9,["Das Parfum. Die Geschichte eines Mörders","Il profumo"],"Patrick Süskind","1985"],[9,["源氏物語","La storia di Genji"],"Murasaki Shikibu","inizio XI sec."],[9,["The Scent of Possibility"],"Sarah McCartney","s.d."],[9,["Aphrodesia"],"John Oehler","s.d."],[9,["Farina - Der Parfümeur von Köln"],"Ina Knobloch","s.d."],[9,["Fragrancia"],"Paul Richardot","s.d."],[9,["The Perfumer"],"Jan Moran","2020"],[9,["Aqua mirabilis. La straordinaria storia dell'invenzione del profumo"],"Eleonora Recalcati","2024"],[9,["Il sentiero dei profumi"],"Cristina Caboni","s.d."]]''')

ARTICLES = {'the', 'a', 'an', 'le', 'la', 'les', 'l', 'un', 'une', 'il', 'lo', 'gli', 'i', 'uno', 'una', 'der',
            'die', 'das', 'ein', 'eine', 'el', 'los', 'las', 'de', 'du', 'des', 'of', 'di', 'del', 'della',
            'and', 'et', 'e', 'y', 'und', 'en', 'in', 'on', 'to', 'for', 'per', 'con', 'with'}
ANONYMOUS = ('aa.vv', 'aa. vv', 'autore non accertato', 'anonimo', 'collectif', 'comite', 'comité')
SITE_JUNK = re.compile(r'\((?:[^()]*(?:z-lib|1lib|z-library|annas-archive|libgen|b-ok|booksc)[^()]*)\)', re.I)


# ---------------------------------------------------------------------------
# Testo
# ---------------------------------------------------------------------------

def norm(text):
    text = unicodedata.normalize('NFKD', str(text or ''))
    text = ''.join(c for c in text if not unicodedata.combining(c)).casefold()
    text = text.replace('&', ' and ').replace('œ', 'oe').replace('æ', 'ae').replace('ß', 'ss')
    return ' '.join(re.sub(r'[_\W]+', ' ', text).split())


def words(text):
    return tuple(w for w in norm(text).split() if w not in ARTICLES and not w.isdigit())


def main_title(title):
    """'Titolo: sottotitolo' / 'Titolo. Sottotitolo' / 'Titolo (ed.)' -> 'Titolo'"""
    return re.split(r'\s*[:.;]\s+|\s+[-–—]\s+|\s*\(', str(title or ''), maxsplit=1)[0]


def surnames(author):
    """'Robert Tisserand, Tony Balacs' -> {'tisserand', 'balacs'}; niente per le opere collettive."""
    if any(mark in norm(author) or mark in str(author).casefold() for mark in ANONYMOUS):
        return set()
    found = set()
    for person in re.split(r'\s*(?:,|;|&|\(|\)|\bet al\b|\be\b|\band\b|\bet\b|\bund\b|\by\b)\s*', author or ''):
        person_words = [w for w in norm(person).split() if len(w) > 2 and w not in ('cura', 'eds', 'ed')]
        if person_words:
            found.add(person_words[-1])
    return found


def clean_stem(filename):
    """Il nome del file senza quello che aggiungono i siti e i programmi."""
    stem = os.path.splitext(os.path.basename(filename))[0]
    stem = SITE_JUNK.sub(' ', stem)
    stem = re.sub(r"\b(?:anna.s archive|z-library|libgen(?:\.\w+)?)\b", ' ', stem, flags=re.I)
    stem = re.sub(r'\b[0-9a-f]{32}\b', ' ', stem, flags=re.I)                 # md5
    stem = re.sub(r'\b(?:97[89][-\d]{10,14}|\d{9}[\dXx])\b', ' ', stem)       # ISBN
    stem = re.sub(r'_tradotto_\w+$', '', stem, flags=re.I)
    return re.sub(r'\s{2,}', ' ', stem.replace('_', ' ')).strip(' -.')


def name_variants(filename):
    """'Autore - Titolo' e 'Titolo -- Autore -- Editore, anno': ogni pezzo puo' essere il titolo."""
    stem = clean_stem(filename)
    parts = [p for p in re.split(r'\s+-{1,2}\s+|\s+[–—]\s+|_-_', stem) if p.strip()]
    return [stem] + parts + [main_title(p) for p in parts]


# ---------------------------------------------------------------------------
# Dati scritti dentro i file
# ---------------------------------------------------------------------------

def _pdf_string(raw):
    if raw.startswith(b'<'):
        try:
            raw = bytes.fromhex(re.sub(rb'[^0-9A-Fa-f]', b'', raw[1:-1]).decode())
        except ValueError:
            return ''
    else:
        raw = raw[1:-1]
        raw = re.sub(rb'\\([0-7]{1,3})', lambda m: bytes([int(m.group(1), 8) & 255]), raw)
        raw = re.sub(rb'\\(.)', lambda m: {b'n': b'\n', b'r': b'\r', b't': b'\t'}.get(m.group(1), m.group(1)), raw)
    if raw.startswith(b'\xfe\xff'):
        return raw[2:].decode('utf-16-be', 'replace')
    return raw.decode('latin-1', 'replace')


def pdf_metadata(path):
    """Titolo e autore dal dizionario Info o dall'XMP, se non sono compressi (spesso non lo sono)."""
    found = []
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as fh:
            chunks = [fh.read(512 * 1024)]
            if size > 1024 * 1024:
                fh.seek(size - 512 * 1024)
                chunks.append(fh.read())
    except OSError:
        return found
    for data in chunks:
        for key in (rb'/Title', rb'/Author'):
            for match in re.finditer(key + rb'\s*(\((?:\\.|[^\\)])*\)|<[0-9A-Fa-f\s]*>)', data):
                value = _pdf_string(match.group(1)).strip()
                if value:
                    found.append(value)
        for tag in (rb'dc:title', rb'dc:creator'):
            for match in re.finditer(rb'<' + tag + rb'>.*?<rdf:li[^>]*>(.*?)</rdf:li>', data, re.S):
                found.append(match.group(1).decode('utf-8', 'replace').strip())
    return found[:8]


def epub_metadata(path):
    try:
        with zipfile.ZipFile(path) as archive:
            container = archive.read('META-INF/container.xml').decode('utf-8', 'replace')
            opf_path = re.search(r'full-path="([^"]+)"', container).group(1)
            opf = archive.read(opf_path).decode('utf-8', 'replace')
    except (OSError, KeyError, AttributeError, zipfile.BadZipFile, RuntimeError):
        return []
    return [re.sub(r'<[^>]+>', '', m).strip() for m in re.findall(r'<dc:(?:title|creator)[^>]*>(.*?)</dc:', opf, re.S)]


def metadata(path):
    try:
        if getattr(os.stat(path), 'st_flags', 0) & SF_DATALESS:
            return []
    except OSError:
        return []
    return epub_metadata(path) if path.lower().endswith('.epub') else pdf_metadata(path)


# ---------------------------------------------------------------------------
# Riconoscimento
# ---------------------------------------------------------------------------

def prepare_catalogue(catalogue=None):
    entries = []
    for index, (category, titles, author, year) in enumerate(catalogue or CATALOGUE):
        variants = []
        for title_index, title in enumerate(titles):
            for variant in (words(title), words(main_title(title))):
                if variant and (variant, title_index) not in variants:
                    variants.append((variant, title_index))
        entries.append({'index': index, 'category': category, 'titles': titles, 'author': author,
                        'year': year, 'variants': variants, 'surnames': surnames(author)})
    return entries


def word_index(entries):
    """parola -> voci che la contengono: per ogni file si confrontano solo i libri che c'entrano."""
    index = {}
    for entry in entries:
        for variant, _ in entry['variants']:
            for word in variant:
                index.setdefault(word, set()).add(entry['index'])
        for surname in entry['surnames']:
            index.setdefault(surname, set()).add(entry['index'])
    return index


def score(entry, haves, file_words):
    """Quanto un file somiglia a un libro della lista (0 = per niente), e con quale dei suoi titoli."""
    same_author = bool(entry['surnames'] & file_words)
    best = (0.0, None)
    for want, title_index in entry['variants']:
        joined_want = ' '.join(want)
        for have in haves:
            if len(set(want) & set(have)) * 2 < len(want):
                continue                      # meno di meta' delle parole in comune: non e' lui
            joined_have = ' '.join(have)
            if len(want) == 1:
                # "Perfume", "Le Parfum": un titolo di una parola sola vale solo con l'autore
                value = 0.8 if (want == have and same_author and len(want[0]) >= 5) else 0.0
            elif want == have:
                value = 1.0 if (len(want) >= 3 or same_author) else 0.0
            else:
                ratio = SequenceMatcher(None, joined_want, joined_have).ratio()
                contained = f' {joined_want} ' in f' {joined_have} '
                value = 0.0
                if ratio >= 0.92 and (len(want) >= 3 or same_author):
                    value = ratio
                elif ratio >= 0.8 and same_author:
                    value = ratio * 0.95
                elif contained and len(want) >= 4:
                    value = 0.9
                elif contained and (len(want) == 3 or same_author):
                    value = 0.86 if same_author else 0.82
            if value > best[0]:
                best = (value, title_index)
    return best


def _best(entries, index, texts):
    haves = {words(t) for t in texts} | {words(main_title(t)) for t in texts}
    haves.discard(())
    file_words = set(words(' '.join(texts)))
    candidates = set()
    for word in file_words:
        candidates |= index.get(word, set())
    best = None
    for position in sorted(candidates):
        entry = entries[position]
        value, title_index = score(entry, haves, file_words)
        if value and (best is None or value > best[1]):
            best = (entry, value, title_index)
    return best


def identify(path, entries, index=None):
    """(voce della lista, punteggio, indice del titolo) per un file, o None.
    Prima il nome del file; i dati scritti dentro il file si leggono solo se il nome non basta."""
    index = index if index is not None else word_index(entries)
    texts = name_variants(path)
    best = _best(entries, index, texts)
    if best is None or best[1] < 0.9:
        extra = metadata(path)
        if extra:
            with_metadata = _best(entries, index, texts + extra)
            if with_metadata and (best is None or with_metadata[1] > best[1]):
                best = with_metadata
    return best


def book_files(roots, skip=()):
    skip = {os.path.realpath(s) for s in skip}
    seen = set()
    for root in roots:
        if not os.path.isdir(root):
            continue
        for folder, dirs, files in os.walk(root):
            real = os.path.realpath(folder)
            # la cartella del programma (i suoi dati sono gia nella libreria) e quella di questo script
            if real in skip or ('app.py' in files and 'import_books.py' in files):
                dirs[:] = []
                continue
            dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in SKIPPED_DIRS)
            for name in sorted(files):
                path = os.path.join(folder, name)
                if name.startswith('.') or not name.lower().endswith(SUPPORTED):
                    continue
                try:
                    key = os.path.realpath(path)
                    if key in seen or os.path.getsize(path) < MIN_SIZE:
                        continue
                except OSError:
                    continue
                seen.add(key)
                yield path


def find_books(roots, entries=None, skip=()):
    """{(indice della voce, indice del titolo, estensione): (punteggio, percorso, voce)}: il file migliore."""
    entries = entries or prepare_catalogue()
    index = word_index(entries)
    found = {}
    for path in book_files(roots, skip):
        match = identify(path, entries, index)
        if not match:
            continue
        entry, value, title_index = match
        key = (entry['index'], title_index, os.path.splitext(path)[1].lower())
        if key not in found or value > found[key][0]:
            found[key] = (value, path, entry)
    return found


# ---------------------------------------------------------------------------
# Preparazione e caricamento
# ---------------------------------------------------------------------------

def stage(found, staging):
    """Collegamenti (o copie) nella cartella di carico, divisi per categoria della lista."""
    if os.path.isdir(staging):
        if not os.path.exists(os.path.join(staging, MARKER)):
            sys.exit(f'La cartella {staging} esiste gia e non e\' stata creata da questo programma: '
                     f'spostala o rinominala e rilancia.')
        shutil.rmtree(staging)
    os.makedirs(staging)
    open(os.path.join(staging, MARKER), 'w').close()
    staged = []
    for value, path, entry in sorted(found.values(), key=lambda f: (f[2]['category'], f[2]['index'])):
        folder = os.path.join(staging, CATEGORIES[entry['category']])
        os.makedirs(folder, exist_ok=True)
        name = os.path.basename(path)
        target = os.path.join(folder, name)
        number = 2
        while os.path.exists(target):
            stem, ext = os.path.splitext(name)
            target = os.path.join(folder, f'{stem} ({number}){ext}')
            number += 1
        try:
            os.link(path, target)              # nessuno spazio in piu' sul disco
        except OSError:
            shutil.copy2(path, target)
        staged.append((entry, path, target))
    return staged


def find_importer():
    here = os.path.dirname(os.path.abspath(__file__))
    for folder in (here, os.getcwd()):
        path = os.path.join(folder, 'import_books.py')
        if os.path.isfile(path):
            return path
    return None


def main():
    parser = argparse.ArgumentParser(description='Trova sul Mac i libri di profumeria e li carica nella libreria.')
    parser.add_argument('cartelle', nargs='*', help='altre cartelle dove cercare (per esempio un disco esterno)')
    parser.add_argument('--cartella', default='Profumeria', help='cartella della libreria (default: Profumeria)')
    parser.add_argument('--codice', default=os.getenv('LIBRARY_CODE'), help='codice della libreria')
    parser.add_argument('--server', default=os.getenv('LIBRARY_SERVER') or DEFAULT_SERVER)
    parser.add_argument('--solo-cerca', action='store_true', help='mostra cosa trova, senza caricare')
    args = parser.parse_args()

    roots = [os.path.join(HOME, r) for r in ROOTS] + [os.path.abspath(os.path.expanduser(c)) for c in args.cartelle]
    print('ULTIMATE TRANSLATOR - Cerco sul Mac i libri della lista di profumeria...\n')
    entries = prepare_catalogue()
    found = find_books(roots, entries, skip=[STAGING])
    books = {entry['index'] for _, _, entry in found.values()}
    print(f'Trovati {len(found)} file di {len(books)} libri della lista (su {len(entries)}):')
    for value, path, entry in sorted(found.values(), key=lambda f: (f[2]['category'], f[2]['index'])):
        print(f"  [{CATEGORIES[entry['category']]}] {entry['titles'][0]} - {entry['author']}  <-  {path}")

    missing = [e for e in entries if e['index'] not in books]
    if args.solo_cerca or not found:
        if not found:
            print('Nessun libro della lista nelle cartelle del Mac.')
        return 0
    staged = stage(found, STAGING)
    with open(os.path.join(STAGING, 'Libri della lista non trovati sul Mac.txt'), 'w', encoding='utf-8') as fh:
        for entry in missing:
            fh.write(f"[{CATEGORIES[entry['category']]}] {entry['titles'][0]} - {entry['author']} - {entry['year']}\n")
    print(f'\nNon trovati sul Mac: {len(missing)} (elenco in "{STAGING}/Libri della lista non trovati sul Mac.txt")')

    importer = find_importer()
    if not importer:
        print(f'\nimport_books.py non e\' in questa cartella: trascina "{STAGING}" nella pagina Libreria del sito.')
        return 0
    print(f'\nCarico {len(staged)} file nella libreria, cartella "{args.cartella}"...\n')
    command = [sys.executable, importer, STAGING, '--cartella', args.cartella, '--server', args.server]
    if args.codice:
        command += ['--codice', args.codice]
    return subprocess.call(command)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit('\nInterrotto. Rilancia quando vuoi: i libri gia caricati vengono saltati.')
