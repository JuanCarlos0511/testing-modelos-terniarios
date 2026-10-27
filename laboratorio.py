#!/usr/bin/env python3
"""Script común del laboratorio (guía, sección 3.2).

Subcomandos, en el orden de la guía:
  datos      descarga MGSM (en, es) de un commit fijo y verifica SHA-256
  preparar   crea la carpeta de entrega con ficha, bitácora y versiones
  correr     envía los problemas a cada modelo vía llama-server (--piloto N para la prueba piloto)
  velocidad  ejecuta llama-bench con los parámetros de la sección 3.3
  versiones  escribe versiones.txt
  verificar  revisa la lista de verificación de la sección 4.2

Las condiciones de la tabla 2, los prompts y los modelos están fijos aquí; config.json
solo contiene rutas y datos de cada laptop. No modificar sin autorización de los profesores.
"""
import argparse
import csv
import datetime
import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent

# Tabla 1. El motor decide qué compilación se usa (bitnet.cpp o llama.cpp).
# El GGUF oficial de BitNet trae una plantilla que coloca el fin de texto justo antes de la
# respuesta; se usa la del modelo original (microsoft/bitnet-b1.58-2B-4T, commit 04c3b9a),
# que es la misma que bitnet.cpp aplicaba con su plantilla integrada "bitnet".
MODELOS = {
    "bitnet-2b":           {"motor": "bitnet",   "plantilla": {}, "archivo_plantilla": "plantillas/bitnet-2b.jinja"},
    "falcon3-3b-ternario": {"motor": "bitnet",   "plantilla": {}},
    "bonsai2-2b-ternario": {"motor": "llamacpp", "plantilla": {"enable_thinking": False}},
    "falcon3-3b-q4":       {"motor": "llamacpp", "plantilla": {}},
    "qwen2.5-1.5b-q4":     {"motor": "llamacpp", "plantilla": {}},
    "qwen3.5-2b-q4":       {"motor": "llamacpp", "plantilla": {"enable_thinking": False}},
}
IDIOMAS = ("es", "en")

# Tabla 2.
TEMPERATURA = 0
TOP_K = 1
SEMILLA = 42
MAX_TOKENS = 512
CONTEXTO = 2048

# Sección 3.1. Sin system prompt; el modelo recibe solo este mensaje de usuario.
PROMPTS = {
    "es": ("Resuelve el siguiente problema de matemáticas. Razona paso a paso y, al final, "
           "escribe en una línea aparte \"Respuesta: \" seguido únicamente del número final.\n\n"
           "Problema: {pregunta}"),
    "en": ("Solve the following math problem. Reason step by step and, at the end, "
           "write on a separate line \"Answer: \" followed only by the final number.\n\n"
           "Problem: {pregunta}"),
}

# MGSM fijado a un commit para que las tres laptops usen exactamente los mismos archivos.
MGSM_COMMIT = "3622039cf51f7eeffa58b957332a8e8c337981d7"
MGSM_URL = "https://raw.githubusercontent.com/google-research/url-nlp/{commit}/mgsm/mgsm_{idioma}.tsv"
MGSM_SHA256 = {
    "en": "50021d0f28cc957edcb44e7806425b1c7fbd648ddcb9e0a8ec689d10e57d40fa",
    "es": "5bd27ebdf00140cec845c5298dc17715f5cd57d8edda6df1976db40bf3f0750a",
}
N_PROBLEMAS = 250

CAMPOS = ("id", "modelo", "idioma", "respuesta_correcta", "respuesta_modelo", "tokens_prompt",
          "tokens_generados", "tiempo_prompt_ms", "tiempo_generacion_ms", "truncada", "fecha_hora")


def ahora():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def sha256(ruta):
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def cargar_config(ruta):
    ruta = Path(ruta)
    if not ruta.is_file():
        sys.exit(f"No existe {ruta}. Copia config.ejemplo.json a config.json y ajusta las rutas.")
    cfg = json.loads(ruta.read_text(encoding="utf-8"))
    faltan = [m for m in MODELOS if m not in cfg.get("modelos", {})]
    if faltan:
        sys.exit(f"config.json: faltan rutas de modelos: {', '.join(faltan)}")
    for motor in ("bitnet", "llamacpp"):
        cfg.setdefault(f"{motor}_bin", str(Path(cfg[f"{motor}_dir"]) / "build" / "bin"))
    cfg.setdefault("hilos", nucleos_fisicos())
    return cfg


def nucleos_fisicos():
    try:
        import psutil
        return psutil.cpu_count(logical=False)
    except ImportError:
        return None


def ejecutable(cfg, motor, nombre):
    sufijo = ".exe" if os.name == "nt" else ""
    ruta = Path(cfg[f"{motor}_bin"]) / (nombre + sufijo)
    if not ruta.is_file():
        sys.exit(f"No existe {ruta}. Revisa {motor}_bin en config.json.")
    return str(ruta)


def carpeta_entrega(cfg, args):
    return Path(args.salida) if args.salida else RAIZ / "resultados" / f"entrega_{cfg['apellido']}"


# ---------------------------------------------------------------- datos

def ruta_datos(idioma):
    return RAIZ / "datos" / f"mgsm_{idioma}.tsv"


def cmd_datos(args):
    (RAIZ / "datos").mkdir(exist_ok=True)
    for idioma in IDIOMAS:
        destino = ruta_datos(idioma)
        if destino.is_file() and sha256(destino) == MGSM_SHA256[idioma]:
            print(f"OMITIDO {destino.name}: ya verificado.")
            continue
        url = MGSM_URL.format(commit=MGSM_COMMIT, idioma=idioma)
        parcial = destino.with_suffix(".tsv.part")
        with urllib.request.urlopen(url, timeout=60) as r, open(parcial, "wb") as f:
            shutil.copyfileobj(r, f)
        if sha256(parcial) != MGSM_SHA256[idioma]:
            parcial.unlink()
            sys.exit(f"SHA-256 incorrecto en {url}")
        parcial.replace(destino)
        print(f"COMPLETO {destino.name}: descargado y verificado.")


def leer_problemas(idioma):
    ruta = ruta_datos(idioma)
    if not ruta.is_file() or sha256(ruta) != MGSM_SHA256[idioma]:
        sys.exit(f"{ruta} falta o no coincide con el SHA-256 fijado. Ejecuta: laboratorio.py datos")
    with open(ruta, encoding="utf-8", newline="") as f:
        filas = list(csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE))
    if len(filas) != N_PROBLEMAS or any(len(fila) != 2 for fila in filas):
        sys.exit(f"{ruta}: se esperaban {N_PROBLEMAS} filas de 2 columnas.")
    return [(i, pregunta, respuesta) for i, (pregunta, respuesta) in enumerate(filas, start=1)]


# ---------------------------------------------------------------- servidor

class Servidor:
    """llama-server de un modelo, con medición de la memoria pico (RSS) del proceso."""

    def __init__(self, cfg, modelo, registro):
        import psutil
        self.psutil = psutil
        self.puerto = puerto_libre()
        comando = [
            ejecutable(cfg, MODELOS[modelo]["motor"], "llama-server"),
            "-m", cfg["modelos"][modelo],
            "-c", str(CONTEXTO), "-np", "1", "-t", str(cfg["hilos"]), "-ngl", "0",
            "--jinja", "--reasoning-format", "none",
            "--host", "127.0.0.1", "--port", str(self.puerto),
        ]
        if "archivo_plantilla" in MODELOS[modelo]:
            comando += ["--chat-template-file", str(RAIZ / MODELOS[modelo]["archivo_plantilla"])]
        self.log = open(registro, "a", encoding="utf-8")
        self.log.write(f"\n# {ahora()} {' '.join(comando)}\n")
        self.log.flush()
        self.proceso = subprocess.Popen(comando, stdout=self.log, stderr=subprocess.STDOUT)
        self.pico = 0
        self._parar = threading.Event()
        self._hilo = threading.Thread(target=self._medir, daemon=True)
        self._hilo.start()
        self._esperar_listo()

    def _medir(self):
        try:
            p = self.psutil.Process(self.proceso.pid)
            while not self._parar.is_set():
                rss = p.memory_info().rss
                for hijo in p.children(recursive=True):
                    rss += hijo.memory_info().rss
                self.pico = max(self.pico, rss)
                self._parar.wait(0.1)
        except self.psutil.NoSuchProcess:
            pass

    def _esperar_listo(self, limite=600):
        inicio = time.time()
        while time.time() - inicio < limite:
            if self.proceso.poll() is not None:
                sys.exit(f"llama-server terminó al cargar el modelo; ver {self.log.name}")
            try:
                with urllib.request.urlopen(self.url("/health"), timeout=5) as r:
                    if r.status == 200:
                        break
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                pass
            time.sleep(0.5)
        else:
            self.cerrar()
            sys.exit(f"llama-server no respondió en {limite} s; ver {self.log.name}")
        # El servidor puede ajustar el contexto a la memoria disponible; la tabla 2 exige 2048.
        with urllib.request.urlopen(self.url("/props"), timeout=30) as r:
            props = json.load(r)
        n_ctx = props.get("default_generation_settings", {}).get("n_ctx")
        if n_ctx != CONTEXTO:
            self.cerrar()
            sys.exit(f"El servidor usa n_ctx={n_ctx}, no {CONTEXTO}; ver {self.log.name}")

    def url(self, ruta):
        return f"http://127.0.0.1:{self.puerto}{ruta}"

    def preguntar(self, prompt, plantilla):
        cuerpo = {
            "messages": [{"role": "user", "content": prompt}],
            "temperature": TEMPERATURA, "top_k": TOP_K, "seed": SEMILLA,
            "max_tokens": MAX_TOKENS, "stream": False,
            # Cada problema se procesa completo; sin reutilizar el prefijo del anterior.
            "cache_prompt": False,
        }
        if plantilla:
            cuerpo["chat_template_kwargs"] = plantilla
        peticion = urllib.request.Request(self.url("/v1/chat/completions"),
                                          data=json.dumps(cuerpo).encode(),
                                          headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(peticion, timeout=1800) as r:
            return json.load(r)

    def cerrar(self):
        self._parar.set()
        if self.proceso.poll() is None:
            self.proceso.terminate()
            try:
                self.proceso.wait(30)
            except subprocess.TimeoutExpired:
                self.proceso.kill()
        self._hilo.join(5)
        self.log.close()


def puerto_libre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------- correr

def leer_respuestas(ruta):
    """Respuestas ya guardadas; descarta una última línea incompleta tras una interrupción."""
    if not ruta.is_file():
        return {}
    validas, lineas = {}, ruta.read_text(encoding="utf-8").splitlines()
    for linea in lineas:
        try:
            fila = json.loads(linea)
            validas[fila["id"]] = linea
        except (json.JSONDecodeError, KeyError, TypeError):
            print(f"AVISO {ruta.name}: se descarta una línea incompleta.")
    if len(validas) != len(lineas):
        ruta.write_text("".join(validas[i] + "\n" for i in sorted(validas)), encoding="utf-8")
    return validas


def guardar_memoria(entrega, modelo, idioma, pico_bytes):
    carpeta = entrega / "memoria_parcial"
    carpeta.mkdir(exist_ok=True)
    ruta = carpeta / f"{modelo}_{idioma}.json"
    anterior = json.loads(ruta.read_text())["memoria_pico_mb"] if ruta.is_file() else 0
    pico = max(anterior, round(pico_bytes / 2**20, 1))
    ruta.write_text(json.dumps({"memoria_pico_mb": pico}))
    with open(entrega / "memoria.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["modelo", "idioma", "memoria_pico_mb"])
        for m in MODELOS:
            for i in IDIOMAS:
                parcial = carpeta / f"{m}_{i}.json"
                if parcial.is_file():
                    w.writerow([m, i, json.loads(parcial.read_text())["memoria_pico_mb"]])
    return pico


def cmd_correr(args):
    cfg = cargar_config(args.config)
    if args.piloto:
        entrega = Path(args.salida) if args.salida else RAIZ / "resultados" / "piloto"
    else:
        entrega = carpeta_entrega(cfg, args)
    (entrega / "respuestas").mkdir(parents=True, exist_ok=True)
    (entrega / "registros").mkdir(exist_ok=True)
    modelos = args.modelos or list(MODELOS)
    idiomas = args.idiomas or list(IDIOMAS)
    duraciones = {}
    with open(entrega / "registros" / "corridas.txt", "a", encoding="utf-8") as bitacora:
        bitacora.write(f"{ahora()} inicio modelos={modelos} idiomas={idiomas} "
                       f"hilos={cfg['hilos']} piloto={args.piloto or 'no'}\n")
    for modelo in modelos:
        for idioma in idiomas:
            problemas = leer_problemas(idioma)[:args.piloto or None]
            ruta = entrega / "respuestas" / f"{modelo}_{idioma}.jsonl"
            hechas = leer_respuestas(ruta)
            pendientes = [p for p in problemas if p[0] not in hechas]
            print(f"== {modelo} {idioma}: {len(hechas)} hechas, {len(pendientes)} pendientes")
            if not pendientes:
                continue
            servidor = Servidor(cfg, modelo, entrega / "registros" / f"servidor_{modelo}_{idioma}.log")
            inicio = time.time()
            try:
                with open(ruta, "a", encoding="utf-8") as salida:
                    for n, (pid, pregunta, correcta) in enumerate(pendientes, start=1):
                        r = servidor.preguntar(PROMPTS[idioma].format(pregunta=pregunta),
                                               MODELOS[modelo]["plantilla"])
                        eleccion, uso, tiempos = r["choices"][0], r["usage"], r["timings"]
                        fila = {
                            "id": pid, "modelo": modelo, "idioma": idioma,
                            "respuesta_correcta": correcta,
                            "respuesta_modelo": eleccion["message"].get("content") or "",
                            "tokens_prompt": uso["prompt_tokens"],
                            "tokens_generados": uso["completion_tokens"],
                            "tiempo_prompt_ms": round(tiempos["prompt_ms"], 3),
                            "tiempo_generacion_ms": round(tiempos["predicted_ms"], 3),
                            "truncada": eleccion["finish_reason"] == "length",
                            "fecha_hora": ahora(),
                        }
                        salida.write(json.dumps(fila, ensure_ascii=False) + "\n")
                        salida.flush()
                        os.fsync(salida.fileno())
                        print(f"  {modelo} {idioma} #{pid} ({n}/{len(pendientes)}): "
                              f"{fila['tokens_generados']} tokens, "
                              f"{(fila['tiempo_prompt_ms'] + fila['tiempo_generacion_ms']) / 1000:.1f} s"
                              f"{', TRUNCADA' if fila['truncada'] else ''}")
            finally:
                servidor.cerrar()
                pico = guardar_memoria(entrega, modelo, idioma, servidor.pico)
            duraciones[(modelo, idioma)] = (time.time() - inicio) / len(pendientes)
            print(f"   memoria pico del servidor: {pico} MB")
    with open(entrega / "registros" / "corridas.txt", "a", encoding="utf-8") as bitacora:
        bitacora.write(f"{ahora()} fin\n")
    if args.piloto and duraciones:
        total = sum(d * N_PROBLEMAS for d in duraciones.values())
        print(f"Estimación de la corrida completa para lo medido: {total / 3600:.1f} h "
              f"(sin contar la carga de cada modelo).")


# ---------------------------------------------------------------- velocidad

def cmd_velocidad(args):
    cfg = cargar_config(args.config)
    carpeta = carpeta_entrega(cfg, args) / "velocidad"
    carpeta.mkdir(parents=True, exist_ok=True)
    for modelo in args.modelos or list(MODELOS):
        destino = carpeta / f"{modelo}.csv"
        comando = [ejecutable(cfg, MODELOS[modelo]["motor"], "llama-bench"),
                   "-m", cfg["modelos"][modelo], "-p", "512", "-n", "128", "-r", "5",
                   "-t", str(cfg["hilos"]), "-o", "csv"]
        print(f"== {modelo}: {' '.join(comando)}")
        with open(destino.with_suffix(".csv.part"), "w", encoding="utf-8") as f:
            resultado = subprocess.run(comando, stdout=f)
        if resultado.returncode != 0:
            sys.exit(f"llama-bench falló con {modelo} (código {resultado.returncode}).")
        destino.with_suffix(".csv.part").replace(destino)


# ---------------------------------------------------------------- versiones

def git(carpeta, *argumentos):
    try:
        return subprocess.run(["git", "-C", str(carpeta), *argumentos], capture_output=True,
                              text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def commit(carpeta):
    sha = git(carpeta, "rev-parse", "HEAD") or "desconocido"
    if git(carpeta, "status", "--porcelain", "--untracked-files=no"):
        sha += " (con cambios locales)"
    return sha


def texto_versiones(cfg):
    lineas = [f"Fecha: {ahora()}",
              f"Script común: {commit(RAIZ)}",
              f"bitnet.cpp: {commit(cfg['bitnet_dir'])}"]
    submodulo = Path(cfg["bitnet_dir"]) / "3rdparty" / "llama.cpp"
    if submodulo.is_dir():
        lineas.append(f"bitnet.cpp, submódulo llama.cpp: {commit(submodulo)}")
    lineas.append(f"llama.cpp: {commit(cfg['llamacpp_dir'])}")
    lineas.append(f"MGSM (google-research/url-nlp): {MGSM_COMMIT}")
    lineas.append("Modelos:")
    for modelo in MODELOS:
        ruta = Path(cfg["modelos"][modelo])
        tamano = f"{ruta.stat().st_size / 1e6:.1f} MB" if ruta.is_file() else "NO ENCONTRADO"
        lineas.append(f"  {modelo}: {ruta.name}, {tamano}")
    return "\n".join(lineas) + "\n"


def cmd_versiones(args):
    cfg = cargar_config(args.config)
    entrega = carpeta_entrega(cfg, args)
    entrega.mkdir(parents=True, exist_ok=True)
    (entrega / "versiones.txt").write_text(texto_versiones(cfg), encoding="utf-8")
    print((entrega / "versiones.txt").read_text(encoding="utf-8"), end="")


# ---------------------------------------------------------------- preparar

def ficha(cfg):
    try:
        import psutil
        logicos, ram = psutil.cpu_count(logical=True), f"{psutil.virtual_memory().total / 2**30:.1f}"
    except ImportError:
        logicos, ram = os.cpu_count(), ""
    procesador = platform.processor()
    if Path("/proc/cpuinfo").is_file():
        for linea in Path("/proc/cpuinfo").read_text().splitlines():
            if linea.startswith("model name"):
                procesador = linea.split(":", 1)[1].strip()
                break
    return (f"Alumno:\n"
            f"Marca y modelo de la laptop:\n"
            f"Procesador: {procesador}\n"
            f"Núcleos físicos / lógicos: {nucleos_fisicos()} / {logicos}\n"
            f"Memoria RAM (GB): {ram}\n"
            f"Sistema operativo y versión: {platform.platform()}\n"
            f"Hilos usados: {cfg['hilos']}\n"
            f"Inicio y fin de la corrida completa (fecha y hora):\n")


def cmd_preparar(args):
    cfg = cargar_config(args.config)
    entrega = carpeta_entrega(cfg, args)
    entrega.mkdir(parents=True, exist_ok=True)
    for nombre, contenido in (("ficha_laptop.txt", ficha(cfg)),
                              ("bitacora.md", (RAIZ / "plantillas" / "bitacora.md").read_text(encoding="utf-8"))):
        destino = entrega / nombre
        if destino.exists():
            print(f"OMITIDO {destino}: ya existe.")
        else:
            destino.write_text(contenido, encoding="utf-8")
            print(f"CREADO {destino}")
    cmd_versiones(args)
    if cfg["hilos"] != nucleos_fisicos():
        print(f"AVISO: hilos={cfg['hilos']} no coincide con los {nucleos_fisicos()} núcleos físicos.")


# ---------------------------------------------------------------- verificar

def cmd_verificar(args):
    cfg = cargar_config(args.config)
    entrega = carpeta_entrega(cfg, args)
    problemas = []
    for modelo in MODELOS:
        for idioma in IDIOMAS:
            ruta = entrega / "respuestas" / f"{modelo}_{idioma}.jsonl"
            if not ruta.is_file():
                problemas.append(f"falta {ruta.name}")
                continue
            filas = [json.loads(l) for l in ruta.read_text(encoding="utf-8").splitlines()]
            ids = sorted(f.get("id") for f in filas)
            if ids != list(range(1, N_PROBLEMAS + 1)):
                problemas.append(f"{ruta.name}: {len(filas)} líneas; ids distintos de 1..{N_PROBLEMAS}")
            for f in filas:
                vacios = [c for c in CAMPOS if f.get(c) in (None, "")]
                if vacios:
                    problemas.append(f"{ruta.name} #{f.get('id')}: campos vacíos {vacios}")
    for modelo in MODELOS:
        if not (entrega / "velocidad" / f"{modelo}.csv").is_file():
            problemas.append(f"falta velocidad/{modelo}.csv")
    memoria = entrega / "memoria.csv"
    filas = list(csv.DictReader(open(memoria, encoding="utf-8"))) if memoria.is_file() else []
    if len(filas) != len(MODELOS) * len(IDIOMAS):
        problemas.append(f"memoria.csv tiene {len(filas)} filas, no {len(MODELOS) * len(IDIOMAS)}")
    for nombre in ("ficha_laptop.txt", "versiones.txt", "bitacora.md"):
        if not (entrega / nombre).is_file():
            problemas.append(f"falta {nombre}")
    ficha_txt = entrega / "ficha_laptop.txt"
    if ficha_txt.is_file():
        for linea in ficha_txt.read_text(encoding="utf-8").splitlines():
            if linea.rstrip().endswith(":"):
                problemas.append(f"ficha_laptop.txt sin completar: {linea}")
    if problemas:
        print("PENDIENTE:\n  " + "\n  ".join(problemas))
        sys.exit(1)
    print(f"Lista de verificación completa en {entrega}.")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default=str(RAIZ / "config.json"))
    p.add_argument("--salida", help="carpeta de entrega (por defecto resultados/entrega_<apellido>)")
    sub = p.add_subparsers(dest="comando", required=True)
    sub.add_parser("datos").set_defaults(f=cmd_datos)
    sub.add_parser("preparar").set_defaults(f=cmd_preparar)
    c = sub.add_parser("correr")
    c.add_argument("--piloto", type=int, metavar="N", help="solo los N primeros problemas, en resultados/piloto")
    c.add_argument("--modelos", nargs="+", choices=list(MODELOS))
    c.add_argument("--idiomas", nargs="+", choices=list(IDIOMAS))
    c.set_defaults(f=cmd_correr)
    v = sub.add_parser("velocidad")
    v.add_argument("--modelos", nargs="+", choices=list(MODELOS))
    v.set_defaults(f=cmd_velocidad)
    sub.add_parser("versiones").set_defaults(f=cmd_versiones)
    sub.add_parser("verificar").set_defaults(f=cmd_verificar)
    args = p.parse_args()
    args.f(args)


if __name__ == "__main__":
    main()
