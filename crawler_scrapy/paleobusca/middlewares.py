"""Middlewares del descargador.

POLITICA DE SELECCION DE FORMATO: el repositorio es de texto plano, asi que
todo lo que no sea HTML/texto se descarta en cuanto se conoce la cabecera
Content-Type, sin pasar por el parser ni por las pipelines.

Nota de API: desde Scrapy 2.13 los metodos de middleware ya no reciben el
argumento 'spider'; se toma el crawler en from_crawler().
"""
import logging

from scrapy.exceptions import IgnoreRequest

from urllib.parse import urlparse

from .policies import (TIPOS_CONTENIDO_ACEPTADOS, dominio_vetado,
                       host_otro_idioma, url_es_prometedora,
                       url_es_trampa)

logger = logging.getLogger(__name__)


class FiltroTipoContenido:
    """Descarta respuestas cuyo Content-Type no sea textual."""

    def __init__(self, crawler):
        self.stats = crawler.stats

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler)

    def process_response(self, request, response):
        ctype = (response.headers.get("Content-Type", b"")
                 .decode("latin-1").split(";")[0].strip().lower())

        # Sin cabecera: se deja pasar y la pipeline de limpieza decidira.
        if not ctype:
            return response

        if not ctype.startswith(TIPOS_CONTENIDO_ACEPTADOS):
            self.stats.inc_value("paleobusca/descartes_por_tipo")
            self.stats.inc_value("paleobusca/tipo_descartado/" + ctype)
            logger.debug("DESCARTE tipo=%s url=%s", ctype, response.url)
            raise IgnoreRequest("Content-Type no textual: " + ctype)

        return response


class FiltroFrontera:
    """Re-aplica las politicas de frontera en el momento de pedir la URL.

    Normalmente alcanza con filtrar al EXTRAER los enlaces (spiders/paleo.py),
    pero si una corrida larga encolo URLs con politicas viejas, esas URLs ya
    estan en el JOBDIR y se descargarian igual. Este middleware las descarta
    sin gastar red, lo que permite corregir una politica sin tirar la frontera
    ni el registro de URLs ya vistas.

    Caso real: una corrida encolo cientos de miles de URLs de camaras de
    comercio y de un diario polaco porque el filtro tematico comparaba
    subcadenas ("amber" dentro de "chambers"). Con este middleware la cola
    envenenada se drena sin descargar nada.
    """

    def __init__(self, crawler):
        self.crawler = crawler
        self.stats = crawler.stats

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler)

    def process_request(self, request):
        url = request.url
        dominio = (urlparse(url).netloc or "").lower().split(":")[0]
        if dominio.startswith("www."):
            dominio = dominio[4:]

        if dominio_vetado(dominio) or host_otro_idioma(dominio):
            self.stats.inc_value("paleobusca/frontera_dominio_descartado")
            raise IgnoreRequest("dominio vetado: " + dominio)

        if url_es_trampa(url):
            self.stats.inc_value("paleobusca/frontera_trampa_descartada")
            raise IgnoreRequest("trampa de arana: " + url[:120])

        # Dentro de los dominios semilla se confia; fuera, la URL tiene que
        # hablar del tema por si misma.
        spider = self.crawler.spider
        semillas = getattr(spider, "dominios_semilla", set()) if spider else set()
        if dominio not in semillas and not url_es_prometedora(url):
            self.stats.inc_value("paleobusca/frontera_fuera_de_tema")
            raise IgnoreRequest("fuera de tema: " + url[:120])

        return None
