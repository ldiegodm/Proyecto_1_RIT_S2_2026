"""Verifica las semillas y los dominios candidatos ANTES de una corrida larga.

Por cada URL de config/semillas.txt y config/semillas_extra.txt revisa, con el mismo User-Agent honesto
del arañador y con 1,5 s entre peticiones al mismo sitio:
    - si robots.txt permite la URL y si declara Crawl-delay (P10)
    - si responde, con que codigo y tipo de contenido (P2, P14)
    - cuantas palabras de texto limpio tiene y su puntaje tematico (P3, P8)
    - cuantos enlaces tiene (sirve para ver si el sitio "da para seguir")
Tambien avisa de los dominios de config/dominios_permitidos.txt que no tienen ninguna semilla.

Uso:  python verificar_dominios.py                 # revisa las semillas de config/
      python verificar_dominios.py URL [URL ...]   # revisa URLs candidatas (por ejemplo antes de agregarlas)
Hace una peticion por URL y una a robots.txt por sitio: es una prueba corta y cortes.
"""

import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

from arana import descargador, procesamiento as proc
from arana.almacen import Almacen
from arana.arana import RAIZ, cargar_parametros, leer_lista


def main():
    p = cargar_parametros(RAIZ / "config" / "parametros.toml")
    if "CAMBIAR" in p.correo_contacto:
        print("Primero hay que poner el correo en config/parametros.toml")
        return 2
    tema = proc.Tema(RAIZ / "config" / "terminos_tema.txt")
    candidatas = sys.argv[1:]
    semillas = candidatas or (leer_lista(RAIZ / "config" / "semillas.txt")
                              + leer_lista(RAIZ / "config" / "semillas_extra.txt"))
    dominios_lista = leer_lista(RAIZ / "config" / "dominios_permitidos.txt")

    with tempfile.TemporaryDirectory() as tmp:
        almacen = Almacen(tmp)
        robots = descargador.Robots(p, almacen)
        ultima = {}
        print(f"{'URL':62} {'robots':9} {'http':5} {'tipo':16} {'palabras':>8} {'punt.':>6} {'enlaces':>7}")
        dominios_con_semilla = set()
        for url in semillas:
            url = proc.normalizar_url(url)
            host = urlsplit(url).netloc
            dominios_con_semilla.add(proc.dominio_de(host))

            permitido, delay, _ = robots.consultar(url)
            estado_robots = ("ok" if permitido else "PROHIBIDO") + (f" d={delay:g}" if delay else "")
            espera = max(p.retardo_host_s, delay or 0) - (time.monotonic() - ultima.get(host, 0))
            if espera > 0:
                time.sleep(espera)
            resp = descargador.descargar(url, p)
            ultima[host] = time.monotonic()

            palabras, puntaje, enlaces = "-", "-", "-"
            if resp.redireccion:
                tipo = "-> " + resp.redireccion[:40]
            elif resp.cuerpo is not None:
                tipo = resp.content_type
                if tipo == "application/pdf":
                    resultado = proc.pdf_a_texto(resp.cuerpo, 40)
                    texto, n_enlaces = (resultado[1] if resultado else ""), 0
                elif tipo == "text/plain":
                    texto, n_enlaces = proc.extraer_texto_plano(resp.cuerpo, resp.content_type_completo).texto, 0
                else:
                    pagina = proc.extraer_html(resp.cuerpo, resp.content_type_completo, url)
                    texto, n_enlaces = pagina.texto, len(pagina.enlaces)
                palabras = proc.contar_palabras(texto)
                puntaje = f"{tema.puntaje(texto)[0]:.1f}"
                enlaces = n_enlaces
            else:
                tipo = resp.rechazo or resp.error[:16] or "-"
            print(f"{url[:62]:62} {estado_robots:9} {resp.codigo or '-':<5} {tipo[:16]:16} {palabras!s:>8} "
                  f"{puntaje!s:>6} {enlaces!s:>7}", flush=True)
        almacen.cerrar()

    sin_semilla = [d for d in dominios_lista if d not in dominios_con_semilla]
    if sin_semilla and not candidatas:
        print("\nDominios de la lista blanca sin semilla (solo se alcanzan si otro sitio los enlaza):",
              ", ".join(sin_semilla))
    return 0


if __name__ == "__main__":
    sys.exit(main())
