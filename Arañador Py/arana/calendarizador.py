"""Calendarizador: decide que URL se visita y cuando.

Se divide como en clase:
    Largo plazo (que visitar y con que prioridad)
        P1  lista blanca de dominios (los descubiertos se evaluan con su primera pagina)
        P4  frontera como cola de prioridad (hereda el puntaje de la pagina y suma el anchor text)
        P5  profundidad maxima
        P6  URLs vistas: una URL no se encola dos veces
        P12 cuota por dominio
    Corto plazo (cortesia: optimizar la red o ser corteses)
        P11 una conexion a la vez por host y al menos `retardo_host_s` entre peticiones
            (esquema de colas por host del arañador Mercator)

Es la estructura compartida por todos los hilos, asi que cada metodo publico toma el candado.
"""

import hashlib
import heapq
import itertools
import threading
import time
from collections import Counter
from dataclasses import dataclass
from urllib.parse import urlsplit

from . import procesamiento as proc

PRIORIDAD_SEMILLA = 100.0
PENALIZACION_CUOTA = 1000.0       # P12: los dominios pasados de cuota quedan al final de la fila


@dataclass
class Tarea:
    url: str                # normalizada
    url_original: str
    url_padre: str
    anchor: str
    semilla: str
    host: str
    dominio: str
    profundidad: int
    prioridad: float
    # Solo en revisitas (P9): documento ya guardado y sus validadores HTTP
    doc_id: int = None
    etag: str = ""
    last_modified: str = ""
    ruta: str = ""
    tamano_anterior: int = 0


def _clave(url):
    """Huella de 64 bits: el conjunto de URLs vistas ocupa mucho menos que guardar las URLs completas."""
    return int.from_bytes(hashlib.blake2b(url.encode(), digest_size=8).digest(), "big")


class Calendarizador:
    def __init__(self, p, almacen, tema, dominios_semilla, dominios_lista, reevaluar_dominios=False):
        self.p = p
        self.almacen = almacen
        self.tema = tema
        self.candado = threading.Lock()
        self.colas = {}                  # host -> heap de (-prioridad, contador, Tarea)
        self.listo_en = {}               # host -> instante (monotonic) desde el que se puede volver a pedir
        self.crawl_delay = {}            # host -> Crawl-delay de su robots.txt
        self.en_vuelo = set()            # hosts con una peticion en curso
        self.vistas = set()              # P6
        self.contador = itertools.count()
        self.n_pendientes = 0
        self.docs_por_dominio = Counter(almacen.docs_por_dominio())
        self.contadores = Counter()      # por que se descartaron enlaces (para el resumen)
        self.fallos_eval = Counter()     # P1: intentos fallidos de evaluar un dominio candidato

        # dominio -> aprobado | candidato | rechazado. Un candidato de una corrida anterior que no llego a
        # evaluarse se olvida: volvera a entrar como candidato cuando alguien lo enlace.
        estados_validos = ("aprobado",) if reevaluar_dominios else ("aprobado", "rechazado")
        self.dominios = {d: e for d, e in almacen.cargar_dominios().items() if e in estados_validos}
        for d in dominios_semilla:
            self._aprobar(d, "semilla")
        for d in dominios_lista:
            if d not in self.dominios:
                self._aprobar(d, "lista")

    # ---- P1: lista blanca ------------------------------------------------------

    def _aprobar(self, dominio, origen):
        self.dominios[dominio] = "aprobado"
        self.almacen.guardar_dominio(dominio, origen, "aprobado")

    def _es_elegible(self, host):
        return host.split(":")[0].endswith(tuple(self.p.sufijos_elegibles))

    def _dominio_admitido(self, host, dominio):
        """Politica P1. Un dominio descubierto con sufijo .edu/.gov/... pasa a 'candidato': se deja entrar
        una sola URL y su primera pagina decide (resolver_dominio)."""
        estado = self.dominios.get(dominio)
        if estado == "aprobado":
            return True
        if estado is None and self._es_elegible(host):
            self.dominios[dominio] = "candidato"
            self.almacen.guardar_dominio(dominio, "descubierto", "candidato")
            return True
        return False                      # rechazado, en evaluacion o fuera de la lista blanca

    def es_candidato(self, dominio):
        with self.candado:
            return self.dominios.get(dominio) == "candidato"

    def resolver_dominio(self, dominio, relevante, evaluada=True):
        """La pagina de un dominio descubierto decide si entra a la lista blanca (P1 + P3).

        Una sola pagina es poca evidencia (una universidad entera puede quedar fuera por mirar su portada), asi que un
        dominio se rechaza hasta despues de `max_evaluaciones_dominio` paginas sin exito, ya sea porque no eran del tema o
        porque no se pudieron evaluar (403, 404, robots, redireccion). Entre evaluaciones solo se encola una URL suya.
        """
        with self.candado:
            if self.dominios.get(dominio) != "candidato":
                return
            if relevante:
                nuevo = "aprobado"
            else:
                self.fallos_eval[dominio] += 1
                nuevo = "rechazado" if self.fallos_eval[dominio] >= self.p.max_evaluaciones_dominio else "sin_evaluar"
            if nuevo == "sin_evaluar":
                del self.dominios[dominio]
            else:
                self.dominios[dominio] = nuevo
        self.almacen.guardar_dominio(dominio, "descubierto", nuevo)

    # ---- largo plazo: encolar ----------------------------------------------------

    def agregar_semilla(self, url):
        normal = proc.normalizar_url(url)
        if not normal:
            return False
        host = urlsplit(normal).netloc
        return self._encolar_filas([Tarea(normal, url, "", "", normal, host, proc.dominio_de(host),
                                          0, PRIORIDAD_SEMILLA)])

    def cargar_pendientes(self, filas):
        """Reanudacion: vuelve a meter en la frontera lo que quedo pendiente en la base."""
        with self.candado:
            for clave in self.almacen.cargar_vistas():
                self.vistas.add(_clave(clave))
            trampas = []
            por_host = Counter(f[5] for f in filas)
            for (url, original, padre, anchor, semilla, host, dominio, prof, prio) in filas:
                if self._es_trampa(url) or proc.es_tienda(url):          # filtros nuevos sobre lo ya pendiente
                    trampas.append(url)
                    continue
                if por_host[host] > self.p.host_saturado and self.tema.bonus_anchor((anchor or "") + " " + url) == 0:
                    trampas.append(url)                                  # un host que domina la cola: solo lo que habla del tema
                    continue
                self._meter(Tarea(url, original, padre, anchor, semilla, host, dominio, prof, prio))
        self.almacen.descartar_pendientes(trampas, "trampa")
        self.contadores["trampa_al_reanudar"] += len(trampas)

    def encolar(self, enlaces, padre, puntaje_padre, profundidad=None):
        """Politicas P1, P2 (extension), P4, P5, P6. enlaces: [(url_normalizada, anchor)]."""
        profundidad = padre.profundidad + 1 if profundidad is None else profundidad
        if profundidad > self.p.profundidad_maxima:                      # P5
            self.contadores["profundidad"] += len(enlaces)
            return 0
        tareas = []
        # Los enlaces con mas terminos del tema en su anchor o URL van primero: si el dominio es nuevo, la URL que
        # lo evalua (P1) es la mas prometedora y no una portada cualquiera.
        puntuados = sorted(((self.tema.bonus_anchor(a + " " + u), u, a) for u, a in enlaces), key=lambda e: -e[0])
        if len(enlaces) > self.p.enlaces_densos:             # p. ej. un articulo de Wikipedia: ~1800 enlaces, casi todos de otros temas
            self.contadores["denso_sin_tema"] += sum(1 for b, _, _ in puntuados if b == 0)
            puntuados = [e for e in puntuados if e[0] > 0]
        enlaces = [(u, a) for _, u, a in puntuados]
        with self.candado:
            for url, anchor in enlaces:
                if _clave(url) in self.vistas:                           # P6
                    continue
                if proc.es_tienda(url):                                  # P1: tiendas
                    self.contadores["tienda"] += 1
                    continue
                if self._es_trampa(url):                                 # P5/P6: busquedas, login, adjuntos
                    self.contadores["trampa"] += 1
                    continue
                if proc.extension_bloqueada(url):                        # P2
                    self.contadores["extension"] += 1
                    continue
                host = urlsplit(url).netloc
                dominio = proc.dominio_de(host)
                if not self._dominio_admitido(host, dominio):            # P1
                    self.contadores["dominio"] += 1
                    continue
                # P4: hereda el puntaje de la pagina donde aparecio y suma si el anchor o la URL hablan del tema
                prioridad = puntaje_padre + 5 * self.tema.bonus_anchor(anchor + " " + url)
                tareas.append(Tarea(url, url, padre.url, anchor, padre.semilla, host, dominio,
                                    profundidad, prioridad))
                self.vistas.add(_clave(url))
                self._meter(tareas[-1])
        self._persistir(tareas)
        return len(tareas)

    def encolar_redireccion(self, destino, padre):
        """Una redireccion se trata como un enlace mas (misma profundidad), para que pase por los filtros."""
        normal = proc.normalizar_url(destino)
        if not normal:
            return 0
        return self.encolar([(normal, padre.anchor)], padre, padre.prioridad, profundidad=padre.profundidad)

    def _es_trampa(self, url):
        return proc.es_trampa(url, self.p.rutas_excluidas, self.p.hosts_excluidos, self.p.parametros_excluidos,
                              self.p.patron_hosts_excluidos)

    def encolar_revisita(self, tareas):
        """Politica P9: mete en la frontera documentos ya guardados para pedirlos con GET condicional."""
        with self.candado:
            for t in tareas:
                self._meter(t)

    def _encolar_filas(self, tareas):
        with self.candado:
            nuevas = []
            for t in tareas:
                if _clave(t.url) not in self.vistas:
                    self.vistas.add(_clave(t.url))
                    self._meter(t)
                    nuevas.append(t)
        self._persistir(nuevas)
        return bool(nuevas)

    def _meter(self, t):
        heapq.heappush(self.colas.setdefault(t.host, []), (-t.prioridad, next(self.contador), t))
        self.n_pendientes += 1

    def _persistir(self, tareas):
        self.almacen.registrar_urls([(t.url, t.url_original, t.url_padre, t.anchor, t.semilla, t.host,
                                      t.dominio, t.profundidad, t.prioridad) for t in tareas])

    # ---- corto plazo: siguiente URL y cortesia -----------------------------------

    def _cuota_excedida(self, dominio):
        """Politica P12 (despues de un calentamiento, para que la regla no sea 100 % al inicio)."""
        total = self.almacen.n_docs
        if total < self.p.calentamiento_docs:
            return False
        return self.docs_por_dominio[dominio] / total > self.p.cuota_dominio

    def siguiente(self):
        """Devuelve (tarea, 0) o (None, segundos_hasta_que_algun_host_este_listo).

        Politica P11: solo se entregan hosts sin peticion en curso y cuyo retardo ya vencio.
        Entre ellos se elige la URL de mayor prioridad (P4); P12 baja la prioridad de dominios pasados de cuota.
        """
        with self.candado:
            ahora = time.monotonic()
            mejor, mejor_p, proxima = None, None, None
            for host, cola in self.colas.items():
                if host in self.en_vuelo:
                    continue
                listo = self.listo_en.get(host, 0.0)
                if listo > ahora:
                    proxima = listo if proxima is None else min(proxima, listo)
                    continue
                tarea = cola[0][2]
                prioridad = tarea.prioridad - (PENALIZACION_CUOTA if self._cuota_excedida(tarea.dominio) else 0)
                if mejor is None or prioridad > mejor_p:
                    mejor, mejor_p = host, prioridad
            if mejor is None:
                return None, (max(proxima - ahora, 0.0) if proxima is not None else 0.5)
            cola = self.colas[mejor]
            tarea = heapq.heappop(cola)[2]
            if not cola:
                del self.colas[mejor]
            self.n_pendientes -= 1
            self.en_vuelo.add(mejor)
            return tarea, 0.0

    def fijar_crawl_delay(self, host, segundos):
        with self.candado:
            self.crawl_delay[host] = segundos

    def terminar(self, tarea, espera_extra=0.0):
        """Libera el host y fija cuando se puede volver a pedir (P11; espera_extra viene de Retry-After, P14)."""
        with self.candado:
            self.en_vuelo.discard(tarea.host)
            espera = max(self.p.retardo_host_s, self.crawl_delay.get(tarea.host) or 0.0, espera_extra)
            self.listo_en[tarea.host] = time.monotonic() + espera

    def registrar_guardado(self, dominio):
        with self.candado:
            self.docs_por_dominio[dominio] += 1

    def estado(self):
        with self.candado:
            return self.n_pendientes, len(self.en_vuelo), len(self.colas)

    def terminado(self):
        with self.candado:
            return self.n_pendientes == 0 and not self.en_vuelo
