"""Descargador (diapositivas 5 y 6): peticiones HTTP corteses y lectura de robots.txt.

Politicas que se implementan aqui:
    P2  solo se lee el cuerpo si el Content-Type es texto/HTML/PDF
    P9  GET condicional (If-None-Match / If-Modified-Since)
    P10 robots.txt: no se descarga lo prohibido y se respeta Crawl-delay
    P13 User-Agent propio y verificacion de certificados SSL
    P14 timeout, reintentos con espera exponencial y Retry-After

Cambios respecto a download_html.py de la Tarea02: ya no se finge ser Chrome ni se desactiva
la verificacion SSL (P13), y hay reintentos (P14).
"""

import http.client
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
import urllib.robotparser
import zlib
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

TIPOS_ACEPTADOS = {"text/html", "application/xhtml+xml", "text/plain", "application/pdf"}
NOMBRE_ROBOT = "PaleoBuscaBot"


class _SinRedireccion(urllib.request.HTTPRedirectHandler):
    """No seguir redirecciones solas: la URL nueva debe pasar otra vez por P1, P5, P6 y robots.txt."""

    def redirect_request(self, *args, **kwargs):
        return None


_ABRIDOR = urllib.request.build_opener(
    _SinRedireccion, urllib.request.HTTPSHandler(context=ssl.create_default_context()))   # P13: SSL verificado


def user_agent(correo):
    return f"{NOMBRE_ROBOT}/1.0 (+{correo})"            # P13


@dataclass
class Respuesta:
    url: str
    codigo: int = 0                 # 0 = no hubo respuesta HTTP
    content_type: str = ""          # tipo sin parametros: text/html
    content_type_completo: str = "" # con charset
    cuerpo: bytes = None
    tamano: int = 0
    etag: str = ""
    last_modified: str = ""
    redireccion: str = ""
    intentos: int = 0
    error: str = ""
    retry_after: float = 0.0        # segundos que el host debe descansar (P14)
    rechazo: str = ""               # content_type | tamano


def _retry_after(encabezados):
    valor = (encabezados.get("Retry-After") or "").strip()
    return float(valor) if valor.isdigit() else 0.0


def _descomprimir(datos, limite):
    """gzip con limite de tamano (evita bombas de descompresion)."""
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    salida = d.decompress(datos, limite + 1)
    if len(salida) > limite or d.unconsumed_tail:
        raise ValueError("tamano")
    return salida


def _leer(r, resp, max_bytes):
    resp.codigo = r.status
    resp.content_type = r.headers.get_content_type() or ""
    resp.content_type_completo = r.headers.get("Content-Type", "")
    resp.etag = r.headers.get("ETag", "")
    resp.last_modified = r.headers.get("Last-Modified", "")
    if resp.content_type not in TIPOS_ACEPTADOS:                     # P2: no se baja el cuerpo
        resp.rechazo = "content_type"
        return resp
    largo = r.headers.get("Content-Length", "")
    if largo.isdigit() and int(largo) > max_bytes:
        resp.rechazo = "tamano"
        return resp
    datos = r.read(max_bytes + 1)
    if len(datos) > max_bytes:
        resp.rechazo = "tamano"
        return resp
    if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
        try:
            datos = _descomprimir(datos, max_bytes)
        except ValueError:
            resp.rechazo = "tamano"
            return resp
    resp.cuerpo, resp.tamano = datos, len(datos)
    return resp


def _pedir(url, p, condicional=None):
    """Una sola peticion, con los encabezados de cortesia. Lanza las excepciones de urllib."""
    encabezados = {"User-Agent": user_agent(p.correo_contacto),
                   "Accept": "text/html,application/xhtml+xml,application/pdf,text/plain;q=0.8",
                   "Accept-Encoding": "gzip"}
    if condicional:                                                  # P9
        if condicional.get("etag"):
            encabezados["If-None-Match"] = condicional["etag"]
        if condicional.get("last_modified"):
            encabezados["If-Modified-Since"] = condicional["last_modified"]
    return _ABRIDOR.open(urllib.request.Request(url, headers=encabezados), timeout=p.timeout_s)


def descargar(url, p, condicional=None, detener=None):
    """Politica P14: hasta p.reintentos reintentos con espera 2, 4, 8 s; respeta Retry-After en 429/503."""
    resp = Respuesta(url=url)
    for intento in range(1, p.reintentos + 2):
        resp.intentos = intento
        espera = 2 ** intento
        try:
            with _pedir(url, p, condicional) as r:
                resp.error = ""
                return _leer(r, resp, p.max_bytes_descarga)
        except urllib.error.HTTPError as e:
            resp.codigo = e.code
            if e.code == 304:
                return resp
            if 300 <= e.code < 400 and e.headers.get("Location"):
                resp.redireccion = urljoin(url, e.headers["Location"])
                return resp
            if e.code in (429, 503):
                pedido = _retry_after(e.headers)
                resp.retry_after = max(pedido, espera)
                resp.error = f"HTTP {e.code}"
                if pedido > p.retry_after_max_s:                     # pide esperar demasiado: no insistir
                    return resp
                espera = max(pedido, espera)
            elif e.code >= 500:
                resp.error = f"HTTP {e.code}"
            else:                                                    # 4xx: se registra y no se reintenta
                resp.error = f"HTTP {e.code}"
                return resp
        except urllib.error.URLError as e:
            resp.error = f"{type(e.reason).__name__}: {e.reason}"
            if isinstance(e.reason, ssl.SSLCertVerificationError):   # certificado malo: no tiene sentido reintentar
                return resp
        except (TimeoutError, socket.timeout, ConnectionError, ssl.SSLError,
                http.client.HTTPException, OSError) as e:
            resp.error = f"{type(e).__name__}: {e}"
        if intento > p.reintentos or (detener and detener.is_set()):
            break
        if detener:
            detener.wait(espera)
        else:
            time.sleep(espera)
    return resp


# ---------------------------------------------------------------------------
# P10: robots.txt
# ---------------------------------------------------------------------------

class Robots:
    """Cache de robots.txt por host. Un host se consulta desde un solo hilo a la vez
    (lo garantiza el calendarizador), asi que no hay descargas repetidas del mismo archivo."""

    def __init__(self, p, almacen):
        self.p = p
        self.almacen = almacen
        self.cache = {}             # host -> (expira, parser, acceso)
        self.candado = threading.Lock()

    def _descargar(self, esquema, host):
        """Devuelve (parser o None, acceso) con acceso en: ok | libre | prohibido."""
        url = f"{esquema}://{host}/robots.txt"
        try:
            with _pedir(url, self.p) as r:
                texto = r.read(512_000).decode("utf-8", errors="replace")
            parser = urllib.robotparser.RobotFileParser()
            parser.parse(texto.splitlines())
            return parser, "ok"
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500 and e.code != 429:
                return None, "libre"                # sin robots.txt: RFC 9309 permite todo
            return None, "prohibido"                # 5xx o 429: se asume prohibido por ahora
        except Exception:
            return None, "prohibido"

    def consultar(self, url):
        """Politica P10. Devuelve (permitido, crawl_delay o None, hizo_peticion)."""
        partes = urlsplit(url)
        host = partes.netloc
        with self.candado:
            entrada = self.cache.get(host)
        hizo_peticion = False
        if entrada is None or entrada[0] < time.time():
            parser, acceso = self._descargar(partes.scheme, host)
            ttl = self.p.robots_ttl_s if acceso != "prohibido" else 900     # reintentar pronto si fallo
            entrada = (time.time() + ttl, parser, acceso)
            with self.candado:
                self.cache[host] = entrada
            delay = parser.crawl_delay(NOMBRE_ROBOT) if parser else None
            self.almacen.guardar_robots(host, delay, acceso)
            hizo_peticion = True
        _, parser, acceso = entrada
        if acceso == "prohibido":
            return False, None, hizo_peticion
        if parser is None:
            return True, None, hizo_peticion
        delay = parser.crawl_delay(NOMBRE_ROBOT)
        return parser.can_fetch(NOMBRE_ROBOT, url), (float(delay) if delay else None), hizo_peticion
