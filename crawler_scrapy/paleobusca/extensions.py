"""Extensiones propias del aranador."""
import time

from scrapy import signals
from twisted.internet.task import LoopingCall


class ProgresoConsola:
    """Imprime una linea compacta de progreso en la consola.

    Con LOG_FILE configurado, Scrapy manda TODO el log al archivo y deja la
    consola muda. Esta extension escribe directo a stdout UNA linea cada
    PROGRESO_INTERVALO segundos, con el mismo formato que el aranador propio,
    para poder vigilar una corrida de toda la noche sin leer el log entero.

    El log detallado (la bitacora que pide la rubrica) sigue yendo al archivo.
    """

    def __init__(self, crawler, intervalo):
        self.crawler = crawler
        self.stats = crawler.stats
        self.intervalo = intervalo
        self.inicio = None
        self.tarea = None
        self.ultimo_docs = 0
        self.ultimo_tiempo = None

    @classmethod
    def from_crawler(cls, crawler):
        intervalo = crawler.settings.getfloat("PROGRESO_INTERVALO", 30.0)
        ext = cls(crawler, intervalo)
        if intervalo > 0:
            crawler.signals.connect(ext.abrir, signal=signals.spider_opened)
            crawler.signals.connect(ext.cerrar, signal=signals.spider_closed)
        return ext

    def abrir(self):
        self.inicio = self.ultimo_tiempo = time.time()
        meta = self.crawler.settings.getfloat("META_GB", 0)
        print("Aranador PaleoBusca en marcha{}. Progreso cada {:.0f} s; "
              "bitacora completa en {}".format(
                  " (meta {:.1f} GB)".format(meta) if meta else "",
                  self.intervalo,
                  self.crawler.settings.get("LOG_FILE") or "consola"),
              flush=True)
        self.tarea = LoopingCall(self.imprimir)
        self.tarea.start(self.intervalo, now=False)

    def cerrar(self, reason=None):
        if self.tarea is not None and self.tarea.running:
            self.tarea.stop()
        self.imprimir(final=reason)

    def imprimir(self, final=None):
        if self.inicio is None:
            return
        ahora = time.time()
        minutos = (ahora - self.inicio) / 60.0

        docs = self.stats.get_value("item_scraped_count", 0) or 0
        descartados = self.stats.get_value("item_dropped_count", 0) or 0
        gb = (self.stats.get_value("paleobusca/bytes_texto_repo", 0) or 0) / 1e9

        transcurrido = max(ahora - (self.ultimo_tiempo or ahora), 1e-9)
        ritmo = (docs - self.ultimo_docs) * 60.0 / transcurrido
        self.ultimo_docs, self.ultimo_tiempo = docs, ahora

        pendientes = en_vuelo = hosts = 0
        try:                                   # el engine puede estar cerrando
            engine = self.crawler.engine
            if engine.scheduler is not None:
                pendientes = len(engine.scheduler)
            en_vuelo = len(engine.downloader.active)
            hosts = len(engine.downloader.slots)
        except Exception:
            pass

        etiqueta = "FIN {}".format(final) if final else time.strftime("%H:%M:%S")
        print("[{}] {:6.1f} min | docs={} ({:.3f} GB) | {:5.0f} docs/min | "
              "descartados={} | pendientes={} en_vuelo={} hosts={}".format(
                  etiqueta, minutos, docs, gb, ritmo, descartados,
                  pendientes, en_vuelo, hosts), flush=True)
