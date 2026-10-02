"""Configuracion del aranador PaleoBusca (Scrapy).

Cada bloque esta rotulado con la politica de aranado que implementa, para
poder senalar en la discusion de resultados "donde y como" se implementa
cada una. Las politicas de contenido estan en policies.py y las de frontera
en spiders/paleo.py.
"""
import sys

BOT_NAME = "paleobusca"
SPIDER_MODULES = ["paleobusca.spiders"]
NEWSPIDER_MODULE = "paleobusca.spiders"

# ===========================================================================
# POLITICA DE IDENTIFICACION
# La arana se identifica con un agente propio y un contacto, para que los
# administradores de los museos puedan escribirnos o bloquearnos si molesta.
# ===========================================================================
USER_AGENT = (
    "PaleoBuscaBot/0.1 (proyecto academico; Instituto Tecnologico de Costa "
    "Rica; Recuperacion de Informacion Textual; +mailto:dmm1462003@gmail.com)"
)

# ===========================================================================
# POLITICA DE CORTESIA (politeness)
# 1. Se obedece robots.txt siempre (protocolo de exclusion de robots).
# 2. Maximo 2 descargas simultaneas por dominio y una espera entre pedidos,
#    aleatorizada para no golpear en rafagas regulares.
# 3. AutoThrottle ajusta la espera segun la latencia observada: si el
#    servidor se pone lento, la arana se frena sola.
# La concurrencia global si es alta (128) porque el paralelismo se logra
# visitando MUCHOS dominios a la vez, no saturando uno: con 29 dominios
# semilla a 2 descargas cada uno, el tope real son ~58 simultaneas.
# ===========================================================================
# ---------------------------------------------------------------------------
# REACTOR DE RED (limitacion de plataforma, no decision de diseno)
# En Windows el reactor por defecto de Scrapy usa select(), que admite como
# maximo 512 descriptores: con alta concurrencia la corrida muere con
# "ValueError: too many file descriptors in select()". El reactor IOCP de
# Twisted usa las APIs asincronas nativas de Windows y no tiene ese tope.
# Medido con 29 dominios semilla: select 3.8 req/s vs. IOCP 15.2 req/s.
# Requiere pywin32 y twisted-iocpsupport (ver requirements.txt).
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    TWISTED_REACTOR = "twisted.internet.iocpreactor.reactor.IOCPReactor"
else:
    TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"

ROBOTSTXT_OBEY = True
CONCURRENT_REQUESTS = 128
CONCURRENT_REQUESTS_PER_DOMAIN = 2
CONCURRENT_REQUESTS_PER_IP = 0
DOWNLOAD_DELAY = 0.5
DOWNLOAD_DELAY_JITTER = 0.5   # +-50% de variacion (antes RANDOMIZE_DOWNLOAD_DELAY)

AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 1.0
AUTOTHROTTLE_MAX_DELAY = 30.0
AUTOTHROTTLE_TARGET_CONCURRENCY = 2.0
AUTOTHROTTLE_DEBUG = False

# MODO VOLUMEN (opcional, se activa por linea de comandos, no aqui):
#   -s CONCURRENT_REQUESTS_PER_DOMAIN=6 -s DOWNLOAD_DELAY=0.15 \n#   -s AUTOTHROTTLE_TARGET_CONCURRENCY=6
# Sube el caudal ~4x a costa de pedirle mas a cada servidor. Se deja fuera
# del archivo a proposito: la politica que el proyecto defiende es la de
# arriba, y usar el modo volumen es una decision consciente por corrida.

# ===========================================================================
# POLITICA DE RECORRIDO (frontera)
# Recorrido en amplitud (BFS): DEPTH_PRIORITY = 1 hace que Scrapy atienda
# primero lo mas cercano a las semillas, que es lo mas relevante al tema.
# La cola se respalda en disco para que la frontera no se coma la RAM en una
# corrida de millones de URLs.
# ===========================================================================
DEPTH_PRIORITY = 1
DEPTH_LIMIT = 6
DEPTH_STATS_VERBOSE = True
SCHEDULER_DISK_QUEUE = "scrapy.squeues.PickleFifoDiskQueue"
SCHEDULER_MEMORY_QUEUE = "scrapy.squeues.FifoMemoryQueue"

# ===========================================================================
# POLITICA DE USO DE RECURSOS
# Se corta cualquier descarga mayor a 8 MB (una pagina de texto nunca lo es),
# se limita el tiempo de espera y los reintentos, y se desactivan cookies
# porque no se necesita sesion para leer articulos publicos.
# ===========================================================================
DOWNLOAD_MAXSIZE = 8 * 1024 * 1024
DOWNLOAD_WARNSIZE = 2 * 1024 * 1024
DOWNLOAD_TIMEOUT = 25
RETRY_ENABLED = True
RETRY_TIMES = 2
RETRY_HTTP_CODES = [500, 502, 503, 504, 522, 524, 408, 429]
REDIRECT_MAX_TIMES = 5
COOKIES_ENABLED = False
TELNETCONSOLE_ENABLED = False
REACTOR_THREADPOOL_MAXSIZE = 20
DNSCACHE_ENABLED = True
DNSCACHE_SIZE = 20000
AJAXCRAWL_ENABLED = False
COMPRESSION_ENABLED = True          # pide gzip: menos ancho de banda al sitio

# ===========================================================================
# POLITICA DE SELECCION DE FORMATO
# Middleware propio: si la respuesta no es HTML/texto, se descarta antes de
# parsear. Es la red de seguridad por si un enlace sin extension resulta ser
# un PDF o un video.
# ===========================================================================
DOWNLOADER_MIDDLEWARES = {
    # 100: antes de resolver DNS y abrir conexion, para que una cola
    # encolada con politicas viejas no cueste ancho de banda.
    "paleobusca.middlewares.FiltroFrontera": 100,
    "paleobusca.middlewares.FiltroTipoContenido": 560,
}

# ===========================================================================
# CADENA DE PROCESAMIENTO DEL DOCUMENTO (item pipelines)
# El orden importa: limpiar -> filtrar -> deduplicar -> guardar texto ->
# registrar metadatos. Se deduplica ANTES de escribir para no gastar disco.
# ===========================================================================
ITEM_PIPELINES = {
    "paleobusca.pipelines.LimpiezaHTMLPipeline": 100,
    "paleobusca.pipelines.FiltroDocumentoPipeline": 200,
    "paleobusca.pipelines.DeduplicacionPipeline": 300,
    "paleobusca.pipelines.RepositorioTextoPipeline": 400,
    "paleobusca.pipelines.MetadatosSQLitePipeline": 500,
}

# ===========================================================================
# BITACORA (rubrica: "debe guardar un log que demuestre el recorrido")
# Log de Scrapy a archivo + estadisticas cada 30 s. Cada pagina visitada se
# registra con nivel INFO como linea VISITA (ver spiders/paleo.py) y ademas
# queda en la tabla documentos/bitacora de SQLite.
# ===========================================================================
# Formateador propio: Scrapy volcaba el Item completo (con el texto del
# documento) por cada guardado y cada descarte, y el log crecio a 74 MB en
# 50 minutos. FormatoBreve deja una linea corta por evento.
LOG_FORMATTER = "paleobusca.logformat.FormatoBreve"

# Progreso compacto en CONSOLA (una linea cada 30 s). Con LOG_FILE puesto,
# Scrapy manda todo al archivo y la consola queda muda; esta extension es
# la que permite vigilar una corrida de toda la noche de un vistazo.
EXTENSIONS = {
    "paleobusca.extensions.ProgresoConsola": 100,
}
PROGRESO_INTERVALO = 30.0         # segundos; 0 lo desactiva

# Lineas VISITA por cada pagina recorrida: son la bitacora que pide la
# rubrica. Ponerlo en False reduce el log a los guardados y descartes.
BITACORA_DETALLADA = True

LOG_LEVEL = "INFO"
LOG_FILE = "logs/crawl.log"
LOG_FILE_APPEND = True
LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
LOGSTATS_INTERVAL = 30.0

# ===========================================================================
# PARAMETROS PROPIOS DE LAS POLITICAS (leidos por spider y pipelines)
# ===========================================================================
MAX_PAGINAS_POR_DOMINIO = 20000   # nadie baja "todo de un solo sitio"
MIN_PALABRAS = 120                # descarta indices, galerias y paginas vacias
MIN_PUNTAJE_TEMA = 6              # variedad: terminos distintos del tema
MIN_DENSIDAD_TEMA = 20.0          # densidad: menciones del tema por 1000 palabras
# Umbral calibrado con una corrida de prueba: los articulos del tema dieron
# entre 60 y 122 menciones por mil palabras, mientras que el ruido de museo
# ("Eat, drink and shop" 14.0, "Our Gardens" 18.5, "Identify nature" 19.2)
# quedo por debajo de 20. Reproducible con scripts/estadisticas.py.
IDIOMAS_ACEPTADOS = ["en"]
PERMITIR_DOMINIOS_NUEVOS = True   # crawling enfocado fuera de las semillas
REPO_DIR = "repo"
DB_PATH = "repo/metadatos.sqlite"
SQLITE_BATCH = 100                # commits agrupados

# ===========================================================================
# META DE TAMANO DEL REPOSITORIO
# Al alcanzar estos GB de texto limpio acumulado (contando corridas previas,
# no solo la actual) la arana se cierra ordenadamente: termina lo que tiene
# en vuelo, hace commit y guarda la frontera en el JOBDIR. 0 = sin limite.
# ===========================================================================
META_GB = 3.0

FEED_EXPORT_ENCODING = "utf-8"
