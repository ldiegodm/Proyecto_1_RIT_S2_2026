"""Pruebas del arañador. Ejecutar con:  python -m pytest -q

Las pruebas de extraccion usan las 10 paginas reales de la Tarea02 (tests/fixtures/);
la de integracion levanta un servidor HTTP local, asi que no necesita internet.
"""

import glob
import http.server
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from arana import descargador, procesamiento as proc
from arana.almacen import Almacen
from arana.arana import Parametros, construir, tareas_de_revisita, trabajador
from arana.calendarizador import Calendarizador, Tarea

RAIZ = Path(__file__).resolve().parent.parent
TEMA = proc.Tema(RAIZ / "config" / "terminos_tema.txt")


def parametros(**cambios):
    p = Parametros(correo_contacto="prueba@example.org", hilos=4, retardo_host_s=0.05, timeout_s=3,
                   reintentos=0, robots_ttl_s=60, procesos_pdf=1, calentamiento_docs=10**9,
                   sufijos_elegibles=[".edu"], profundidad_maxima=3)
    for k, v in cambios.items():
        setattr(p, k, v)
    return p


# ---------------------------------------------------------------------------
# P6, P1, P2: URLs
# ---------------------------------------------------------------------------

def test_normalizar_url():
    n = proc.normalizar_url
    assert n("HTTP://WWW.Example.COM:80/a/b?utm_source=x&b=2&a=1#seccion") == "http://www.example.com/a/b?a=1&b=2"
    assert n("https://example.com:443") == "https://example.com/"
    assert n("https://example.com:8443/x") == "https://example.com:8443/x"
    assert n("../otra", base="https://example.com/a/b/c.html") == "https://example.com/a/otra"
    assert n("https://www.bbc.co.uk/bitesize/articles/zt3ntrd#zjhws82") == "https://www.bbc.co.uk/bitesize/articles/zt3ntrd"
    assert n("https://example.com/p?fbclid=abc&PHPSESSID=1") == "https://example.com/p"
    assert n("mailto:a@b.c") is None and n("ftp://example.com/x") is None and n("javascript:void(0)") is None


def test_dominio_de():
    assert proc.dominio_de("www.nhm.ac.uk") == "nhm.ac.uk"
    assert proc.dominio_de("ucmp.berkeley.edu") == "berkeley.edu"
    assert proc.dominio_de("australian.museum") == "australian.museum"
    assert proc.dominio_de("www.nps.gov:8080") == "nps.gov"
    assert proc.dominio_de("www.bbc.co.uk") == "bbc.co.uk"


def test_extension_bloqueada():
    assert proc.extension_bloqueada("https://x.org/foto.JPG")
    assert proc.extension_bloqueada("https://x.org/a/estilo.css?v=2")
    assert not proc.extension_bloqueada("https://x.org/paper.pdf")
    assert not proc.extension_bloqueada("https://x.org/articulo")
    assert not proc.extension_bloqueada("https://x.org/articulo.html")


# ---------------------------------------------------------------------------
# P2, P3, P7, P8 sobre paginas reales
# ---------------------------------------------------------------------------

ARCHIVOS = sorted(glob.glob(str(RAIZ / "tests" / "fixtures" / "*.html")))


@pytest.mark.parametrize("ruta", ARCHIVOS)
def test_extraccion_de_paginas_reales(ruta):
    pagina = proc.extraer_html(Path(ruta).read_bytes(), "text/html; charset=utf-8", "https://example.org/base/")
    assert pagina.titulo
    assert proc.contar_palabras(pagina.texto) >= 150                     # P8 no descartaria estas semillas
    for resto in ("function(", "var ", "{{", "<div", "</a>", "window.dataLayer"):
        assert resto not in pagina.texto                                # sin scripts ni HTML
    assert pagina.enlaces and all(u.startswith("http") and "#" not in u for u, _ in pagina.enlaces)
    puntaje, terminos = TEMA.puntaje(pagina.texto)
    assert puntaje >= 4.0 and terminos                                   # P3: las semillas son del tema


def test_puntaje_tematico_distingue_temas():
    tema = "The Tyrannosaurus rex was a theropod dinosaur from the Late Cretaceous. " * 10
    otro = "The city council approved the new budget for road repairs and public transport. " * 10
    assert TEMA.puntaje(tema)[0] > 50
    assert TEMA.puntaje(otro)[0] == 0
    assert TEMA.puntaje("dinosaur fossil")[0] == 0                      # muy corto: puntaje no confiable


def test_hash_ignora_espacios_y_mayusculas():
    assert proc.hash_texto("Hola   Mundo\n") == proc.hash_texto("hola mundo")
    assert proc.hash_texto("hola mundo") != proc.hash_texto("hola mundos")


def test_meta_robots_y_head_sin_cerrar():
    html = (b"<html><head><title>T</title><meta name='robots' content='noindex, nofollow'>"
            b"<body><p>Texto visible de la pagina con varias palabras.</p><nav>menu</nav></body></html>")
    pagina = proc.extraer_html(html, "text/html", "https://example.org/")
    assert pagina.noindex and pagina.nofollow
    assert "Texto visible" in pagina.texto and "menu" not in pagina.texto    # </head> omitido no tapa el cuerpo


# ---------------------------------------------------------------------------
# P4, P5, P6, P11, P12: calendarizador
# ---------------------------------------------------------------------------

@pytest.fixture
def almacen(tmp_path):
    a = Almacen(tmp_path)
    yield a
    a.cerrar()


def nuevo_calendarizador(almacen, **cambios):
    return Calendarizador(parametros(**cambios), almacen, TEMA, {"a.org", "b.org"}, [])


def tarea_semilla(url="https://a.org/"):
    host = url.split("/")[2]
    return Tarea(url, url, "", "", url, host, proc.dominio_de(host), 0, 100.0)


def test_prioridad_y_visitados(almacen):
    cal = nuevo_calendarizador(almacen)
    padre = tarea_semilla()
    n = cal.encolar([("https://a.org/x", "home"), ("https://a.org/dinosaur", "dinosaur fossils"),
                     ("https://a.org/x", "repetida")], padre, 10.0)
    assert n == 2                                                        # P6: la repetida no se encola
    assert cal.encolar([("https://a.org/x", "otra vez")], padre, 10.0) == 0
    primera, _ = cal.siguiente()
    assert primera.url == "https://a.org/dinosaur"                       # P4: el anchor del tema sube la prioridad
    cal.terminar(primera)


def test_profundidad_maxima(almacen):
    cal = nuevo_calendarizador(almacen, profundidad_maxima=2)
    padre = tarea_semilla()
    padre.profundidad = 2
    assert cal.encolar([("https://a.org/x", "")], padre, 1.0) == 0       # P5
    padre.profundidad = 1
    assert cal.encolar([("https://a.org/x", "")], padre, 1.0) == 1


def test_lista_blanca_y_dominios_candidatos(almacen):
    cal = nuevo_calendarizador(almacen)
    padre = tarea_semilla()
    n = cal.encolar([("https://fuera.com/x", ""), ("https://uni.edu/a", ""), ("https://uni.edu/b", "")], padre, 1.0)
    assert n == 1                                                        # P1: .com no; .edu entra con una sola URL
    cal.resolver_dominio("uni.edu", True)
    assert cal.encolar([("https://uni.edu/b", "")], padre, 1.0) == 1     # aprobado: ya entra el resto
    cal2 = nuevo_calendarizador(almacen)
    cal2.encolar([("https://otra.edu/a", "")], padre, 1.0)
    cal2.resolver_dominio("otra.edu", False)
    assert cal2.encolar([("https://otra.edu/b", "")], padre, 1.0) == 0   # rechazado por la primera pagina


def test_un_host_a_la_vez_y_retardo(almacen):
    cal = nuevo_calendarizador(almacen, retardo_host_s=0.3)
    padre = tarea_semilla()
    cal.encolar([("https://a.org/1", ""), ("https://a.org/2", ""), ("https://b.org/1", "")], padre, 1.0)
    t1, _ = cal.siguiente()
    t2, _ = cal.siguiente()
    assert {t1.host, t2.host} == {"a.org", "b.org"}                      # hosts distintos en paralelo
    t3, espera = cal.siguiente()
    assert t3 is None                                                    # a.org esta en vuelo: P11
    cal.terminar(t1)
    cal.terminar(t2)
    t3, espera = cal.siguiente()
    assert t3 is None and 0 < espera <= 0.3                              # el retardo todavia no vence
    time.sleep(0.35)
    t3, _ = cal.siguiente()
    assert t3 is not None


def test_crawl_delay_y_retry_after_alargan_el_retardo(almacen):
    cal = nuevo_calendarizador(almacen, retardo_host_s=0.05)
    cal.encolar([("https://a.org/1", ""), ("https://a.org/2", "")], tarea_semilla(), 1.0)
    t, _ = cal.siguiente()
    cal.fijar_crawl_delay("a.org", 0.5)
    cal.terminar(t)
    assert cal.siguiente()[0] is None
    time.sleep(0.2)
    assert cal.siguiente()[0] is None                                    # P10: Crawl-delay mayor que el retardo base


def test_cuota_por_dominio(almacen):
    cal = nuevo_calendarizador(almacen, calentamiento_docs=10, cuota_dominio=0.5)
    almacen.n_docs = 10
    cal.docs_por_dominio["a.org"] = 9                                    # a.org ya tiene 90 %
    cal.docs_por_dominio["b.org"] = 1
    cal.encolar([("https://a.org/x", "dinosaur"), ("https://b.org/y", "")], tarea_semilla(), 50.0)
    primera, _ = cal.siguiente()
    assert primera.dominio == "b.org"                                    # P12: a.org pierde prioridad aunque valga mas


# ---------------------------------------------------------------------------
# Integracion sin internet: servidor local con robots.txt, HTML, PDF y duplicados
# ---------------------------------------------------------------------------

TEXTO = ("The Tyrannosaurus rex was a large theropod dinosaur that lived during the Late Cretaceous period. "
         "Paleontologists study fossil bones to learn how these animals lived and why they went extinct. ") * 12


def pagina(titulo, cuerpo, enlaces=""):
    return (f"<html><head><title>{titulo}</title><script>var x=1;</script></head><body>"
            f"<nav><a href='/menu'>Menu</a></nav><p>{cuerpo}</p>{enlaces}<footer>pie</footer></body></html>").encode()


def pdf_minimo(texto):
    """PDF de una pagina escrito a mano, suficiente para probar la extraccion con pypdf."""
    contenido = f"BT /F1 12 Tf 50 700 Td ({texto}) Tj ET".encode()
    objetos = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
               b"/Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length %d >>\nstream\n" % len(contenido) + contenido + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    salida, posiciones = bytearray(b"%PDF-1.4\n"), []
    for i, obj in enumerate(objetos, 1):
        posiciones.append(len(salida))
        salida += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    inicio_xref = len(salida)
    salida += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objetos) + 1)
    for pos in posiciones:
        salida += b"%010d 00000 n \n" % pos
    salida += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objetos) + 1, inicio_xref)
    return bytes(salida)


class Sitio(http.server.BaseHTTPRequestHandler):
    peticiones = []
    agentes = []
    condicionales = []      # valores de If-None-Match recibidos
    version = 1             # cambia el texto de /a para probar la revisita

    def log_message(self, *args):
        pass

    def responder(self, codigo, tipo, cuerpo, extra=None):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self):
        Sitio.peticiones.append((self.path, time.monotonic()))
        Sitio.agentes.append(self.headers.get("User-Agent"))
        if self.path == "/a":                                        # /a lleva ETag y contesta 304 si no cambio
            etag = f'"v{Sitio.version}"'
            Sitio.condicionales.append(self.headers.get("If-None-Match"))
            if self.headers.get("If-None-Match") == etag:
                return self.responder(304, "text/html", b"", {"ETag": etag})
            texto = TEXTO.replace("rex", "A") + (" Nueva informacion sobre fosiles. " * 10 if Sitio.version > 1 else "")
            return self.responder(200, "text/html", pagina("A", texto), {"ETag": etag})
        rutas = {
            "/robots.txt": (200, "text/plain", b"User-agent: *\nDisallow: /privado/\nCrawl-delay: 0\n"),
            "/": (200, "text/html", pagina("Inicio", TEXTO, "<a href='/a'>dinosaur A</a> <a href='/b#frag'>B</a>"
                  "<a href='/copia'>copia</a> <a href='/privado/x'>privado</a> <a href='/foto.jpg'>foto</a>"
                  "<a href='/paper.pdf'>paper</a> <a href='/otra-cosa'>tienda</a> <a href='/viejo'>viejo</a>"
                  "<a href='/tipo-raro'>raro</a> <a href='http://ajeno.invalid/x'>ajeno</a>")),
            "/a": (200, "text/html", pagina("A", TEXTO.replace("rex", "A"))),
            "/b": (200, "text/html", pagina("B", TEXTO.replace("rex", "B"))),
            "/copia": (200, "text/html", pagina("Copia de A", TEXTO.replace("rex", "A"))),   # mismo texto que /a (P7)
            "/privado/x": (200, "text/html", pagina("Privado", TEXTO)),
            "/otra-cosa": (200, "text/html", pagina("Tienda", "Buy cheap toys and office chairs today. " * 30)),
            "/corto": (200, "text/html", pagina("Corto", "Tyrannosaurus dinosaur fossil cretaceous. " * 10)),
            "/paper.pdf": (200, "application/pdf", pdf_minimo("Dinosaur fossil paleontology " * 30)),
            "/tipo-raro": (200, "image/png", b"\x89PNG..."),
            "/viejo": (301, "text/html", b"", {"Location": "/a"}),
        }
        if self.path in rutas:
            self.responder(*rutas[self.path])
        else:
            self.responder(404, "text/html", b"no existe")


@pytest.fixture
def sitio():
    Sitio.peticiones, Sitio.agentes, Sitio.condicionales, Sitio.version = [], [], [], 1
    servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Sitio)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    yield f"127.0.0.1:{servidor.server_address[1]}"
    servidor.shutdown()


def correr(ctx, hilos=4):
    trabajadores = [threading.Thread(target=trabajador, args=(ctx,), name=f"H_{i}") for i in range(hilos)]
    for t in trabajadores:
        t.start()
    for t in trabajadores:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in trabajadores), "el arañador no termino"
    ctx.pool_pdf.shutdown(wait=True)


def test_integracion_sin_internet(tmp_path, sitio):
    p = parametros(min_palabras=50, umbral_tematico=4.0)
    ctx = construir(p, tmp_path, [f"http://{sitio}/", f"http://{sitio}/corto"], [], TEMA)
    correr(ctx)
    db = sqlite3.connect(tmp_path / "arana.db")

    estados = dict(db.execute("SELECT url_normalizada, COALESCE(motivo, '') || '|' || estado FROM urls"))
    base = f"http://{sitio}"
    guardados = {u.replace(base, "") for u, e in estados.items() if e.endswith("|hecho")}
    assert guardados == {"/", "/a", "/b", "/paper.pdf"}, estados           # PDF incluido: P2
    assert estados[f"{base}/privado/x"].startswith("robots")               # P10
    assert estados[f"{base}/copia"].startswith("duplicado")                # P7
    assert estados[f"{base}/otra-cosa"].startswith("fuera_de_tema")        # P3
    assert estados[f"{base}/corto"].startswith("muy_corto")                # P8
    assert estados[f"{base}/viejo"].startswith("redireccion")
    assert estados[f"{base}/tipo-raro"].startswith("content_type")         # P2
    assert f"{base}/foto.jpg" not in estados                               # P2: extension, ni se encola
    assert not any("ajeno.invalid" in u for u in estados)                  # P1: fuera de la lista blanca
    assert f"{base}/b" in estados and not any("#" in u for u in estados)   # P6
    assert "/privado/x" not in [r for r, _ in Sitio.peticiones]         # nunca se pidio lo prohibido

    assert all(a.startswith("PaleoBuscaBot/1.0 (+prueba@example.org)") for a in Sitio.agentes)   # P13

    # Repositorio: texto limpio y metadatos
    filas = db.execute("SELECT ruta_archivo_txt, hash_sha256, content_type FROM documentos").fetchall()
    assert len(filas) == 4 and len({f[1] for f in filas}) == 4
    for ruta, _, tipo in filas:
        texto = (tmp_path / ruta).read_text(encoding="utf-8")
        assert "var x=1" not in texto and "<p>" not in texto and "Menu" not in texto and "pie" not in texto.split()


def test_retardo_entre_peticiones_al_mismo_host(tmp_path, sitio):
    p = parametros(min_palabras=50, retardo_host_s=0.25)
    ctx = construir(p, tmp_path, [f"http://{sitio}/"], [], TEMA)
    correr(ctx, hilos=6)
    momentos = [t for ruta, t in Sitio.peticiones]
    huecos = [b - a for a, b in zip(momentos, momentos[1:])]
    assert len(momentos) > 4
    assert min(huecos) >= 0.2, f"peticiones demasiado juntas: {min(huecos):.3f}s"      # P11 con 6 hilos y 1 host


def test_reanudacion(tmp_path, sitio):
    p = parametros(min_palabras=50)
    ctx = construir(p, tmp_path, [f"http://{sitio}/"], [], TEMA)
    tarea, _ = ctx.cal.siguiente()                                       # se toma la semilla pero no se procesa (corte)
    ctx.cal.terminar(tarea)
    ctx.pool_pdf.shutdown()
    ctx.almacen.cerrar()
    ctx2 = construir(p, tmp_path, [f"http://{sitio}/"], [], TEMA)        # segunda corrida: la semilla sigue pendiente
    assert ctx2.cal.estado()[0] == 1
    correr(ctx2)
    assert ctx2.almacen.n_docs == 4


def test_robots_inaccesible_es_prohibido(tmp_path):
    p = parametros()
    almacen = Almacen(tmp_path)
    robots = descargador.Robots(p, almacen)
    permitido, _, _ = robots.consultar("http://127.0.0.1:1/x")           # nadie escucha en ese puerto
    assert permitido is False
    almacen.cerrar()


def test_revisita_condicional(tmp_path, sitio):
    """P9: primero se guarda /a con su ETag; despues la revisita manda If-None-Match, recibe 304 y luego un cambio."""
    p = parametros(min_palabras=50, revisita_estable_dias=0)
    ctx = construir(p, tmp_path, [f"http://{sitio}/a"], [], TEMA)
    correr(ctx)
    db = sqlite3.connect(tmp_path / "arana.db")
    doc_id, etag, hash_1 = db.execute("SELECT id, etag, hash_sha256 FROM documentos").fetchone()
    assert etag == '"v1"'

    def revisar():
        ctx = construir(p, tmp_path, [], [], TEMA, reanudar=False)
        ctx.cal.encolar_revisita(tareas_de_revisita(ctx.almacen, p, 100))
        correr(ctx)
        ctx.almacen.cerrar()

    time.sleep(1.1)                       # la fecha se guarda al segundo: la revisita exige que sea vieja
    Sitio.condicionales.clear()
    revisar()
    assert Sitio.condicionales == ['"v1"']                                   # se mando If-None-Match
    assert db.execute("SELECT hash_sha256 FROM documentos").fetchone()[0] == hash_1   # 304: nada cambia

    Sitio.version = 2                                                        # el sitio publica una version nueva
    time.sleep(1.1)
    revisar()
    hash_2, etag_2 = db.execute("SELECT hash_sha256, etag FROM documentos WHERE id=?", (doc_id,)).fetchone()
    assert hash_2 != hash_1 and etag_2 == '"v2"'
    assert db.execute("SELECT COUNT(*) FROM documentos").fetchone()[0] == 1   # se actualiza, no se duplica
