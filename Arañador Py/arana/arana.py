"""PaleoBuscaBot: modulo de control del arañador.

Politica P15: un grupo de hilos trabajadores toma URLs del calendarizador, las descarga,
las procesa y las guarda. Cada decision queda en la bitacora (logs/bitacora.log).

Uso:
    python -m arana                      # corrida normal (hasta objetivo_gb o hasta Ctrl+C)
    python -m arana --max-docs 200       # corrida corta de prueba
    python -m arana --duracion-min 480   # se detiene solo a las 8 horas
    python -m arana --datos D:/datos     # guardar el repositorio en otro disco
Para parar con calma: Ctrl+C, o crear un archivo llamado PARAR dentro de la carpeta de datos.
Al volver a ejecutar, continua donde quedo (la frontera esta guardada en SQLite).
"""

import argparse
import logging
import shutil
import threading
import time
import tomllib
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from . import descargador, procesamiento as proc
from .almacen import Almacen, ahora
from .calendarizador import Calendarizador, Tarea

RAIZ = Path(__file__).resolve().parent.parent
PALABRAS_NOTICIA = ("/news", "/noticia", "/blog", "/press", "/story", "/stories", "/magazine")

bitacora = logging.getLogger("bitacora")


# ---------------------------------------------------------------------------
# Parametros
# ---------------------------------------------------------------------------

@dataclass
class Parametros:
    """Valores de config/parametros.toml (el 'archivo de configuracion de politicas' de la diapositiva 6)."""
    correo_contacto: str = "CAMBIAR@correo.com"
    hilos: int = 32
    objetivo_gb: float = 10.0
    carpeta_datos: str = "datos"
    min_disco_libre_gb: float = 2.0
    profundidad_maxima: int = 6
    min_palabras: int = 150
    umbral_tematico: float = 8.0
    cuota_dominio: float = 0.15
    calentamiento_docs: int = 500
    max_bytes_descarga: int = 40_000_000
    max_paginas_pdf: int = 300
    procesos_pdf: int = 2
    sufijos_elegibles: list = field(default_factory=list)
    retardo_host_s: float = 1.5
    timeout_s: float = 10
    reintentos: int = 3
    retry_after_max_s: float = 60
    robots_ttl_s: float = 86400
    rutas_excluidas: list = field(default_factory=list)
    hosts_excluidos: list = field(default_factory=list)
    parametros_excluidos: list = field(default_factory=list)
    patron_hosts_excluidos: str = ""
    revisita_noticia_dias: float = 1
    revisita_estable_dias: float = 30


def cargar_parametros(ruta):
    with open(ruta, "rb") as f:
        datos = tomllib.load(f)
    plano = {k: v for seccion in datos.values() for k, v in seccion.items()}
    return Parametros(**plano)


def leer_lista(ruta):
    """Lineas de un archivo de texto sin comentarios (#) ni vacias."""
    with open(ruta, encoding="utf-8") as f:
        return [l.split("#")[0].strip() for l in f if l.split("#")[0].strip()]


# ---------------------------------------------------------------------------
# Contexto compartido por los hilos
# ---------------------------------------------------------------------------

class Contexto:
    def __init__(self, p, almacen, cal, robots, tema, pool_pdf):
        self.p, self.almacen, self.cal, self.robots, self.tema = p, almacen, cal, robots, tema
        self.parar = threading.Event()
        self.pool_pdf = pool_pdf
        self.candado_pool = threading.Lock()
        self.contadores = {"guardados": 0, "descartados": 0}
        self.motivos = {}
        self.candado_contadores = threading.Lock()


def registrar(ctx, tarea, codigo, decision, motivo=""):
    """P15: una linea de bitacora por URL procesada: fecha | hilo | url | host | http | decision | motivo."""
    bitacora.info("%s | %s | %s | %s | %s | %s", threading.current_thread().name, tarea.url, tarea.host,
                  codigo, decision, motivo)
    with ctx.candado_contadores:
        ctx.contadores["guardados" if decision == "guardado" else "descartados"] += 1
        if decision != "guardado":
            clave = motivo.split(":")[0]
            ctx.motivos[clave] = ctx.motivos.get(clave, 0) + 1


def descartar(ctx, tarea, resp, motivo, duplicado_de=None):
    codigo = resp.codigo if resp else 0
    registrar(ctx, tarea, codigo, "descartado", motivo)
    ctx.almacen.marcar_url(tarea.url, "descartado", motivo, codigo, resp.intentos if resp else 0,
                           resp.error if resp else "", duplicado_de)


def pdf_a_texto(ctx, datos):
    """Los PDF se procesan en procesos aparte: extraer texto gasta CPU y los hilos comparten el GIL."""
    for _ in range(2):
        try:
            with ctx.candado_pool:
                pool = ctx.pool_pdf
            return pool.submit(proc.pdf_a_texto, datos, ctx.p.max_paginas_pdf).result(timeout=180)
        except BrokenProcessPool:
            with ctx.candado_pool:
                ctx.pool_pdf = ProcessPoolExecutor(max_workers=ctx.p.procesos_pdf)
        except Exception:
            return None
    return None


# ---------------------------------------------------------------------------
# Pipeline de una URL
# ---------------------------------------------------------------------------

def verificar_robots(ctx, tarea):
    """Politica P10: True si robots.txt permite la URL. Respeta Crawl-delay y espera tras bajar robots.txt."""
    permitido, delay, pidio_robots = ctx.robots.consultar(tarea.url)
    if delay:
        ctx.cal.fijar_crawl_delay(tarea.host, delay)
    if pidio_robots:                         # la peticion de robots.txt cuenta: esperar antes de la pagina
        ctx.parar.wait(max(ctx.p.retardo_host_s, delay or 0))
    return permitido


def extraer_texto(ctx, resp, url):
    """Politica P2: respuesta -> (titulo, texto, enlaces, noindex, nofollow); None si el PDF no se puede leer."""
    if resp.content_type == "application/pdf":
        resultado = pdf_a_texto(ctx, resp.cuerpo)
        return None if resultado is None else (resultado[0], resultado[1], [], False, False)
    if resp.content_type == "text/plain":
        return "", proc.extraer_texto_plano(resp.cuerpo, resp.content_type_completo).texto, [], False, False
    pagina = proc.extraer_html(resp.cuerpo, resp.content_type_completo, url)
    return pagina.titulo, pagina.texto, pagina.enlaces, pagina.noindex, pagina.nofollow


def revisitar(ctx, tarea):
    """Politica P9: GET condicional de un documento ya guardado (If-None-Match / If-Modified-Since)."""
    p, alm = ctx.p, ctx.almacen
    if not verificar_robots(ctx, tarea):
        registrar(ctx, tarea, 0, "revisita", "robots")
        return 0.0
    resp = descargador.descargar(tarea.url, p, {"etag": tarea.etag, "last_modified": tarea.last_modified},
                                 detener=ctx.parar)
    if resp.codigo == 304:
        alm.tocar_documento(tarea.doc_id)
        registrar(ctx, tarea, 304, "revisita", "sin_cambios")
    elif resp.cuerpo is None:
        registrar(ctx, tarea, resp.codigo, "revisita", f"fallida:{resp.rechazo or resp.error or 'redireccion'}")
    else:
        extraido = extraer_texto(ctx, resp, tarea.url)
        if extraido is None or not extraido[1].strip():
            registrar(ctx, tarea, resp.codigo, "revisita", "fallida:texto_vacio")
            return resp.retry_after
        titulo, texto = extraido[0], extraido[1]
        contenido = f"{titulo}\n\n{texto}\n" if titulo else f"{texto}\n"
        puntaje, terminos = ctx.tema.puntaje(texto)
        palabras = proc.contar_palabras(texto)
        if puntaje < p.umbral_tematico or palabras < p.min_palabras:
            registrar(ctx, tarea, resp.codigo, "revisita", "fallida:ya_no_cumple_filtros")
            return resp.retry_after
        meta = dict(titulo=titulo, num_palabras=palabras, hash_sha256=proc.hash_texto(texto),
                    puntaje_tematico=puntaje, terminos_encontrados=",".join(terminos), content_type=resp.content_type,
                    tamano_bytes_original=resp.tamano, codigo_http=resp.codigo, intentos=resp.intentos,
                    last_modified=resp.last_modified, etag=resp.etag)
        if alm.actualizar_documento(tarea.doc_id, tarea.ruta, tarea.tamano_anterior, meta, contenido):
            registrar(ctx, tarea, resp.codigo, "revisita", "actualizado")
        else:
            alm.tocar_documento(tarea.doc_id)
            registrar(ctx, tarea, resp.codigo, "revisita", "sin_cambios_de_contenido")
    return resp.retry_after


def procesar(ctx, tarea):
    """Descarga y procesa una URL. Devuelve segundos extra que el host debe descansar (P14)."""
    if tarea.doc_id is not None:
        return revisitar(ctx, tarea)
    p, cal, alm = ctx.p, ctx.cal, ctx.almacen

    if not verificar_robots(ctx, tarea):
        cal.resolver_dominio(tarea.dominio, False, evaluada=False)
        descartar(ctx, tarea, None, "robots")
        return 0.0

    # P13, P14, P2: descarga
    resp = descargador.descargar(tarea.url, p, detener=ctx.parar)
    if resp.redireccion:
        cal.resolver_dominio(tarea.dominio, False, evaluada=False)     # el destino volvera a pasar por P1
        descartar(ctx, tarea, resp, "redireccion")
        cal.encolar_redireccion(resp.redireccion, tarea)
        return resp.retry_after
    if resp.rechazo or resp.cuerpo is None:
        cal.resolver_dominio(tarea.dominio, False, evaluada=False)
        if resp.rechazo:
            motivo = f"{resp.rechazo}:{resp.content_type}" if resp.rechazo == "content_type" else resp.rechazo
        else:
            motivo = f"error:{resp.error or resp.codigo}"
        descartar(ctx, tarea, resp, motivo)
        return resp.retry_after

    extraido = extraer_texto(ctx, resp, tarea.url)
    if extraido is None:
        cal.resolver_dominio(tarea.dominio, False, evaluada=False)
        descartar(ctx, tarea, resp, "pdf_ilegible")
        return 0.0
    titulo, texto, enlaces, noindex, nofollow = extraido

    palabras = proc.contar_palabras(texto)
    puntaje, terminos = ctx.tema.puntaje(texto)                          # P3
    relevante = puntaje >= p.umbral_tematico
    cal.resolver_dominio(tarea.dominio, relevante)                       # P1: primera pagina de un dominio nuevo

    # Solo se siguen los enlaces de paginas relevantes (P3). Las semillas se consideran relevantes por diseño.
    if (relevante or tarea.profundidad == 0) and not nofollow and enlaces:
        cal.encolar(enlaces, tarea, puntaje)

    if noindex:
        descartar(ctx, tarea, resp, "noindex")
    elif not relevante:
        descartar(ctx, tarea, resp, f"fuera_de_tema:{puntaje:.1f}")
    elif palabras < p.min_palabras:                                      # P8
        descartar(ctx, tarea, resp, f"muy_corto:{palabras}")
    else:
        hash_doc = proc.hash_texto(texto)                                # P7
        duplicado = alm.existe_hash(hash_doc)
        if duplicado is None:
            contenido = f"{titulo}\n\n{texto}\n" if titulo else f"{texto}\n"
            meta = dict(
                url_original=tarea.url_original, url_normalizada=tarea.url, url_padre=tarea.url_padre,
                anchor_text=tarea.anchor, semilla_origen=tarea.semilla, dominio=tarea.dominio, host=tarea.host,
                profundidad=tarea.profundidad, prioridad=tarea.prioridad, puntaje_tematico=puntaje,
                terminos_encontrados=",".join(terminos), es_relevante=1, content_type=resp.content_type,
                tamano_bytes_original=resp.tamano, codigo_http=resp.codigo, intentos=resp.intentos,
                error=resp.error, fecha_descarga=ahora(), last_modified=resp.last_modified, etag=resp.etag,
                tipo_pagina="noticia" if any(w in tarea.url.lower() for w in PALABRAS_NOTICIA) else "estable",
                titulo=titulo, num_palabras=palabras, hash_sha256=hash_doc, robots_permitido=1)
            doc_id, duplicado = alm.guardar_documento(meta, contenido)
            if doc_id is not None:
                cal.registrar_guardado(tarea.dominio)
                registrar(ctx, tarea, resp.codigo, "guardado", f"id={doc_id} palabras={palabras}")
                alm.marcar_url(tarea.url, "hecho", f"id={doc_id}", resp.codigo, resp.intentos)
        if duplicado is not None:
            descartar(ctx, tarea, resp, f"duplicado:id={duplicado}", duplicado)
    return resp.retry_after


def trabajador(ctx):
    cal = ctx.cal
    while not ctx.parar.is_set():
        tarea, espera = cal.siguiente()
        if tarea is None:
            if cal.terminado():
                break
            ctx.parar.wait(min(max(espera, 0.05), 0.5))
            continue
        extra = 0.0
        try:
            extra = procesar(ctx, tarea)
        except Exception as e:                      # un error en una pagina no debe tumbar el hilo
            try:
                descartar(ctx, tarea, None, f"excepcion:{type(e).__name__}:{e}")
            except Exception:
                pass
        finally:
            cal.terminar(tarea, extra)


# ---------------------------------------------------------------------------
# Programa principal
# ---------------------------------------------------------------------------

def configurar_bitacora(carpeta):
    (carpeta / "logs").mkdir(parents=True, exist_ok=True)
    manejador = logging.FileHandler(carpeta / "logs" / "bitacora.log", encoding="utf-8")
    manejador.setFormatter(logging.Formatter("%(asctime)s | %(message)s", "%Y-%m-%d %H:%M:%S"))
    bitacora.addHandler(manejador)
    bitacora.setLevel(logging.INFO)
    bitacora.propagate = False


def resumen(ctx, inicio):
    alm = ctx.almacen
    pend, en_vuelo, hosts = ctx.cal.estado()
    minutos = (time.time() - inicio) / 60
    print(f"[{time.strftime('%H:%M:%S')}] {minutos:6.1f} min | docs={alm.n_docs} ({alm.bytes_texto / 1e9:.3f} GB) | "
          f"guardados={ctx.contadores['guardados']} descartados={ctx.contadores['descartados']} | "
          f"pendientes={pend} en_vuelo={en_vuelo} hosts={hosts}", flush=True)


def construir(p, carpeta, semillas, dominios_lista, tema, reanudar=True):
    """Crea almacen, calendarizador y contexto, y carga la frontera (pendientes de corridas anteriores + semillas)."""
    dominios_semilla = {proc.dominio_de(urlsplit(n).netloc) for n in map(proc.normalizar_url, semillas) if n}
    almacen = Almacen(carpeta)
    cal = Calendarizador(p, almacen, tema, dominios_semilla, dominios_lista)
    if reanudar:
        cal.cargar_pendientes(almacen.cargar_pendientes())
        for url in semillas:
            cal.agregar_semilla(url)
    pool_pdf = ProcessPoolExecutor(max_workers=p.procesos_pdf)
    return Contexto(p, almacen, cal, descargador.Robots(p, almacen), tema, pool_pdf)


def tareas_de_revisita(almacen, p, limite):
    """P9: documentos vencidos segun su tipo (noticia: mas seguido; ficha estable: mas espaciado)."""
    filas = almacen.documentos_a_revisar(p.revisita_noticia_dias, p.revisita_estable_dias, limite)
    return [Tarea(url=url, url_original=original, url_padre="", anchor="", semilla=semilla, host=host,
                  dominio=dominio, profundidad=prof, prioridad=0.0, doc_id=doc_id, etag=etag or "",
                  last_modified=modificado or "", ruta=ruta, tamano_anterior=tamano or 0)
            for (doc_id, url, original, dominio, host, etag, modificado, semilla, prof, tamano, ruta) in filas]


def main(argv=None):
    ap = argparse.ArgumentParser(description="PaleoBuscaBot: arañador enfocado en dinosaurios y paleontologia.")
    ap.add_argument("--config", default=str(RAIZ / "config" / "parametros.toml"))
    ap.add_argument("--datos", help="carpeta de datos (por defecto, la del archivo de configuracion)")
    ap.add_argument("--hilos", type=int)
    ap.add_argument("--max-docs", type=int, help="parar al llegar a este numero de documentos guardados")
    ap.add_argument("--objetivo-gb", type=float)
    ap.add_argument("--duracion-min", type=float, help="parar despues de estos minutos")
    ap.add_argument("--revisita", action="store_true",
                    help="P9: en vez de arañar sitios nuevos, revisar con GET condicional los documentos ya guardados")
    ap.add_argument("--max-revisitas", type=int, default=100_000)
    ap.add_argument("--permitir-correo-falso", action="store_true",
                    help="solo para pruebas locales: no exigir un correo real en el User-Agent")
    args = ap.parse_args(argv)

    p = cargar_parametros(args.config)
    if args.hilos:
        p.hilos = args.hilos
    if args.objetivo_gb:
        p.objetivo_gb = args.objetivo_gb
    if "CAMBIAR" in p.correo_contacto and not args.permitir_correo_falso:
        print("Antes de arañar hay que poner un correo real en config/parametros.toml (correo_contacto): "
              "va en el User-Agent para que los administradores de los sitios puedan contactar al grupo (P13).")
        return 2

    carpeta = Path(args.datos or p.carpeta_datos)
    if not carpeta.is_absolute() and not args.datos:
        carpeta = RAIZ / carpeta
    carpeta.mkdir(parents=True, exist_ok=True)
    configurar_bitacora(carpeta)

    config = RAIZ / "config"
    semillas = leer_lista(config / "semillas.txt") + leer_lista(config / "semillas_extra.txt")
    tema = proc.Tema(config / "terminos_tema.txt")
    ctx = construir(p, carpeta, semillas, leer_lista(config / "dominios_permitidos.txt"), tema,
                    reanudar=not args.revisita)
    almacen, cal = ctx.almacen, ctx.cal
    if args.revisita:
        cal.encolar_revisita(tareas_de_revisita(almacen, p, args.max_revisitas))

    meta_bytes = p.objetivo_gb * 1e9
    archivo_parar = carpeta / "PARAR"
    archivo_parar.unlink(missing_ok=True)
    inicio = time.time()
    print(f"Arañador iniciado: {p.hilos} hilos, datos en {carpeta}, "
          f"{almacen.n_docs} documentos previos, {cal.estado()[0]} URLs pendientes.", flush=True)

    with ThreadPoolExecutor(max_workers=p.hilos, thread_name_prefix="H") as hilos:
        futuros = [hilos.submit(trabajador, ctx) for _ in range(p.hilos)]
        try:
            ultimo_resumen = 0.0
            while not all(f.done() for f in futuros):
                time.sleep(1)
                if time.time() - ultimo_resumen >= 30:
                    resumen(ctx, inicio)
                    ultimo_resumen = time.time()
                if ctx.parar.is_set():
                    continue
                if almacen.bytes_texto >= meta_bytes:
                    print("Objetivo de tamaño alcanzado.")
                    ctx.parar.set()
                elif args.max_docs and ctx.contadores["guardados"] >= args.max_docs:
                    ctx.parar.set()
                elif args.duracion_min and time.time() - inicio >= args.duracion_min * 60:
                    print("Duracion maxima alcanzada.")
                    ctx.parar.set()
                elif shutil.disk_usage(carpeta).free < p.min_disco_libre_gb * 1e9:
                    print(f"Quedan menos de {p.min_disco_libre_gb} GB libres en el disco: se detiene para no llenarlo.")
                    ctx.parar.set()
                elif archivo_parar.exists():
                    print("Archivo PARAR encontrado.")
                    ctx.parar.set()
        except KeyboardInterrupt:
            print("\nCtrl+C: terminando las descargas en curso...", flush=True)
            ctx.parar.set()
            for f in futuros:
                f.result()

    resumen(ctx, inicio)
    print("Motivos de descarte:", dict(sorted(ctx.motivos.items(), key=lambda kv: -kv[1])))
    print("Enlaces ignorados:", dict(cal.contadores))
    ctx.pool_pdf.shutdown(wait=False, cancel_futures=True)
    almacen.cerrar()
    return 0
