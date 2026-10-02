"""Cadena de procesamiento: de HTML crudo a repositorio de texto + metadatos.

Orden configurado en settings.ITEM_PIPELINES:
  100 LimpiezaHTMLPipeline     -> extrae texto util, calcula hash y puntaje
  200 FiltroDocumentoPipeline  -> aplica idioma, longitud minima y tema
  300 DeduplicacionPipeline    -> descarta contenido repetido (por hash)
  400 RepositorioTextoPipeline -> escribe el .txt en el repositorio
  500 MetadatosSQLitePipeline  -> registra los metadatos y la bitacora

Nota de API: desde Scrapy 2.13 los metodos de pipeline ya no reciben el
argumento 'spider'; se obtiene el crawler en from_crawler() y de ahi las
settings, las estadisticas y el spider activo.
"""
import hashlib
import logging
import os
import re
import sqlite3

from scrapy.exceptions import DropItem

from .policies import densidad_tema, detectar_idioma, puntaje_tema

try:                      # extractor de texto de calidad (quita boilerplate)
    import trafilatura
except ImportError:       # pragma: no cover
    trafilatura = None

try:
    from lxml import html as lxml_html
except ImportError:       # pragma: no cover
    lxml_html = None

_ESPACIOS = re.compile(r"[ \t\r\f\v]+")
_LINEAS = re.compile(r"\n{3,}")


def _normalizar(texto):
    """Colapsa espacios y lineas en blanco, conservando los parrafos."""
    lineas = [_ESPACIOS.sub(" ", ln).strip() for ln in texto.splitlines()]
    return _LINEAS.sub("\n\n", "\n".join(lineas)).strip()


class PipelineBase:
    """Acceso comun a settings, estadisticas y log."""

    def __init__(self, crawler):
        self.crawler = crawler
        self.settings = crawler.settings
        self.stats = crawler.stats
        self.logger = logging.getLogger(type(self).__name__)

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler)


class LimpiezaHTMLPipeline(PipelineBase):
    """POLITICA DE LIMPIEZA: el repositorio guarda texto, no HTML.

    Se usa trafilatura, que aisla el cuerpo del articulo y descarta menus,
    barras laterales, pies de pagina, scripts y estilos. Si no esta
    instalada, se cae a un extractor propio basado en lxml.
    """

    def open_spider(self):
        if trafilatura is None:
            self.logger.warning(
                "trafilatura no instalada: se usa el extractor lxml de respaldo")

    def process_item(self, item):
        html = item.pop("html_crudo", None) or ""
        texto = ""

        if trafilatura is not None:
            texto = trafilatura.extract(
                html,
                url=item.get("url_final"),
                include_comments=False,   # los comentarios no son del tema
                include_tables=True,      # las fichas de especies usan tablas
                favor_precision=True,     # texto limpio antes que abundante
            ) or ""

        if not texto and lxml_html is not None:
            texto = self._extraer_con_lxml(html)

        item["texto"] = _normalizar(texto)
        item["bytes_texto"] = len(item["texto"].encode("utf-8"))
        item["palabras"] = len(item["texto"].split())
        item["hash_texto"] = hashlib.sha256(
            item["texto"].encode("utf-8")).hexdigest()
        item["puntaje_tema"] = puntaje_tema(item["texto"])
        item["densidad_tema"] = round(densidad_tema(item["texto"]), 2)
        item["idioma"] = detectar_idioma(item.get("idioma"), item["texto"])
        return item

    @staticmethod
    def _extraer_con_lxml(html):
        if not html:
            return ""
        try:
            doc = lxml_html.fromstring(html)
        except Exception:
            return ""
        vetados = ("script", "style", "noscript", "nav", "header", "footer",
                   "aside", "form", "svg", "iframe", "figure")
        for tag in vetados:
            for nodo in list(doc.iter(tag)):
                nodo.drop_tree()
        return "\n".join(doc.itertext())


class FiltroDocumentoPipeline(PipelineBase):
    """POLITICAS DE IDIOMA, LONGITUD MINIMA Y PERTENENCIA AL TEMA."""

    def open_spider(self):
        self.min_palabras = self.settings.getint("MIN_PALABRAS")
        self.min_tema = self.settings.getint("MIN_PUNTAJE_TEMA")
        self.min_densidad = self.settings.getfloat("MIN_DENSIDAD_TEMA")
        self.idiomas = set(self.settings.getlist("IDIOMAS_ACEPTADOS"))

    def process_item(self, item):
        if item["idioma"] not in self.idiomas:
            self.stats.inc_value("paleobusca/descartes_por_idioma")
            raise DropItem("idioma={} {}".format(item["idioma"], item["url"]))

        if item["palabras"] < self.min_palabras:
            self.stats.inc_value("paleobusca/descartes_por_longitud")
            raise DropItem("palabras={} {}".format(item["palabras"], item["url"]))

        if item["puntaje_tema"] < self.min_tema:
            self.stats.inc_value("paleobusca/descartes_por_tema")
            raise DropItem("tema={} {}".format(item["puntaje_tema"], item["url"]))

        if item["densidad_tema"] < self.min_densidad:
            self.stats.inc_value("paleobusca/descartes_por_densidad")
            raise DropItem("densidad={} {}".format(
                item["densidad_tema"], item["url"]))

        return item


class DeduplicacionPipeline(PipelineBase):
    """POLITICA DE DEDUPLICACION DE CONTENIDO.

    Scrapy ya deduplica URLs (RFPDupeFilter). Esto ataca el otro caso: la
    misma nota publicada en varias URLs (version de impresion, amp,
    parametros de campana). Se compara el sha256 del texto limpio, no del
    HTML, que cambia en cada visita por banners, fechas y anuncios.
    """

    def open_spider(self):
        self.vistos = set()
        ruta = self.settings.get("DB_PATH")
        if os.path.exists(ruta):          # corrida reanudada: recuperar hashes
            try:
                con = sqlite3.connect(ruta)
                self.vistos = {r[0] for r in
                               con.execute("SELECT hash_texto FROM documentos")}
                con.close()
                self.logger.info("Dedup: %d hashes previos cargados",
                                 len(self.vistos))
            except sqlite3.Error as e:
                self.logger.warning("Dedup: no se pudo leer la BD (%s)", e)

    def process_item(self, item):
        h = item["hash_texto"]
        if h in self.vistos:
            self.stats.inc_value("paleobusca/descartes_por_duplicado")
            raise DropItem("duplicado " + item["url"])
        self.vistos.add(h)
        return item


class RepositorioTextoPipeline(PipelineBase):
    """Escribe el documento como .txt puro dentro de repo/<dominio>/.

    Se reparte en subcarpetas por los 2 primeros digitos del hash para no
    dejar cientos de miles de archivos en un solo directorio, cosa que
    degrada el sistema de archivos y vuelve inusable el explorador.
    """

    def open_spider(self):
        self.base = self.settings.get("REPO_DIR")
        os.makedirs(self.base, exist_ok=True)
        # El contador del repositorio arranca desde lo ya guardado en
        # corridas anteriores, para que la meta de tamano sea acumulativa.
        previos = self._bytes_ya_guardados()
        self.stats.set_value("paleobusca/bytes_texto_repo", previos)
        if previos:
            self.logger.info("Repositorio previo: %.3f GB",
                             previos / 1073741824.0)

    def _bytes_ya_guardados(self):
        ruta = self.settings.get("DB_PATH")
        if not os.path.exists(ruta):
            return 0
        try:
            con = sqlite3.connect(ruta)
            n = con.execute(
                "SELECT COALESCE(SUM(bytes_texto),0) FROM documentos"
            ).fetchone()[0]
            con.close()
            return n or 0
        except sqlite3.Error:
            return 0

    def process_item(self, item):
        h = item["hash_texto"]
        carpeta = os.path.join(self.base, item["dominio"], h[:2])
        os.makedirs(carpeta, exist_ok=True)
        ruta = os.path.join(carpeta, h + ".txt")

        with open(ruta, "w", encoding="utf-8", newline="\n") as f:
            f.write(item["texto"])

        item["ruta_archivo"] = os.path.relpath(ruta, self.base).replace("\\", "/")
        self.stats.inc_value("paleobusca/documentos_guardados")
        self.stats.inc_value("paleobusca/bytes_texto", item["bytes_texto"])
        self.stats.inc_value("paleobusca/bytes_texto_repo", item["bytes_texto"])
        return item


class MetadatosSQLitePipeline(PipelineBase):
    """Repositorio de metadatos + bitacora consultable.

    SQLite se eligio sobre CSV porque permite responder con SQL las
    preguntas de la discusion de resultados (paginas por dominio,
    profundidad promedio, tasa de duplicados) y sobre MySQL porque no exige
    levantar un servidor ni credenciales para reproducir el trabajo.
    """

    DDL = """
    CREATE TABLE IF NOT EXISTS documentos (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        url            TEXT UNIQUE,
        url_final      TEXT,
        dominio        TEXT,
        referer        TEXT,
        profundidad    INTEGER,
        es_semilla     INTEGER,
        dominio_semilla INTEGER,
        http_status    INTEGER,
        content_type   TEXT,
        bytes_html     INTEGER,
        last_modified  TEXT,
        etag           TEXT,
        fecha_descarga TEXT,
        titulo         TEXT,
        idioma         TEXT,
        palabras       INTEGER,
        bytes_texto    INTEGER,
        hash_texto     TEXT,
        puntaje_tema   INTEGER,
        densidad_tema  REAL,
        ruta_archivo   TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_doc_dominio ON documentos(dominio);
    CREATE INDEX IF NOT EXISTS ix_doc_hash    ON documentos(hash_texto);

    CREATE TABLE IF NOT EXISTS errores (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        url     TEXT,
        dominio TEXT,
        motivo  TEXT,
        fecha   TEXT
    );
    """

    CAMPOS = ["url", "url_final", "dominio", "referer", "profundidad",
              "es_semilla", "dominio_semilla", "http_status", "content_type", "bytes_html",
              "last_modified", "etag", "fecha_descarga", "titulo", "idioma",
              "palabras", "bytes_texto", "hash_texto", "puntaje_tema",
              "densidad_tema", "ruta_archivo"]

    def open_spider(self):
        ruta = self.settings.get("DB_PATH")
        os.makedirs(os.path.dirname(ruta) or ".", exist_ok=True)
        self.con = sqlite3.connect(ruta)
        self.con.executescript(self.DDL)
        self.con.execute("PRAGMA journal_mode=WAL")      # escrituras rapidas
        self.con.execute("PRAGMA synchronous=NORMAL")
        self.con.commit()
        self.lote = self.settings.getint("SQLITE_BATCH")
        self.pendientes = 0
        # el spider registra los fallos de red en la tabla errores
        if self.crawler.spider is not None:
            self.crawler.spider.paleo_db = self.con

    def process_item(self, item):
        columnas = ",".join(self.CAMPOS)
        marcas = ",".join("?" * len(self.CAMPOS))
        valores = [item.get(c) for c in self.CAMPOS]
        self.con.execute(
            "INSERT OR IGNORE INTO documentos ({}) VALUES ({})".format(
                columnas, marcas), valores)
        self.pendientes += 1
        if self.pendientes >= self.lote:
            self.con.commit()
            self.pendientes = 0
        return item

    def close_spider(self):
        self.con.commit()
        total, palabras, bytes_ = self.con.execute(
            "SELECT COUNT(*), COALESCE(SUM(palabras),0), "
            "COALESCE(SUM(bytes_texto),0) FROM documentos").fetchone()
        self.logger.info(
            "REPOSITORIO: %d documentos | %d palabras | %.2f MB de texto",
            total, palabras, bytes_ / 1048576.0)
        self.con.close()
