"""Estructura de un documento del repositorio.

Cada campo de este Item es un metadato que la documentacion exige almacenar
para poder justificar las politicas de aranado (seccion 3 del documento).
"""
import scrapy


class DocumentoItem(scrapy.Item):
    # --- identidad y procedencia (politica de alcance y bitacora) ---
    url = scrapy.Field()            # URL solicitada
    url_final = scrapy.Field()      # URL tras redirecciones
    dominio = scrapy.Field()
    referer = scrapy.Field()        # quien enlazo este documento
    profundidad = scrapy.Field()    # saltos desde la semilla
    es_semilla = scrapy.Field()       # esta URL estaba en seeds/urls.txt
    dominio_semilla = scrapy.Field()  # el dominio estaba en seeds/urls.txt

    # --- respuesta HTTP (politica de recursos y refresco) ---
    http_status = scrapy.Field()
    content_type = scrapy.Field()
    bytes_html = scrapy.Field()
    last_modified = scrapy.Field()
    etag = scrapy.Field()
    fecha_descarga = scrapy.Field()

    # --- contenido (politica de seleccion tematica e idioma) ---
    titulo = scrapy.Field()
    idioma = scrapy.Field()
    texto = scrapy.Field()
    palabras = scrapy.Field()
    bytes_texto = scrapy.Field()
    hash_texto = scrapy.Field()     # sha256, usado para deduplicar
    puntaje_tema = scrapy.Field()   # terminos distintos del tema (variedad)
    densidad_tema = scrapy.Field()  # menciones del tema por 1000 palabras

    # --- ubicacion en el repositorio ---
    ruta_archivo = scrapy.Field()

    # campo interno: HTML crudo que la pipeline de limpieza consume y descarta
    html_crudo = scrapy.Field()
