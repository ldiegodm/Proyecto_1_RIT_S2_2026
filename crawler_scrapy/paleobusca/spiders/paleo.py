"""Arana de PaleoBusca.

Aqui viven las politicas de FRONTERA: que se visita, en que orden y hasta
donde. Las de cortesia estan en settings.py y las de contenido en
policies.py / pipelines.py.

Uso:
    scrapy crawl paleo
    scrapy crawl paleo -s JOBDIR=estado/corrida1          # reanudable
    scrapy crawl paleo -a semillas=seeds/urls.txt
    scrapy crawl paleo -s CLOSESPIDER_PAGECOUNT=50        # prueba corta
"""
import datetime as dt
import os
from collections import Counter
from urllib.parse import urlparse

import scrapy
from scrapy.exceptions import CloseSpider, IgnoreRequest
from scrapy.linkextractors import LinkExtractor

from ..items import DocumentoItem
from ..policies import (EXTENSIONES_VETADAS, dominio_vetado,
                        host_otro_idioma, pagina_rica_en_tema,
                        url_es_prometedora, url_es_trampa)


def dominio_de(url):
    """Dominio normalizado, sin 'www.' y sin puerto."""
    host = (urlparse(url).netloc or "").lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


class PaleoSpider(scrapy.Spider):
    name = "paleo"

    # --------------------------------------------------------------- arranque
    def __init__(self, semillas="seeds/urls.txt", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.archivo_semillas = semillas
        self.paginas_por_dominio = Counter()
        self.dominios_semilla = set()

        # Extractor de enlaces: implementa la POLITICA DE EXCLUSION DE
        # FORMATOS a nivel de enlace (no se encolan binarios) y canonicaliza
        # las URLs para que /pagina y /pagina?utm=x no se cuenten dos veces.
        self.extractor = LinkExtractor(
            deny_extensions=EXTENSIONES_VETADAS,
            canonicalize=True,
            unique=True,
            tags=("a",),
            attrs=("href",),
        )

    # Scrapy >= 2.13 arranca por Spider.start() (generador asincrono).
    async def start(self):
        for peticion in self._peticiones_semilla():
            yield peticion

    # Compatibilidad con Scrapy < 2.13, que arranca por start_requests().
    def start_requests(self):
        return self._peticiones_semilla()

    def _peticiones_semilla(self):
        """POLITICA DE SEMILLAS: las 10 URLs de la Tarea 02, en seeds/urls.txt.

        Se cargan desde archivo (no hardcodeadas) para poder ampliar la
        coleccion sin tocar codigo, y se encolan con prioridad alta para que
        el nucleo confiable del tema se recorra antes que lo periferico.
        """
        self.meta_bytes = int(
            self.settings.getfloat("META_GB", 0) * 1073741824)
        if self.meta_bytes:
            self.logger.info("META DE TAMANO: %.2f GB de texto limpio",
                             self.meta_bytes / 1073741824.0)

        ruta = self.archivo_semillas
        if not os.path.isabs(ruta):
            ruta = os.path.join(os.getcwd(), ruta)

        with open(ruta, encoding="utf-8") as f:
            semillas = [ln.strip() for ln in f
                        if ln.strip() and not ln.startswith("#")]

        self.dominios_semilla = {dominio_de(u) for u in semillas}
        self.logger.info("SEMILLAS: %d urls en %d dominios: %s",
                         len(semillas), len(self.dominios_semilla),
                         ", ".join(sorted(self.dominios_semilla)))

        for url in semillas:
            yield scrapy.Request(
                url,
                callback=self.parse,
                errback=self.registrar_error,
                meta={"es_semilla": True},
                priority=100,          # las semillas primero
                # dont_filter: las semillas se piden SIEMPRE, aunque el
                # filtro de duplicados del JOBDIR ya las haya visto. Sin esto,
                # reanudar una corrida no reinyecta nada por las semillas y la
                # frontera solo se encoge: fue el motivo de que una corrida
                # reanudada cerrara con "finished" sin pedir una sola URL.
                dont_filter=True,
            )

    # ------------------------------------------------------------- extraccion
    def parse(self, response):
        self._revisar_meta()
        dominio = dominio_de(response.url)
        profundidad = response.meta.get("depth", 0)
        self.paginas_por_dominio[dominio] += 1

        # BITACORA: una linea por pagina visitada, con su procedencia.
        if self.settings.getbool("BITACORA_DETALLADA", True):
            self._registrar_visita(response, dominio, profundidad)

        yield self._armar_item(response, dominio, profundidad)

        # PROPAGACION DE RELEVANCIA: solo una pagina que habla del tema presta
        # su confianza a todos sus enlaces (ver policies.pagina_rica_en_tema).
        rica = pagina_rica_en_tema(response.text)
        self.crawler.stats.inc_value(
            "paleobusca/paginas_ricas" if rica else "paleobusca/paginas_pobres")

        for peticion in self._seguir_enlaces(response, dominio, profundidad,
                                             rica):
            yield peticion

    def _registrar_visita(self, response, dominio, profundidad):
        self.logger.info(
            "VISITA d=%d dom=%s status=%d bytes=%d url=%s ref=%s",
            profundidad, dominio, response.status, len(response.body),
            response.url, response.request.headers.get("Referer", b"-")
            .decode("latin-1"))

    def _armar_item(self, response, dominio, profundidad):
        cab = response.headers
        item = DocumentoItem()
        item["url"] = response.request.url
        item["url_final"] = response.url
        item["dominio"] = dominio
        item["referer"] = cab.get("Referer", b"").decode("latin-1") or \
            response.request.headers.get("Referer", b"").decode("latin-1")
        item["profundidad"] = profundidad
        item["es_semilla"] = int(bool(response.meta.get("es_semilla")))
        # distingue "URL semilla" de "dominio semilla": sirve para medir
        # cuanto aporto el crawling enfocado fuera de las fuentes iniciales
        item["dominio_semilla"] = int(dominio in self.dominios_semilla)
        item["http_status"] = response.status
        item["content_type"] = cab.get("Content-Type", b"").decode("latin-1")
        item["bytes_html"] = len(response.body)
        item["last_modified"] = cab.get("Last-Modified", b"").decode("latin-1")
        item["etag"] = cab.get("ETag", b"").decode("latin-1")
        item["fecha_descarga"] = dt.datetime.now().astimezone().isoformat(
            timespec="seconds")
        item["titulo"] = (response.css("title::text").get() or "").strip()
        # el codigo declarado por la pagina; policies.detectar_idioma decide
        item["idioma"] = response.xpath("/html/@lang").get()
        item["html_crudo"] = response.text
        return item

    # --------------------------------------------------------------- frontera
    def _seguir_enlaces(self, response, dominio_actual, profundidad,
                        pagina_rica=True):
        """POLITICA DE SELECCION DE ENLACES (crawling enfocado).

        1. Se descartan trampas, dominios vetados y otros idiomas.
        2. Si la pagina ACTUAL habla del tema, dentro de un dominio semilla se
           siguen todos sus enlaces: es la localidad tematica.
        3. Si la pagina actual NO habla del tema, solo se siguen los enlaces
           que hablan del tema por si mismos. Esto evita que una biografia del
           personal de un museo abra la puerta al sitio entero.
        4. Fuera de los dominios semilla siempre se exige tema, y con prioridad
           menor: primero se agota lo confiable.
        """
        stats = self.crawler.stats
        tope = self.settings.getint("MAX_PAGINAS_POR_DOMINIO")
        permitir_nuevos = self.settings.getbool("PERMITIR_DOMINIOS_NUEVOS")

        for enlace in self.extractor.extract_links(response):
            url = enlace.url
            dom = dominio_de(url)

            if url_es_trampa(url):
                stats.inc_value("paleobusca/enlaces_trampa")
                continue

            # POLITICA DE DOMINIOS VETADOS: tiendas, redes sociales y
            # plataformas de video no aportan informacion del tema.
            if dominio_vetado(dom):
                stats.inc_value("paleobusca/enlaces_dominio_vetado")
                continue

            # POLITICA DE IDIOMA EN LA FRONTERA: no gastar descargas en
            # ediciones en otros idiomas que el filtro luego botaria.
            if host_otro_idioma(dom):
                stats.inc_value("paleobusca/enlaces_otro_idioma")
                continue

            # POLITICA ANTI-ACAPARAMIENTO: ningun dominio aporta mas de
            # MAX_PAGINAS_POR_DOMINIO documentos ("no se vale bajar todo de
            # un solo sitio"). Reparte el repositorio entre fuentes.
            if self.paginas_por_dominio[dom] >= tope:
                stats.inc_value("paleobusca/enlaces_tope_dominio")
                continue

            en_tema = url_es_prometedora(url, enlace.text)

            if dom in self.dominios_semilla and (pagina_rica or en_tema):
                prioridad = 10
            elif dom in self.dominios_semilla:
                stats.inc_value("paleobusca/enlaces_pagina_pobre")
                continue
            elif permitir_nuevos and en_tema:
                prioridad = 0
                stats.inc_value("paleobusca/dominios_nuevos_seguidos")
            else:
                stats.inc_value("paleobusca/enlaces_fuera_de_tema")
                continue

            yield scrapy.Request(url, callback=self.parse,
                                 errback=self.registrar_error,
                                 priority=prioridad)

    def _revisar_meta(self):
        """Cierre ordenado al alcanzar META_GB de texto acumulado.

        CloseSpider deja que Scrapy termine las descargas en vuelo, haga el
        commit final de SQLite y persista la frontera en el JOBDIR, asi que
        la corrida se puede continuar despues si se quiere mas volumen.
        """
        if not getattr(self, "meta_bytes", 0):
            return
        total = self.crawler.stats.get_value(
            "paleobusca/bytes_texto_repo", 0)
        if total >= self.meta_bytes:
            raise CloseSpider("meta_de_tamano_alcanzada")

    # ----------------------------------------------------------------- fallos
    def registrar_error(self, failure):
        """Los fallos de red son bitacora: quedan en la tabla errores.

        IgnoreRequest NO se guarda: lo lanza nuestro propio middleware de
        Content-Type, o sea es una POLITICA aplicandose, no un fallo. En la
        primera corrida larga inflaba la tabla con 1258 filas falsas de
        2531, y hacia ver el aranado como si estuviera roto.
        """
        if failure.check(IgnoreRequest):
            return
        peticion = failure.request
        motivo = repr(failure.value)[:300]
        self.logger.warning("FALLO url=%s motivo=%s", peticion.url, motivo)
        con = getattr(self, "paleo_db", None)
        if con is not None:
            con.execute(
                "INSERT INTO errores (url, dominio, motivo, fecha) "
                "VALUES (?,?,?,?)",
                (peticion.url, dominio_de(peticion.url), motivo,
                 dt.datetime.now().astimezone().isoformat(timespec="seconds")))

    def closed(self, reason):
        top = self.paginas_por_dominio.most_common(15)
        self.logger.info("CIERRE (%s). Paginas por dominio (top 15): %s",
                         reason, top)
