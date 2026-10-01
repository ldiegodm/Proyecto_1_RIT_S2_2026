"""Procesamiento de documentos: URL -> texto limpio, enlaces, puntaje tematico y hash.

Politicas que se implementan aqui:
    P2  extraer solo el texto visible (HTML o PDF) y descartar extensiones que no son texto
    P3  puntaje tematico (arañado enfocado)
    P6  normalizacion de URLs
    P7  hash SHA-256 del texto limpio (para detectar duplicados)
    P8  conteo de palabras (el minimo se aplica en arana.py)

Parte del codigo de extraccion de enlaces viene de extraer_link.py de la Tarea02
(ExtractorDeEnlaces): se mantiene la idea de HTMLParser + urljoin + urldefrag.
"""

import hashlib
import io
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

# ---------------------------------------------------------------------------
# P6: normalizacion de URLs
# ---------------------------------------------------------------------------

# Parametros que no cambian el contenido: rastreo y sesiones (RFC 3986 no los define,
# pero son la causa tipica de "mismo contenido con diferentes URLs", diapositiva 6).
PREFIJOS_RASTREO = ("utm_", "mc_", "pk_", "hsa_")
PARAMETROS_RASTREO = {"fbclid", "gclid", "dclid", "msclkid", "igshid", "ref_src", "_ga",
                      "phpsessid", "jsessionid", "sessionid", "session_id", "sid", "sessid"}
PUERTOS_POR_DEFECTO = {"http": 80, "https": 443}

# Esquemas que no son documentos descargables (de la Tarea02)
ESQUEMAS_IGNORADOS = ("mailto:", "javascript:", "tel:", "data:", "about:")


def _es_util(url):
    """Descarta anclas internas y esquemas que no son descargables (de la Tarea02)."""
    if not url or url.startswith("#"):
        return False
    return not url.lower().startswith(ESQUEMAS_IGNORADOS)


def normalizar_url(url, base=None):
    """Devuelve la forma canonica de una URL, o None si no sirve. Politica P6.

    host en minusculas, sin fragmento (#), sin parametros de rastreo, sin puerto por
    defecto, rutas relativas resueltas contra `base` y parametros ordenados.
    """
    try:
        url = url.strip()
        if base:
            url = urljoin(base, url)
        partes = urlsplit(url)
        esquema = partes.scheme.lower()
        host = (partes.hostname or "").lower().rstrip(".")
        if esquema not in PUERTOS_POR_DEFECTO or not host or ":" in host:
            return None
        puerto = partes.port
    except ValueError:
        return None
    netloc = host if puerto in (None, PUERTOS_POR_DEFECTO[esquema]) else f"{host}:{puerto}"
    ruta = quote(partes.path or "/", safe="/%:@!$&'()*+,;=~")
    parametros = [(k, v) for k, v in parse_qsl(partes.query, keep_blank_values=True)
                  if k.lower() not in PARAMETROS_RASTREO
                  and not k.lower().startswith(PREFIJOS_RASTREO)]
    consulta = urlencode(sorted(parametros))
    return urlunsplit((esquema, netloc, ruta, consulta, ""))


# ---------------------------------------------------------------------------
# P1 / P2: dominios y extensiones
# ---------------------------------------------------------------------------

# Segundas etiquetas tipicas de sufijos publicos de dos niveles (ac.uk, co.uk, edu.au...)
_SEGUNDO_NIVEL = {"ac", "co", "gov", "edu", "org", "com", "net"}


def dominio_de(host):
    """Dominio registrable aproximado: www.nhm.ac.uk -> nhm.ac.uk, ucmp.berkeley.edu -> berkeley.edu."""
    host = host.split(":")[0]
    etiquetas = host.split(".")
    if len(etiquetas) >= 3 and etiquetas[-2] in _SEGUNDO_NIVEL and len(etiquetas[-1]) == 2:
        return ".".join(etiquetas[-3:])
    return ".".join(etiquetas[-2:])


EXTENSIONES_BLOQUEADAS = {
    # imagenes, audio, video
    "jpg", "jpeg", "png", "gif", "svg", "webp", "bmp", "ico", "tif", "tiff",
    "mp3", "wav", "ogg", "flac", "aac", "mp4", "avi", "mov", "mkv", "webm", "wmv", "flv",
    # estilos, scripts, datos, comprimidos, ejecutables, otros documentos
    "css", "js", "json", "xml", "rss", "atom", "woff", "woff2", "ttf", "eot",
    "zip", "gz", "tgz", "tar", "rar", "7z", "bz2", "exe", "msi", "dmg", "iso", "apk",
    "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "csv",
}


_ETIQUETAS_TIENDA = ("shop", "store", "tienda", "buy", "cart")
_SEGMENTOS_TIENDA = {"shop", "store", "tienda", "cart", "checkout", "basket", "donate"}


def es_tienda(url):
    """Politica P1: URLs de tiendas y pagos (shop.museo.org, /store/, /cart...) no sirven al colectivo."""
    partes = urlsplit(url)
    if partes.hostname and partes.hostname.split(".")[0] in _ETIQUETAS_TIENDA:
        return True
    return any(seg in _SEGMENTOS_TIENDA for seg in partes.path.lower().split("/"))


def extension_bloqueada(url):
    """Politica P2 (antes de descargar): True si la URL apunta a un archivo que no es texto."""
    ruta = urlsplit(url).path.lower()
    if "." not in ruta.rsplit("/", 1)[-1]:
        return False
    return ruta.rsplit(".", 1)[-1] in EXTENSIONES_BLOQUEADAS


# ---------------------------------------------------------------------------
# P2: HTML -> texto limpio y enlaces
# ---------------------------------------------------------------------------

# Etiquetas cuyo contenido no es texto visible del articulo (menus, scripts, estilos...)
ETIQUETAS_SALTADAS = {"script", "style", "noscript", "nav", "header", "footer", "aside",
                      "svg", "iframe", "template", "head", "select", "button"}
ETIQUETAS_DE_BLOQUE = {"p", "div", "br", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5",
                       "h6", "section", "article", "table", "ul", "ol", "blockquote", "pre",
                       "dd", "dt", "figcaption", "main"}


@dataclass
class Pagina:
    titulo: str = ""
    texto: str = ""
    enlaces: list = field(default_factory=list)   # [(url_normalizada, anchor_text)]
    noindex: bool = False
    nofollow: bool = False


class ExtractorHTML(HTMLParser):
    """Recorre el HTML una sola vez y acumula texto visible, titulo y enlaces.

    Igual que ExtractorDeEnlaces de la Tarea02 se procesa como flujo (sin arbol DOM),
    pero ademas lleva la cuenta de cuando estamos dentro de una etiqueta "saltada".
    """

    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.saltar = 0              # profundidad dentro de etiquetas saltadas
        self.en_titulo = False
        self.titulo = []
        self.partes = []
        self.enlaces = []            # [(href, anchor)]
        self._href = None            # href del <a> abierto
        self._anchor = []
        self.noindex = False
        self.nofollow = False

    def _cerrar_enlace(self):
        if self._href is not None:
            self.enlaces.append((self._href, " ".join("".join(self._anchor).split())))
        self._href, self._anchor = None, []

    def handle_starttag(self, etiqueta, atributos):
        attrs = dict(atributos)
        if etiqueta == "base" and attrs.get("href"):
            self.base = urljoin(self.base, attrs["href"])   # <base href> tiene prioridad (Tarea02)
        elif etiqueta == "meta" and (attrs.get("name") or "").lower() == "robots":
            contenido = (attrs.get("content") or "").lower()
            self.noindex = self.noindex or "noindex" in contenido
            self.nofollow = self.nofollow or "nofollow" in contenido
        elif etiqueta == "title":
            self.en_titulo = True
        elif etiqueta == "body":
            self.saltar = 0          # </head> es opcional: un <head> sin cerrar no debe tapar la pagina
        elif etiqueta in ("a", "area") and attrs.get("href"):
            self._cerrar_enlace()
            self._href = attrs["href"]

        if etiqueta in ETIQUETAS_SALTADAS:
            self.saltar += 1
        elif etiqueta in ETIQUETAS_DE_BLOQUE:
            self.partes.append("\n")

    def handle_endtag(self, etiqueta):
        if etiqueta == "title":
            self.en_titulo = False
        elif etiqueta == "a":
            self._cerrar_enlace()
        if etiqueta in ETIQUETAS_SALTADAS:
            self.saltar = max(0, self.saltar - 1)
        elif etiqueta in ETIQUETAS_DE_BLOQUE:
            self.partes.append("\n")

    def handle_data(self, datos):
        if self.en_titulo:
            self.titulo.append(datos)
        if self._href is not None:
            self._anchor.append(datos)
        if self.saltar == 0 and not self.en_titulo:
            self.partes.append(datos)


def _decodificar(cuerpo, content_type):
    """bytes -> str usando el charset del encabezado, o del <meta>, o utf-8."""
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    if not m:
        m = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", cuerpo[:3000], re.I)
    codificacion = m.group(1) if m else "utf-8"
    if isinstance(codificacion, bytes):
        codificacion = codificacion.decode("ascii", "ignore")
    try:
        return cuerpo.decode(codificacion, errors="replace")
    except LookupError:
        return cuerpo.decode("utf-8", errors="replace")


def _limpiar_lineas(texto):
    """Quita espacios repetidos y lineas que parecen items de menu (menos de 4 palabras sin punto final)."""
    lineas = []
    for linea in texto.split("\n"):
        linea = " ".join(linea.split())
        if len(linea.split()) >= 4 or (linea and linea[-1] in ".!?:"):
            lineas.append(linea)
    return "\n".join(lineas)


def extraer_html(cuerpo, content_type, url):
    """Politica P2: de bytes HTML a (titulo, texto visible, enlaces normalizados)."""
    parser = ExtractorHTML(base=url)
    parser.feed(_decodificar(cuerpo, content_type))
    parser.close()
    parser._cerrar_enlace()

    enlaces, vistos = [], set()
    for href, anchor in parser.enlaces:
        if not _es_util(href):
            continue
        normal = normalizar_url(href, parser.base)
        if normal and normal not in vistos:
            vistos.add(normal)
            enlaces.append((normal, anchor))

    return Pagina(titulo=" ".join("".join(parser.titulo).split()),
                  texto=_limpiar_lineas("".join(parser.partes)),
                  enlaces=enlaces, noindex=parser.noindex, nofollow=parser.nofollow)


def extraer_texto_plano(cuerpo, content_type):
    return Pagina(texto=_limpiar_lineas(_decodificar(cuerpo, content_type)))


def pdf_a_texto(datos, max_paginas=300):
    """Politica P2: bytes de PDF -> (titulo, texto). Devuelve None si no se puede leer.

    Es una funcion de nivel superior (sin estado) para poder ejecutarse en un proceso aparte.
    """
    try:
        import logging
        logging.getLogger("pypdf").setLevel(logging.ERROR)
        from pypdf import PdfReader
        lector = PdfReader(io.BytesIO(datos))
        if lector.is_encrypted:
            return None
        partes = []
        for i, pagina in enumerate(lector.pages):
            if i >= max_paginas:
                break
            try:
                partes.append(pagina.extract_text() or "")
            except Exception:
                continue
        texto = "\n".join(partes)
        texto = re.sub(r"-\n(\w)", r"\1", texto)             # palabras partidas al final de linea
        texto = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", texto)
        texto = "\n".join(" ".join(l.split()) for l in texto.split("\n") if l.strip())
        titulo = ""
        try:
            titulo = (lector.metadata.title or "").strip() if lector.metadata else ""
        except Exception:
            pass
        return titulo, texto
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Palabras, P8 y P7
# ---------------------------------------------------------------------------

_PALABRA = re.compile(r"[^\W\d_]+", re.UNICODE)


def palabras_de(texto):
    return _PALABRA.findall(texto.lower())


def contar_palabras(texto):
    return len(_PALABRA.findall(texto))


def hash_texto(texto):
    """Politica P7: SHA-256 del texto limpio normalizado (minusculas, espacios colapsados)."""
    return hashlib.sha256(" ".join(texto.lower().split()).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# P3: puntaje tematico
# ---------------------------------------------------------------------------

def _raiz(palabra):
    """Quita el plural simple para que dinosaurs cuente como dinosaur."""
    if len(palabra) > 4 and palabra.endswith("s") and not palabra.endswith("ss"):
        return palabra[:-1]
    return palabra


class Tema:
    """Diccionario del tema con pesos (config/terminos_tema.txt)."""

    MIN_PALABRAS = 30   # con menos texto el puntaje no es confiable

    def __init__(self, ruta):
        self.pesos = {}
        with open(ruta, encoding="utf-8") as f:
            for linea in f:
                linea = linea.split("#")[0].split()
                if linea:
                    peso = float(linea[1]) if len(linea) > 1 else 1.0
                    self.pesos[_raiz(linea[0].lower())] = peso

    def puntaje(self, texto):
        """Politica P3: (aciertos ponderados por 1000 palabras, terminos encontrados)."""
        palabras = palabras_de(texto)
        if len(palabras) < self.MIN_PALABRAS:
            return 0.0, []
        aciertos, total = {}, 0.0
        for palabra in palabras:
            r = _raiz(palabra)
            if r in self.pesos:
                aciertos[r] = aciertos.get(r, 0) + 1
                total += self.pesos[r]
        encontrados = sorted(aciertos, key=aciertos.get, reverse=True)[:8]
        return total / len(palabras) * 1000, encontrados

    def bonus_anchor(self, texto):
        """Politica P4: cuantos terminos del tema aparecen en el texto del enlace o en su URL."""
        return sum(1 for p in palabras_de(texto) if _raiz(p) in self.pesos)
