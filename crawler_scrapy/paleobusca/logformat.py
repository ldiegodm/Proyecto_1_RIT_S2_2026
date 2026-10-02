"""Formato breve para el log.

Problema encontrado en la primera corrida larga: el log crecio a 74 MB en 50
minutos (proyeccion: ~900 MB en una noche). La causa no eran las lineas de
bitacora, sino que Scrapy escribe el Item COMPLETO cada vez que se guarda o se
descarta un documento, y nuestro Item contiene el campo `texto`: el documento
entero terminaba dentro del log, duplicando el repositorio.

Aqui se reemplazan esos dos mensajes por una linea corta cada uno. La bitacora
del recorrido (lineas VISITA) y los metadatos en SQLite no cambian.
"""
import logging

from scrapy.logformatter import LogFormatter


def _resumen(item):
    """Una linea con lo identificador del documento, nunca su texto."""
    return "{} | {} palabras | dens {} | {}".format(
        item.get("dominio", "-"),
        item.get("palabras", 0),
        item.get("densidad_tema", 0),
        (item.get("url_final") or item.get("url") or "-")[:160],
    )


class FormatoBreve(LogFormatter):
    """Mismos eventos que el formateador de Scrapy, sin volcar el item."""

    def scraped(self, item, response, spider):
        return {
            "level": logging.INFO,
            "msg": "GUARDADO %(resumen)s",
            "args": {"resumen": _resumen(item)},
        }

    def dropped(self, item, exception, response, spider):
        # Un descarte es una POLITICA aplicandose, no un problema: baja de
        # WARNING a INFO para que el log no parezca lleno de alarmas.
        return {
            "level": logging.INFO,
            "msg": "DESCARTE %(motivo)s",
            "args": {"motivo": str(exception)[:220]},
        }

    def item_error(self, item, exception, response, spider):
        return {
            "level": logging.ERROR,
            "msg": "ERROR DE PIPELINE %(resumen)s",
            "args": {"resumen": _resumen(item)},
        }
