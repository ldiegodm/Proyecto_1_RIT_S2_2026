"""Politicas de aranado expresadas como datos y funciones puras.

Se aislan aqui para que en la discusion de resultados se pueda senalar
un solo archivo como "donde viven las politicas de contenido", separado de
las politicas de cortesia (settings.py) y de frontera (spiders/paleo.py).
"""
import re

# ---------------------------------------------------------------------------
# P1. Politica de alcance tematico
# Vocabulario del tema, tomado de los terminos booleanos de la Actividad
# del 24/09: si un documento no contiene varios de estos terminos, no
# pertenece a la necesidad de informacion de PaleoBusca.
# ---------------------------------------------------------------------------
TERMINOS_TEMA = {
    # nucleo
    "dinosaur", "dinosaurs", "dinosauria", "paleontology", "palaeontology",
    "fossil", "fossils", "fossilization", "fossilisation", "prehistoric",
    # tiempo geologico
    "triassic", "jurassic", "cretaceous", "mesozoic", "paleogene",
    "stratigraphy", "radiometric", "geologic", "geological",
    # grupos
    "theropod", "sauropod", "ornithischian", "saurischian", "ceratopsian",
    "stegosaur", "ankylosaur", "hadrosaur", "pterosaur", "raptor",
    # generos frecuentes
    "tyrannosaurus", "triceratops", "stegosaurus", "velociraptor",
    "brachiosaurus", "diplodocus", "allosaurus", "spinosaurus", "iguanodon",
    # anatomia y evidencia
    "skeleton", "bone", "bones", "skull", "vertebra", "femur", "claw",
    "footprint", "trackway", "coprolite", "amber", "specimen", "excavation",
    "quarry", "sediment", "sedimentary", "extinction", "extinct",
    "herbivore", "carnivore", "predator", "feathered", "evolution",
}

# Pesos: los terminos nucleo valen doble al calcular el puntaje tematico.
TERMINOS_NUCLEO = {
    "dinosaur", "dinosaurs", "dinosauria", "fossil", "fossils",
    "paleontology", "palaeontology", "mesozoic",
}

_PALABRA = re.compile(r"[a-z]+(?:-[a-z]+)?")


def puntaje_tema(texto):
    """Variedad tematica: cuantos terminos distintos del tema hay en el texto.

    Heuristica booleana (presencia, no tf), coherente con el modelo visto en
    clase. Los terminos nucleo cuentan doble.
    """
    if not texto:
        return 0
    presentes = set(_PALABRA.findall(texto.lower())) & TERMINOS_TEMA
    return len(presentes) + len(presentes & TERMINOS_NUCLEO)


def densidad_tema(texto):
    """Densidad tematica: menciones del tema por cada 1000 palabras.

    Complementa a puntaje_tema y resuelve un falso positivo observado en las
    pruebas: paginas como "Our Gardens" o "Eat, drink and shop" de un museo
    nombran muchos terminos distintos en sus menus, pero el tema no es el
    asunto de la pagina. Exigir densidad obliga a que el texto hable de
    dinosaurios, no solo que los mencione.
    """
    palabras = _PALABRA.findall((texto or "").lower())
    if not palabras:
        return 0.0
    menciones = sum(1 for p in palabras if p in TERMINOS_TEMA)
    return 1000.0 * menciones / len(palabras)


# ---------------------------------------------------------------------------
# P2b. Politica de dominios vetados
#
# Dos listas, no una, por una leccion que costo documentos reales: comparar por
# SUBCADENA sobre el dominio produce falsos positivos absurdos. "x.com"
# (Twitter) es subcadena de "zootax.com.cn", una revista de paleontologia; con
# la lista unica se habian vetado 3 documentos validos de ahi.
#
#   HOSTS_VETADOS    -> se comparan como host completo o sufijo de dominio
#   PALABRAS_VETADAS -> se comparan contra las etiquetas del host (lo que hay
#                       entre puntos), que es donde "shop" o "tienda" tienen
#                       sentido: "nhmshop.co.uk", "shop.elsevier.com"
# ---------------------------------------------------------------------------
HOSTS_VETADOS = (
    # comercio y subastas
    "amazon.com", "amazon.co.uk", "amazon.de", "amzn.to", "ebay.com",
    "etsy.com", "alibaba.com", "aliexpress.com", "fossilmall.com",
    "sothebys.com", "christies.com", "happyhentoys.com",
    # bancos de imagenes
    "gettyimages.com", "shutterstock.com", "istockphoto.com", "alamy.com",
    "dreamstime.com",
    # redes sociales y contenido subido por usuarios
    "facebook.com", "twitter.com", "x.com", "instagram.com", "youtube.com",
    "youtu.be", "tiktok.com", "linkedin.com", "pinterest.com", "reddit.com",
    "flickr.com", "vimeo.com", "tumblr.com", "quora.com", "sites.google.com",
    "whatsapp.com", "api.whatsapp.com",
    # diccionarios y proyectos hermanos de wikipedia (no son articulos)
    "dictionary.com", "thesaurus.com", "wiktionary.org", "wikinews.org",
    "wikivoyage.org", "wikiversity.org", "wikiquote.org", "wikibooks.org",
    "wikidata.org",
    # espejos y cache: duplican texto que ya tenemos
    "web.archive.org", "archive.ph", "webcache.googleusercontent.com",
    # turismo y pagos
    "tripadvisor.com", "booking.com", "expedia.com", "eventbrite.com",
    "paypal.com", "doubleclick.net", "visit-dorset.com",
    # Servicios de mapas y buscadores. Hallazgo 01/10 18:20: todo articulo de
    # Wikipedia con coordenadas enlaza a geohack, y geohack reparte a una
    # docena de proveedores de mapas. Esas URLs llevan el nombre del articulo
    # ("?pagename=Dinosaur_Provincial_Park"), asi que pasan el filtro tematico
    # de forma legitima: 16% de las descargas se iban ahi.
    "geohack.toolforge.org", "toolforge.org", "wmflabs.org", "mapper.acme.com",
    "acme.com", "bing.com", "wego.here.com", "here.com", "mapquest.com",
    "openstreetmap.org", "wikimapia.org", "google.com", "duckduckgo.com",
    "yandex.com", "baidu.com", "geonames.org", "osmrm.openstreetmap.de",
)

# Hosts vetados SOLO como host exacto: son plataformas cuyos SUBDOMINIOS si
# interesan (thesauropodomorphlair.wordpress.com, svpow.com, blogs de
# paleontologos), pero cuya portada generica no.
HOSTS_EXACTOS_VETADOS = (
    "wordpress.com", "www.wordpress.com", "blogspot.com", "tumblr.com",
    "medium.com", "substack.com", "wikipedia.org", "archive.org",
)

PALABRAS_VETADAS = (
    "shop", "store", "tienda", "tickets", "toys", "auction", "collectibles",
    "checkout", "basket", "gettyimages", "shutterstock",
)

# Lista blanca: excepciones encontradas empiricamente a las heuristicas de
# arriba. Las revistas de Copernicus y Pensoft usan la ABREVIATURA de la
# revista como subdominio ("fr" = Fossil Record, "bg" = Biogeosciences), y la
# regla de prefijos de idioma las confundia con ediciones en frances o
# bulgaro: se habian vetado 33 articulos en ingles y en tema.
HOSTS_PERMITIDOS = (
    "copernicus.org", "pensoft.net", "zootax.com.cn", "bishopmuseum.org",
    "palaeo-electronica.org", "palass.org", "biodiversitylibrary.org",
)


def _coincide_host(dominio, lista):
    """True si el dominio es uno de la lista o un subdominio de uno."""
    return any(dominio == h or dominio.endswith("." + h) for h in lista)


def host_permitido(dominio):
    return _coincide_host(dominio, HOSTS_PERMITIDOS)


def dominio_vetado(dominio):
    if host_permitido(dominio):
        return False
    # Las versiones moviles (m.sitio.com) duplican el mismo texto.
    if dominio.startswith("m.") or ".m." in dominio:
        return True
    if dominio in HOSTS_EXACTOS_VETADOS:
        return True
    if _coincide_host(dominio, HOSTS_VETADOS):
        return True
    # Palabras de comercio dentro de alguna etiqueta del host.
    etiquetas = dominio.split(".")
    return any(p in etiqueta for etiqueta in etiquetas for p in PALABRAS_VETADAS)


_TOKEN_URL = re.compile(r"[a-z]+")


def url_es_prometedora(url, anchor=""):
    """Crawling enfocado: decide si vale la pena seguir un enlace.

    Solo se siguen enlaces cuya URL o texto de ancla ya sugiere el tema.

    INCIDENTE (01/10/2026): esto comparaba los terminos como SUBCADENA de la
    URL, y los terminos cortos del vocabulario aparecen dentro de palabras
    ajenas: "amber" en "chambers.com", "claw" en "gazetawroclawska.pl",
    "evolution" en "parisrevolutionnaire.org". La arana paso 8 horas
    descargando camaras de comercio y un diario polaco a 1000 paginas/min
    guardando cero documentos. Ahora se comparan PALABRAS COMPLETAS: la URL se
    parte en tokens alfabeticos y se interseca con el vocabulario.

    Efecto lateral util: "dinosaurio" ya no coincide con "dinosaur", asi que
    tambien corta las ediciones en otros idiomas en la raiz.
    """
    tokens = set(_TOKEN_URL.findall((url + " " + (anchor or "")).lower()))
    return bool(tokens & TERMINOS_TEMA)


# ---------------------------------------------------------------------------
# P1b. Riqueza tematica de la PAGINA (para propagacion de relevancia)
#
# Hallazgo 01/10 18:40: la politica "dentro de un dominio semilla se sigue todo
# enlace" hace que en un sitio grande (bbc.co.uk, pbs.org, nps.gov) la arana
# recorra el sitio completo: 630 paginas/min con 0 documentos/min, descartando
# biografias del personal y secciones educativas.
#
# La solucion clasica del crawling enfocado (Chakrabarti et al., 1999) es la
# LOCALIDAD TEMATICA: una pagina que habla del tema enlaza a paginas del tema.
# Asi que solo las paginas ricas en el tema "heredan" el permiso de seguir
# todos sus enlaces; las pobres solo aportan los enlaces que ya hablan del tema
# por si mismos. Se mide sobre el HTML crudo con str.count, que es C puro, para
# no pagar una tokenizacion por pagina.
# ---------------------------------------------------------------------------
TERMINOS_SONDA = (
    "dinosaur", "fossil", "paleonto", "palaeonto", "mesozoic",
    "cretaceous", "jurassic", "triassic", "sauropod", "theropod",
)

MENCIONES_PAGINA_RICA = 8


def pagina_rica_en_tema(html):
    """Aproximacion rapida: cuantas veces se nombra el tema en el HTML."""
    if not html:
        return False
    bajo = html.lower()
    return sum(bajo.count(t) for t in TERMINOS_SONDA) >= MENCIONES_PAGINA_RICA


# ---------------------------------------------------------------------------
# P2. Politica de exclusion de recursos no textuales
# El repositorio debe ser texto limpio: se descartan binarios antes de
# gastar ancho de banda en ellos.
# ---------------------------------------------------------------------------
EXTENSIONES_VETADAS = [
    # imagen / audio / video
    "jpg", "jpeg", "png", "gif", "webp", "svg", "bmp", "ico", "tif", "tiff",
    "mp3", "wav", "ogg", "flac", "mp4", "avi", "mov", "wmv", "mkv", "webm",
    # documentos binarios y empaquetados
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods",
    "zip", "rar", "7z", "gz", "tar", "bz2", "exe", "msi", "dmg", "apk",
    # recursos de presentacion y datos
    "css", "js", "json", "xml", "rss", "atom", "csv", "woff", "woff2",
    "ttf", "eot", "otf", "kml", "kmz", "epub", "mobi",
]

TIPOS_CONTENIDO_ACEPTADOS = ("text/html", "application/xhtml+xml", "text/plain")

# ---------------------------------------------------------------------------
# P3. Politica de trampas de arana (spider traps) y ruido
# Patrones de URL que generan infinitos caminos o paginas sin texto util.
# ---------------------------------------------------------------------------
PATRONES_VETADOS = [
    r"/search",            r"[?&]s=",          r"[?&]q=",
    r"/login",             r"/signin",         r"/register",
    r"/cart",              r"/checkout",       r"/donate",
    r"/shop",              r"/store",          r"/product",
    r"/calendar",          r"/events?/\d{4}",  r"\d{4}/\d{2}/\d{2}",
    r"/print/",            r"[?&]print=",      r"[?&]share=",
    r"/feed/?$",           r"/comment",        r"/replytocom",
    r"[?&]utm_",           r"[?&]sessionid",   r"[?&]sid=",
    r"/wp-admin",          r"/wp-json",        r"/tag/page/",
    # namespaces de servicio de MediaWiki: no son articulos
    r"/wiki/(Special|Talk|User|User_talk|File|File_talk|Template|Template_talk|Help|Help_talk|Wikipedia|Wikipedia_talk|MediaWiki|Draft|Module|Portal_talk|Category_talk|Book):",
    r"/w/index\.php",      r"[?&]action=edit",  r"[?&]veaction=",
    r"/wiki/Main_Page",    r"[?&]oldid=",       r"[?&]diff=",
    r"/es/", r"/fr/", r"/de/", r"/zh/", r"/ja/", r"/ru/",   # otros idiomas
]
_RE_VETADOS = re.compile("|".join(PATRONES_VETADOS), re.I)


def url_es_trampa(url):
    return bool(_RE_VETADOS.search(url))


# ---------------------------------------------------------------------------
# P3b. Politica de idioma A NIVEL DE URL
# Descubierto en una corrida real: "dinosaur" es subcadena de "dinosaurio",
# asi que la arana se metia a es.wikipedia.org, pt.wikipedia.org, etc., y
# gastaba descargas que luego el filtro de idioma botaba (99 descartes en 4
# minutos). Se corta antes de pedir la pagina.
# ---------------------------------------------------------------------------
PREFIJOS_IDIOMA = {
    "es", "fr", "de", "pt", "it", "ru", "ja", "zh", "ko", "nl", "pl",
    "ar", "tr", "sv", "cs", "da", "fi", "no", "hu", "ro", "el", "th",
    "vi", "id", "uk", "he", "fa", "hi", "ca", "sr", "bg", "sk", "hr",
    "lt", "lv", "et", "sl", "ms", "bn", "ta", "simple",
}


def host_otro_idioma(dominio):
    """True si el host corresponde a una edicion en otro idioma.

    La regla de prefijo ("es.sitio.com") es una heuristica: ver
    HOSTS_PERMITIDOS para las excepciones que costaron documentos reales.
    """
    if host_permitido(dominio):
        return False
    if dominio.endswith(("wikipedia.org", "wikimedia.org", "wikibooks.org",
                         "wikiquote.org", "wikisource.org")):
        return dominio != "en.wikipedia.org"
    return dominio.split(".")[0] in PREFIJOS_IDIOMA


# ---------------------------------------------------------------------------
# P4. Politica de idioma (a nivel de documento)
# La coleccion es en ingles (ver documento, seccion 1). Si el HTML no
# declara idioma se usa una heuristica de palabras funcionales.
# ---------------------------------------------------------------------------
FUNCIONALES_EN = {
    "the", "of", "and", "to", "in", "that", "is", "was", "for", "with",
    "as", "are", "were", "from", "this", "these", "their", "which", "have",
}


def detectar_idioma(lang_attr, texto):
    """Devuelve un codigo ISO de 2 letras, o 'xx' si no se puede decidir."""
    if lang_attr:
        return lang_attr.strip().lower().split("-")[0][:2]
    palabras = _PALABRA.findall((texto or "").lower())[:400]
    if len(palabras) < 40:
        return "xx"
    ratio = sum(1 for p in palabras if p in FUNCIONALES_EN) / len(palabras)
    return "en" if ratio > 0.18 else "xx"
