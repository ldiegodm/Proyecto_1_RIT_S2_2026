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
                   reintentos=0, robots_ttl_s=60, procesos_pdf=1, calentamiento_docs=10**9, max_evaluaciones_dominio=1,
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
    assert proc.dominio_de("revista.macn.gob.ar") == "macn.gob.ar"      # no todo el gobierno argentino
    assert proc.dominio_de("www.museocostarica.go.cr") == "museocostarica.go.cr"


def test_es_tienda():
    assert proc.es_tienda("https://shop.bgs.ac.uk/Shop")
    assert proc.es_tienda("https://australian.museum/store/toys")
    assert proc.es_tienda("https://example.org/cart")
    assert not proc.es_tienda("https://www.nhm.ac.uk/discover/what-are-dinosaurs.html")
    assert not proc.es_tienda("https://example.org/workshop/fossils")      # "workshop" no es "shop"


def test_es_trampa_y_parametros_de_presentacion():
    p = parametros()
    p.rutas_excluidas, p.hosts_excluidos, p.parametros_excluidos = ["/search", "/article/file"], ["idp"], ["_csrf", "q"]
    trampa = lambda u: proc.es_trampa(u, p.rutas_excluidas, p.hosts_excluidos, p.parametros_excluidos)
    assert trampa("https://idp.nature.com/authorize?client_id=x")
    assert trampa("https://www.usgs.gov/search?keywords=geology")
    assert trampa("https://journals.plos.org/plosone/article/file?id=10.1371/x&type=supplementary")
    assert trampa("https://www.nature.com/articles/s41559/save?_csrf=abc")
    assert not trampa("https://www.nature.com/articles/s41559-022-01664-8")
    assert not trampa("https://journals.plos.org/plosone/browse/taphonomy?page=2")
    # PLOS repetia cada pagina de listado con distinto orden/vista: P6 las vuelve una sola URL
    assert proc.normalizar_url("https://j.org/browse/x?page=1&resultView=list&sortOrder=MOST_VIEWS") == \
        proc.normalizar_url("https://j.org/browse/x?page=1")


def test_wikipedia_solo_articulos_en_ingles():
    from arana.arana import cargar_parametros
    p = cargar_parametros(RAIZ / "config" / "parametros.toml")
    trampa = lambda u: proc.es_trampa(u, p.rutas_excluidas, p.hosts_excluidos, p.parametros_excluidos,
                                      p.patron_hosts_excluidos)
    assert not trampa("https://en.wikipedia.org/wiki/Tyrannosaurus")
    assert not trampa("https://simple.wikipedia.org/wiki/Dinosaur")
    assert not trampa("https://en.wikipedia.org/wiki/Category:Dinosaurs")      # las categorias son indices utiles
    assert trampa("https://fr.wikipedia.org/wiki/Dinosaure")                    # otros idiomas
    assert trampa("https://es.m.wikipedia.org/wiki/Dinosauria")
    assert trampa("https://en.wikipedia.org/wiki/Special:Search?search=x")
    assert trampa("https://en.wikipedia.org/wiki/Talk:Dinosaur")
    assert trampa("https://en.wikipedia.org/w/index.php?title=Dinosaur&action=history")
    assert trampa("https://en.wikipedia.org/wiki/Dinosaur?oldid=12345")
    assert not trampa("https://ca.gov/algo")                                    # el patron no confunde codigos de idioma con otros sitios


def test_reanudar_descarta_trampas_pendientes(tmp_path):
    p = parametros()
    p.rutas_excluidas, p.hosts_excluidos, p.parametros_excluidos = ["/search"], [], []
    almacen = Almacen(tmp_path)
    almacen.registrar_urls([("https://a.org/buena", "", "", "", "https://a.org/", "a.org", "a.org", 1, 5.0),
                            ("https://a.org/search?x=1", "", "", "", "https://a.org/", "a.org", "a.org", 1, 5.0)])
    cal = Calendarizador(p, almacen, TEMA, {"a.org"}, [])
    cal.cargar_pendientes(almacen.cargar_pendientes())
    assert cal.estado()[0] == 1                                        # solo la buena vuelve a la frontera
    assert almacen.db.execute("SELECT motivo FROM urls WHERE url_normalizada='https://a.org/search?x=1'").fetchone()[0] == "trampa"
    almacen.cerrar()


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


def test_bloques_de_wikipedia_no_entran_al_texto():
    html = (b"<html><head><title>T. rex</title></head><body><div id='siteSub'>From Wikipedia, the free encyclopedia</div>"
            b"<table class='infobox biota'><tr><td>Kingdom: Animalia <table><tr><td>anidada</td></tr></table></td></tr></table>"
            b"<p>Tyrannosaurus is a genus of large theropod dinosaur that lived in western North America.<sup class='reference'>"
            b"<a href='#cite_note-1'>[1]</a></sup> It was the apex predator of its ecosystem.</p>"
            b"<div class='reflist'><ol class='references'><li>Autor, A. (2020). Un articulo citado.</li></ol></div>"
            b"<div id='catlinks'><ul><li>CS1 maint: deprecated archival service</li></ul></div>"
            b"<p>Segunda parte del articulo con suficientes palabras para conservarse.</p></body></html>")
    texto = proc.extraer_html(html, "text/html", "https://en.wikipedia.org/wiki/T").texto
    assert "Tyrannosaurus is a genus" in texto and "Segunda parte del articulo" in texto   # el texto sigue
    for ruido in ("Kingdom", "anidada", "[1]", "Autor, A.", "CS1 maint", "From Wikipedia"):
        assert ruido not in texto, ruido


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


def test_dominio_candidato_usa_el_enlace_mas_prometedor(almacen):
    cal = nuevo_calendarizador(almacen)
    n = cal.encolar([("https://uni.edu/", "home"), ("https://uni.edu/dinosaur-fossils", "dinosaur fossils")],
                    tarea_semilla(), 1.0)
    assert n == 1
    tarea, _ = cal.siguiente()
    assert tarea.url == "https://uni.edu/dinosaur-fossils"               # no la portada, aunque viniera primero


def test_dominio_candidato_no_se_rechaza_por_un_error(almacen):
    cal = nuevo_calendarizador(almacen, max_evaluaciones_dominio=3)
    padre = tarea_semilla()
    cal.encolar([("https://uni.edu/a", "")], padre, 1.0)
    cal.resolver_dominio("uni.edu", False, evaluada=False)               # 403: no se pudo evaluar
    assert cal.encolar([("https://uni.edu/b", "")], padre, 1.0) == 1     # vuelve a intentarse con otra URL
    cal.resolver_dominio("uni.edu", False, evaluada=False)
    assert cal.encolar([("https://uni.edu/c", "")], padre, 1.0) == 1
    cal.resolver_dominio("uni.edu", False, evaluada=False)               # tercer fallo: rechazado
    assert cal.encolar([("https://uni.edu/d", "")], padre, 1.0) == 0


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
    robots_redirige = False # /robots.txt responde 301 hacia /robots-real.txt
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
        if self.path == "/robots.txt" and Sitio.robots_redirige:
            return self.responder(301, "text/html", b"", {"Location": "/robots-real.txt"})
        if self.path == "/robots-real.txt":
            return self.responder(200, "text/plain", b"User-agent: *\nDisallow: /privado/\n")
        if self.path.startswith("/n/"):                                # paginas del tema, una por numero
            return self.responder(200, "text/html", pagina("N", TEXTO.replace("rex", self.path)))
        if self.path.startswith("/m/"):                                # paginas fuera de tema
            return self.responder(200, "text/html", pagina("M", ("Cheap flights and hotels deals " + self.path + " ") * 40))
        enlaces_n = "".join(f"<a href='/n/{i}'>pagina {i}</a> " for i in range(12))
        enlaces_m = "".join(f"<a href='/m/{i}'>oferta {i}</a> " for i in range(12))
        if self.path == "/semilla-hub":
            return self.responder(200, "text/html", pagina("Inicio", TEXTO,
                                  "<a href='/Category:Dinosaur_fossils'>categoria</a> <a href='/menu-del-sitio'>menu</a>"))
        if self.path == "/Category:Dinosaur_fossils":                  # indice corto: tema en la URL, muchos enlaces
            return self.responder(200, "text/html", pagina("Dinosaur fossils", "Lista de paginas.", enlaces_n))
        if self.path == "/menu-del-sitio":                             # corto y con muchos enlaces, pero sin tema
            return self.responder(200, "text/html", pagina("Menu", "Lista de paginas.", enlaces_m))
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
    Sitio.robots_redirige = False
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


def test_paginas_indice_propagan_enlaces(tmp_path, sitio):
    p = parametros(min_palabras=50, min_enlaces_indice=10)
    ctx = construir(p, tmp_path, [f"http://{sitio}/semilla-hub"], [], TEMA)
    correr(ctx)
    pedidas = {ruta for ruta, _ in Sitio.peticiones}
    assert all(f"/n/{i}" in pedidas for i in range(12))                  # el indice con tema en la URL se siguio
    assert not any(ruta.startswith("/m/") for ruta in pedidas)            # el menu sin tema no
    db = sqlite3.connect(tmp_path / "arana.db")
    motivo = db.execute("SELECT motivo FROM urls WHERE url_normalizada LIKE '%Category:Dinosaur_fossils'").fetchone()[0]
    assert motivo.startswith("fuera_de_tema") and motivo.endswith(":indice")   # no se guarda, pero queda marcado
    assert db.execute("SELECT COUNT(*) FROM documentos WHERE url_normalizada LIKE '%/n/%'").fetchone()[0] == 12


def test_reencolar_indices_descartados(tmp_path):
    from reencolar_indices import reencolar
    almacen = Almacen(tmp_path)
    fila = lambda u: (u, "", "", "", u, "a.org", "a.org", 1, 5.0)
    almacen.registrar_urls([fila("https://a.org/Category:Dinosaur_fossils"), fila("https://a.org/contacto"),
                            fila("https://a.org/dinosaur-largo")])
    almacen.marcar_url("https://a.org/Category:Dinosaur_fossils", "descartado", "fuera_de_tema:0.0")   # indice corto
    almacen.marcar_url("https://a.org/contacto", "descartado", "fuera_de_tema:0.0")                   # sin tema en la ruta
    almacen.marcar_url("https://a.org/dinosaur-largo", "descartado", "fuera_de_tema:3.2")             # texto suficiente: no
    almacen.cerrar()
    assert reencolar(tmp_path, TEMA) == 1
    db = sqlite3.connect(tmp_path / "arana.db")
    assert dict(db.execute("SELECT url_normalizada, estado FROM urls")) == {
        "https://a.org/Category:Dinosaur_fossils": "pendiente", "https://a.org/contacto": "descartado",
        "https://a.org/dinosaur-largo": "descartado"}


def test_dominio_se_rechaza_solo_tras_varias_evaluaciones(almacen):
    cal = nuevo_calendarizador(almacen, max_evaluaciones_dominio=3)
    padre = tarea_semilla()
    for i in range(2):                                                    # dos portadas sin tema no bastan
        assert cal.encolar([(f"https://uni.edu/p{i}", "")], padre, 1.0) == 1
        cal.resolver_dominio("uni.edu", False)
    assert cal.encolar([("https://uni.edu/depto-paleontologia", "")], padre, 1.0) == 1
    cal.resolver_dominio("uni.edu", True)                                 # la tercera si era del tema
    assert cal.dominios["uni.edu"] == "aprobado"


def test_reevaluar_dominios_rechazados(tmp_path):
    p = parametros()
    almacen = Almacen(tmp_path)
    almacen.guardar_dominio("uni.edu", "descubierto", "rechazado")
    assert Calendarizador(p, almacen, TEMA, {"a.org"}, []).dominios["uni.edu"] == "rechazado"
    assert "uni.edu" not in Calendarizador(p, almacen, TEMA, {"a.org"}, [], reevaluar_dominios=True).dominios
    almacen.cerrar()


def test_paginas_con_muchos_enlaces_solo_encolan_los_del_tema(almacen):
    cal = nuevo_calendarizador(almacen, enlaces_densos=5)
    enlaces = [(f"https://a.org/otro-{i}", f"otra cosa {i}") for i in range(10)] + \
              [("https://a.org/Gyposaurus", "Gyposaurus"), ("https://a.org/Pterosaur", "pterosaur")]
    assert cal.encolar(enlaces, tarea_semilla(), 10.0) == 2               # 12 enlaces > 5: solo los dos del tema
    cal2 = nuevo_calendarizador(almacen, enlaces_densos=50)
    assert cal2.encolar([(f"https://b.org/otro-{i}", "") for i in range(3)], tarea_semilla("https://b.org/"), 10.0) == 3


def test_nombres_de_genero_cuentan_como_tema():
    assert TEMA.bonus_anchor("Gyposaurus") == 1 and TEMA.bonus_anchor("Velociraptor") == 1
    assert TEMA.bonus_anchor("Triceratops") == 1 and TEMA.bonus_anchor("Titan") == 0     # "saurus" etc. necesitan algo antes
    assert TEMA.bonus_anchor("Peafowl pollen") == 0


def test_enlaces_de_categorias_de_pagina_no_suben_por_el_arbol():
    html = (b"<html><body><p>Texto del articulo sobre un dinosaurio de gran tamano y largo cuello en el Jurasico.</p>"
            b"<a href='/wiki/Allosaurus'>Allosaurus</a>"
            b"<div id='catlinks'><ul><li><a href='/wiki/Category:Animals'>Animals</a></li></ul></div></body></html>")
    enlaces = proc.extraer_html(html, "text/html", "https://en.wikipedia.org/wiki/X").enlaces
    assert [u for u, _ in enlaces] == ["https://en.wikipedia.org/wiki/Allosaurus"]


def test_wikipedia_y_bases_de_usgs_se_excluyen():
    from arana.arana import cargar_parametros
    p = cargar_parametros(RAIZ / "config" / "parametros.toml")
    trampa = lambda u: proc.es_trampa(u, p.rutas_excluidas, p.hosts_excluidos, p.parametros_excluidos,
                                      p.patron_hosts_excluidos)
    assert trampa("https://nas.er.usgs.gov/queries/SpecimenViewer.aspx?SpecimenID=1822165")
    assert trampa("https://mrdata.usgs.gov/geology/state/")
    assert trampa("https://essigdb.berkeley.edu/cgi/calmoth_query?one=T&seq_num=3468")
    assert trampa("https://ucmpdb.berkeley.edu/cgi/ucmp_query2?one=T&spec_id=V42545")
    assert not trampa("https://ucmp.berkeley.edu/diapsids/dinosaur.html")
    assert not trampa("https://www.usgs.gov/programs/paleontology") and not trampa("https://pubs.usgs.gov/")


def test_reanudar_limpia_hosts_saturados_dejando_lo_del_tema(tmp_path):
    p = parametros(host_saturado=3)
    almacen = Almacen(tmp_path)
    fila = lambda u, a: (u, "", "", a, "https://a.org/", "a.org", "a.org", 1, 5.0)
    almacen.registrar_urls([fila(f"https://a.org/x{i}", "otra cosa") for i in range(4)] +
                           [fila("https://a.org/Pterosaur", "pterosaur")])
    cal = Calendarizador(p, almacen, TEMA, {"a.org"}, [])
    cal.cargar_pendientes(almacen.cargar_pendientes())
    assert cal.estado()[0] == 1                                           # 5 pendientes > 3: queda solo la del tema
    almacen.cerrar()


def test_robots_txt_con_redireccion(tmp_path, sitio):
    Sitio.robots_redirige = True
    almacen = Almacen(tmp_path)
    robots = descargador.Robots(parametros(), almacen)
    assert robots.consultar(f"http://{sitio}/privado/x")[0] is False      # se siguio la redireccion y se leyo la regla
    assert robots.consultar(f"http://{sitio}/a")[0] is True
    assert almacen.db.execute("SELECT acceso FROM robots").fetchone()[0] == "ok"
    almacen.cerrar()


def test_reabrir_profundidad_sigue_enlaces_sin_duplicar(tmp_path, sitio):
    from reabrir_frontera import reabrir
    p = parametros(min_palabras=50, profundidad_maxima=0)
    ctx = construir(p, tmp_path, [f"http://{sitio}/"], [], TEMA)
    correr(ctx)
    ctx.almacen.cerrar()
    assert sqlite3.connect(tmp_path / "arana.db").execute("SELECT COUNT(*) FROM documentos").fetchone()[0] == 1
    assert reabrir(tmp_path, profundidad=0) == {"profundidad": 1}
    p.profundidad_maxima = 1
    ctx = construir(p, tmp_path, [], [], TEMA)
    correr(ctx)
    ctx.almacen.cerrar()
    db = sqlite3.connect(tmp_path / "arana.db")
    assert db.execute("SELECT COUNT(*) FROM documentos WHERE url_normalizada=?", (f"http://{sitio}/",)).fetchone()[0] == 1
    assert db.execute("SELECT estado FROM urls WHERE url_normalizada=?", (f"http://{sitio}/",)).fetchone()[0] == "hecho"
    assert db.execute("SELECT COUNT(*) FROM documentos").fetchone()[0] > 1           # ahora si se siguieron sus enlaces


def test_reabrir_umbral_y_robots(tmp_path):
    from reabrir_frontera import reabrir
    almacen = Almacen(tmp_path)
    fila = lambda u, h: (u, "", "", "", u, h, h, 1, 5.0)
    almacen.registrar_urls([fila("https://a.org/x", "a.org"), fila("https://a.org/y", "a.org"),
                            fila("https://b.org/z", "b.org"), fila("https://c.org/w", "c.org")])
    almacen.marcar_url("https://a.org/x", "descartado", "fuera_de_tema:6.5")
    almacen.marcar_url("https://a.org/y", "descartado", "fuera_de_tema:2.0")
    almacen.marcar_url("https://b.org/z", "descartado", "robots")
    almacen.marcar_url("https://c.org/w", "descartado", "robots")
    almacen.guardar_robots("b.org", None, "prohibido")                     # no se pudo leer: se reintenta
    almacen.guardar_robots("c.org", None, "ok")                            # prohibicion real: se respeta
    almacen.cerrar()
    assert reabrir(tmp_path, robots=True, umbral=6.0) == {"robots": 1, "umbral": 1}
    db = sqlite3.connect(tmp_path / "arana.db")
    assert {u for (u,) in db.execute("SELECT url_normalizada FROM urls WHERE estado='pendiente'")} == \
        {"https://a.org/x", "https://b.org/z"}


def test_trampas_vistas_al_abrir_politicas():
    from arana.arana import cargar_parametros
    p = cargar_parametros(RAIZ / "config" / "parametros.toml")
    trampa = lambda u: proc.es_trampa(proc.normalizar_url(u), p.rutas_excluidas, p.hosts_excluidos,
                                      p.parametros_excluidos, p.patron_hosts_excluidos)
    assert trampa("https://www.nature.com/articles/s42003-022-04132-y/figures/5?code=89f0a72a")
    assert trampa("https://en.wikipedia.org/wiki/Wikipedia_talk:WikiProject_Dinosaurs/Article_alerts")
    assert trampa("https://en.wikipedia.org/wiki/Category:FL-Class_amphibian_and_reptile_articles")
    assert trampa("https://en.wikipedia.org/wiki/Category:Low-importance_Palaeontology_articles")
    assert trampa("https://go.ohiolink.edu/acprod/odb_ejc/r/ohl/go/selection?clear=1&session=17462330075950")
    assert not trampa("https://www.nature.com/articles/s42003-022-04132-y")              # el articulo si
    assert not trampa("https://en.wikipedia.org/wiki/Category:Late_Cretaceous_dinosaurs")
    assert not trampa("https://en.wikipedia.org/wiki/Middle-class_dinosaur_museum")      # no confunde "-class" con "-Class_"


def test_reanudar_no_descarta_paginas_ya_guardadas(tmp_path):
    p = parametros(host_saturado=1)
    almacen = Almacen(tmp_path)
    fila = lambda u: (u, "", "", "", "https://a.org/", "a.org", "a.org", 6, 5.0)
    almacen.registrar_urls([fila("https://a.org/guardada"), fila("https://a.org/otra")])
    almacen.guardar_documento(dict(url_normalizada="https://a.org/guardada", dominio="a.org", hash_sha256="h1",
                                   num_palabras=200), "texto")
    cal = Calendarizador(p, almacen, TEMA, {"a.org"}, [])
    cal.cargar_pendientes(almacen.cargar_pendientes())
    assert cal.estado()[0] == 1                                            # la guardada vuelve; la otra (sin tema) no
    tarea, _ = cal.siguiente()
    assert tarea.url == "https://a.org/guardada"
    almacen.cerrar()


def test_quitar_documentos(tmp_path):
    from quitar_documentos import quitar
    almacen = Almacen(tmp_path)
    almacen.registrar_urls([("https://ojp.gov/amber", "", "", "", "s", "ojp.gov", "ojp.gov", 3, 1.0)])
    doc_id, _ = almacen.guardar_documento(dict(url_normalizada="https://ojp.gov/amber", host="ojp.gov", dominio="ojp.gov",
                                               hash_sha256="h", num_palabras=300), "AMBER Alert")
    ruta = tmp_path / almacen.db.execute("SELECT ruta_archivo_txt FROM documentos").fetchone()[0]
    almacen.cerrar()
    assert ruta.exists()
    assert quitar(tmp_path, [doc_id], "fuera_de_tema:amber_alert") == ["https://ojp.gov/amber"]
    db = sqlite3.connect(tmp_path / "arana.db")
    assert db.execute("SELECT COUNT(*) FROM documentos").fetchone()[0] == 0
    assert db.execute("SELECT estado, motivo FROM urls").fetchone() == ("descartado", "fuera_de_tema:amber_alert")
    assert not ruta.exists()



PREFIJOS = ["10.1371", "10.1038"]


def test_es_referencia():
    assert proc.es_referencia("https://doi.org/10.1371/journal.pone.0080557", PREFIJOS)
    assert proc.es_referencia("https://doi.org/10.1038%2Fsrep35295", PREFIJOS)               # prefijo codificado
    assert not proc.es_referencia("https://doi.org/10.1016/j.cretres.2020.104", PREFIJOS)   # Elsevier: de pago
    assert proc.es_referencia("https://pmc.ncbi.nlm.nih.gov/articles/PMC4011880/", PREFIJOS, seguir_pmc=True)
    assert proc.es_referencia("https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4011880", PREFIJOS, seguir_pmc=True)
    assert not proc.es_referencia("https://pmc.ncbi.nlm.nih.gov/articles/PMC4011880/", PREFIJOS)   # apagado por defecto
    assert not proc.es_referencia("https://pubmed.ncbi.nlm.nih.gov/12345/", PREFIJOS)        # resumen corto
    assert not proc.es_referencia("https://journals.plos.org/plosone/", PREFIJOS)
    assert not proc.es_referencia("https://doi.org/10.1371/journal.pone.0063423.g001", PREFIJOS)   # figura, no articulo
    assert not proc.es_referencia("https://doi.org/10.1371/journal.pone.0063423.t001", PREFIJOS)   # tabla


def test_nombres_de_genero_cuentan_en_el_puntaje():
    texto = ("The holotype of Allosaurus and Ceratosaurus shows a deep maxilla, while Velociraptor and "
             "Deinonychus differ in the pubis. ") * 5
    puntaje, terminos = TEMA.puntaje(texto)
    assert puntaje >= 6.0                                                 # pasa P3 aunque no use palabras del diccionario


def test_referencias_sin_lista_blanca_ni_filtro_denso_pero_con_p5(almacen):
    cal = nuevo_calendarizador(almacen, enlaces_densos=3, prefijos_doi=PREFIJOS, profundidad_maxima=2, seguir_pmc=True)
    padre = tarea_semilla()
    enlaces = [("https://doi.org/10.1371/journal.pone.1", "Smith 2014"),
               ("https://doi.org/10.1016/j.x.1", "Jones 2010"),               # de pago: no
               ("https://pmc.ncbi.nlm.nih.gov/articles/PMC1/", "PMC 1"),
               ("https://otro.com/a", "a"), ("https://otro.com/b", "b"), ("https://otro.com/c", "c"),
               ("https://otro.com/d", "d")]
    assert cal.encolar(enlaces, padre, 30.0) == 2                         # las 2 referencias: sin P1 ni filtro denso
    citas = []
    while True:
        t, _ = cal.siguiente()
        if t is None:
            break
        citas.append(t)
        cal.terminar(t)
    plos = proc.normalizar_url(proc.resolver_referencia("https://doi.org/10.1371/journal.pone.1"))
    assert {t.url for t in citas} == {plos, "https://pmc.ncbi.nlm.nih.gov/articles/PMC1/"}
    assert plos.startswith("https://journals.plos.org/plosone/article?id=")     # el DOI se resolvio sin pasar por doi.org
    assert all(t.anchor.startswith("[cita] ") and t.profundidad == 1 for t in citas)
    # la cadena de citas sigue desde un articulo citado (si fue relevante) mientras P5 lo permita
    assert cal.encolar([("https://doi.org/10.1371/journal.pone.2", "Otro")], citas[0], 30.0) == 1
    citas[0].profundidad = 2
    assert cal.encolar([("https://doi.org/10.1371/journal.pone.3", "Otro mas")], citas[0], 30.0) == 0   # P5



def test_resolver_referencia_sin_pasar_por_doi_org():
    r = proc.resolver_referencia
    assert r("https://doi.org/10.1038%2Fs41586-019-1636-y") == "https://www.nature.com/articles/s41586-019-1636-y"
    assert r("https://doi.org/10.3389%2Ffeart.2019.00038") == "https://www.frontiersin.org/articles/10.3389/feart.2019.00038/full"
    assert r("https://doi.org/10.1371%2Fjournal.pone.0223471") == \
        "https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0223471"
    assert r("https://doi.org/10.1371/journal.pbio.1000001") == \
        "https://journals.plos.org/plosbiology/article?id=10.1371/journal.pbio.1000001"
    assert r("https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4819037") == "https://pmc.ncbi.nlm.nih.gov/articles/PMC4819037/"
    assert r("https://doi.org/10.5194/fr-23-71-2020") == "https://doi.org/10.5194/fr-23-71-2020"     # Copernicus: por doi.org


def test_referencias_se_encolan_ya_resueltas(almacen):
    cal = nuevo_calendarizador(almacen, prefijos_doi=["10.1038"])
    assert cal.encolar([("https://doi.org/10.1038%2Fsrep35295", "Paper")], tarea_semilla(), 20.0) == 1
    tarea, _ = cal.siguiente()
    assert tarea.url == "https://www.nature.com/articles/srep35295" and tarea.host == "www.nature.com"



def test_detecta_verificacion_anti_robots():
    assert proc.es_bloqueo("Checking your browser - reCAPTCHA", "Please wait while we verify your browser.")
    assert proc.es_bloqueo("Just a moment...", "Enable JavaScript and cookies to continue")
    assert not proc.es_bloqueo("Tyrannosaurus", "The Tyrannosaurus was a large theropod. " * 60)   # texto largo: no


def test_pmc_excluido_en_la_configuracion():
    from arana.arana import cargar_parametros
    p = cargar_parametros(RAIZ / "config" / "parametros.toml")
    assert p.seguir_pmc is False
    trampa = lambda u: proc.es_trampa(u, p.rutas_excluidas, p.hosts_excluidos, p.parametros_excluidos,
                                      p.patron_hosts_excluidos)
    assert trampa("https://pmc.ncbi.nlm.nih.gov/articles/PMC4819037/")
    assert trampa("https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4819037")
