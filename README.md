# PaleoBusca: arañador propio en Python

Arañador enfocado en dinosaurios y paleontología (textos en inglés), hecho para el Proyecto 1 de
Recuperación de Información Textual. Descarga páginas HTML, PDF y texto plano de sitios confiables,
deja solo el texto limpio en archivos `.txt` y guarda los metadatos en SQLite. El diseño y la
justificación de cada política están en `Docu - Proyecto 1 - RIT - Arañador.pdf`.

## Cómo correrlo

```bash
pip install -r requirements.txt
python -m pytest -q                      # pruebas (no necesitan internet)
python -m arana --max-docs 200           # corrida corta de prueba
python -m arana                          # corrida larga (hasta objetivo_gb o Ctrl+C)
python -m arana --duracion-min 480       # se detiene sola a las 8 horas
python -m arana --datos D:/datos         # guardar el repositorio en otro disco
python -m arana --revisita               # P9: revisar con GET condicional lo ya guardado
python estadisticas.py                   # tabla de la sección 3 + curva de Zipf
```

- **Antes de la primera corrida real** hay que poner un correo del grupo en `config/parametros.toml`
  (`correo_contacto`): va en el User-Agent `PaleoBuscaBot/1.0 (+correo)` (P13). Sin eso el programa no arranca.
- Para parar: `Ctrl+C`, o crear un archivo llamado `PARAR` dentro de la carpeta de datos. Se puede volver a
  ejecutar: la frontera (URLs pendientes y vistas) está guardada en SQLite y continúa donde quedó.
- Los datos se guardan en `datos/` (fuera de git): `arana.db`, `repositorio/<dominio>/.../<id>.txt` y
  `logs/bitacora.log`.

## Arquitectura (sigue la diapositiva 6 del curso)

```
config/ (políticas, semillas, diccionario)
      │
      ▼
arana.py ── hilos trabajadores (P15) ──► calendarizador.py ──► descargador.py ──► procesamiento.py
   │           bitácora                    (qué visitar y cuándo)  (HTTP + robots)    (texto, enlaces, puntaje)
   └───────────────────────────────────────► almacen.py (SQLite + archivos .txt)
```

| Módulo | Qué hace |
|---|---|
| `arana/calendarizador.py` | Frontera con cola de prioridad y colas por host. Largo plazo: qué URLs entran y en qué orden. Corto plazo: cortesía por host. |
| `arana/descargador.py` | Peticiones HTTP con reintentos, GET condicional y robots.txt. |
| `arana/procesamiento.py` | URL normalizada, HTML/PDF a texto limpio, enlaces, puntaje temático, hash. |
| `arana/almacen.py` | Esquema SQLite, archivos `.txt`, estado para reanudar. |
| `arana/arana.py` | Pool de hilos, flujo completo por URL, bitácora, parada limpia, revisita. |

## Dónde se implementa cada política

| # | Política | Dónde |
|---|---|---|
| P1 | Lista blanca de dominios | `calendarizador.py`: `_dominio_admitido`, `resolver_dominio`; listas en `config/dominios_permitidos.txt` y `config/semillas*.txt` |
| P2 | Filtro por tipo de contenido y texto limpio | `procesamiento.py`: `extension_bloqueada`, `extraer_html`, `pdf_a_texto`; `descargador.py`: `_leer` (solo lee cuerpos `text/html`, `text/plain`, `application/pdf`) |
| P3 | Filtro temático (arañado enfocado) | `procesamiento.py`: `Tema.puntaje`; se aplica en `arana.py`: `procesar`; diccionario en `config/terminos_tema.txt` |
| P4 | Priorización de la frontera | `calendarizador.py`: `encolar` (prioridad = puntaje del padre + anchor text), `siguiente`; `Tema.bonus_anchor` |
| P5 | Profundidad máxima | `calendarizador.py`: `encolar` |
| P6 | Normalización de URLs y visitados | `procesamiento.py`: `normalizar_url`; `calendarizador.py`: conjunto `vistas` |
| P7 | Contenido duplicado | `procesamiento.py`: `hash_texto`; `almacen.py`: `existe_hash`, `guardar_documento`; `arana.py`: `procesar` |
| P8 | Longitud mínima | `arana.py`: `procesar` (`min_palabras`); `procesamiento.py`: `contar_palabras` |
| P9 | Revisita condicional | `descargador.py`: `_pedir` (encabezados condicionales); `arana.py`: `revisitar`, `tareas_de_revisita`; `almacen.py`: `documentos_a_revisar` |
| P10 | Respeto de robots.txt | `descargador.py`: clase `Robots`; `arana.py`: `verificar_robots`; `calendarizador.py`: `fijar_crawl_delay` |
| P11 | Retardo y una conexión por host | `calendarizador.py`: `siguiente`, `terminar` |
| P12 | Cuota por dominio | `calendarizador.py`: `_cuota_excedida` (usada en `siguiente`) |
| P13 | User-Agent honesto y SSL | `descargador.py`: `user_agent`, `_ABRIDOR` (certificados verificados) |
| P14 | Errores y reintentos | `descargador.py`: `descargar` |
| P15 | Hilos y bitácora | `arana.py`: `trabajador`, `main` (ThreadPoolExecutor), `registrar` (bitácora) |

La bitácora (`datos/logs/bitacora.log`) tiene una línea por URL: `fecha | hilo | url | host | código HTTP | decisión | motivo`.

## Cambios respecto al documento de diseño

El diseño se siguió casi literal: profundidad 6, 150 palabras mínimo, 1,5 s por host, 15 % por dominio, 3 reintentos
(2, 4 y 8 s), 8 hilos, hash SHA-256, etc. no cambiaron. Lo que sí hay que reflejar en el documento final:

**Un cambio real a una política**
- **P12:** la cuota del 15 % se aplica después de 500 documentos guardados. No es por el volumen: al principio cualquier
  dominio tiene 100 % de los documentos (el primero guardado ya rompería la regla), así que la regla no tiene sentido
  con pocos datos. El valor está en `calentamiento_docs`.

**Detalles que el documento no especificaba (no contradicen ninguna política)**
- **P1:** "un dominio nuevo entra si su primera página supera P3" se implementó así: solo se evalúan dominios cuyo host
  termine en `.edu`, `.gov`, `.ac.uk`, `.museum`, etc. (lista en `config/parametros.toml`), y mientras se evalúa se
  encola una sola URL suya. Si la página no es relevante, el dominio queda rechazado.
- **P3 + P8 + P7:** los enlaces se siguen según la relevancia de la página (P3), aunque luego no se guarde por ser corta
  o repetida: una página índice corta pero temática puede llevar a muchas páginas útiles. Las semillas siempre se siguen.
- **P10:** además de robots.txt se respetan las etiquetas `meta robots noindex/nofollow` (diapositiva 6 del curso).
  Si robots.txt no se puede leer por error de red o 5xx se asume prohibido (RFC 9309) y se reintenta a los 15 minutos.
- **Redirecciones:** no se siguen solas; el destino se trata como un enlace nuevo y pasa por P1, P5, P6 y robots.txt.
- **PDF:** el texto se extrae en procesos aparte para que el GIL de Python no frene los hilos de descarga.
- **Módulos:** 5 en vez de los 8 de la tabla 2.6, siguiendo la arquitectura de la clase. P10 (robots.txt) vive en
  `descargador.py` porque es quien hace la petición; P11 (retardo por host) queda en el calendarizador.
- **Semilla 10:** la URL de Discover Magazine no está cortada (termina en `...the-most-45694`), pero el sitio responde
  403 a los robots. Se deja como semilla y queda registrada como error en la bitácora; no se finge ser un navegador (P13).

## Cómo verificar dominios y semillas

```bash
python verificar_dominios.py                        # revisa config/semillas.txt y config/semillas_extra.txt
python verificar_dominios.py https://sitio.org/x    # revisa URLs candidatas antes de agregarlas
```
Por cada URL muestra si robots.txt la permite, el código HTTP, el tipo de contenido, palabras de texto limpio, puntaje
temático y enlaces. Criterio para dejar un sitio: responde 200, `robots` dice ok, tiene cientos de palabras y puntaje
mayor que el umbral (o muchos enlaces si es una página índice). Sospechosos: 403 (bloquea robots), 0 palabras y 1
enlace (la página depende de JavaScript), error SSL (no se desactiva la verificación).

## Pendientes conocidos

- Calibrar `umbral_tematico` (hoy 4,0) con una corrida corta real y revisar qué se descarta como `fuera_de_tema`.
- `robots: ok` en la verificación solo dice que la URL de entrada está permitida; dentro de cada sitio el arañador
  vuelve a consultar robots.txt en cada URL.
- La PC tiene poco espacio libre en `C:` (unos 14 GB): para 10 GB de texto conviene usar `--datos` con otro disco.
