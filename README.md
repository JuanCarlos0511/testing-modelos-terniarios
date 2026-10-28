# Script común del laboratorio BitNet

Script único de la sección 3.2 de la guía del laboratorio. Los tres alumnos usan
el mismo commit sin modificarlo; cualquier cambio se hace aquí, con autorización
de los profesores, y cada uno registra en `versiones.txt` el commit que usó.

Fijo en `laboratorio.py`: los 6 modelos de la tabla 1, las condiciones de la
tabla 2, los dos prompts de la sección 3.1 y la versión de MGSM. `config.json`
contiene solo las rutas de cada laptop y no se sube al repositorio.

## Requisitos

- bitnet.cpp y llama.cpp (b11555 o posterior) compilados para CPU, con
  `llama-server` y `llama-bench`.
- El fork de llama.cpp de PrismML (rama `prism`), para Bonsai en PQ2_0:

  ```bash
  git clone --depth 1 -b prism https://github.com/PrismML-Eng/llama.cpp llama.cpp-prism
  cmake -S llama.cpp-prism -B llama.cpp-prism/build -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=ON
  cmake --build llama.cpp-prism/build --target llama-server llama-bench -j
  ```

- Los 6 modelos de la tabla 1 en GGUF. Bonsai en `Ternary-Bonsai-2-2B-PQ2_0.gguf`
  (prism-ml/Ternary-Bonsai-2-2B-gguf, SHA-256 `d5f01a4e…f87008f5b`).
- Python 3.9 o posterior y `pip install -r requirements.txt` (psutil).

## Uso

```bash
cp config.ejemplo.json config.json      # ajustar apellido, hilos y rutas
python laboratorio.py datos             # MGSM en y es, commit fijo y SHA-256 verificado
python laboratorio.py preparar          # ficha_laptop.txt, bitacora.md y versiones.txt
python laboratorio.py correr --piloto 5 # prueba piloto en resultados/piloto, con estimación de duración
python laboratorio.py correr            # corrida completa: 6 modelos x 2 idiomas x 250 problemas
python laboratorio.py velocidad         # llama-bench, sección 3.3
python laboratorio.py versiones         # actualizar versiones.txt al terminar
python laboratorio.py verificar         # lista de verificación de la sección 4.2
```

La entrega queda en `resultados/entrega_<apellido>/`, con la estructura de la
sección 4. Completar a mano los campos vacíos de `ficha_laptop.txt` y la bitácora.

`correr` se puede interrumpir con Ctrl+C y volver a lanzar: conserva las
respuestas guardadas y continúa con las pendientes. `--modelos` e `--idiomas`
limitan la corrida a una parte, por ejemplo `--modelos bitnet-2b --idiomas es`.

## Qué hace `correr`

Para cada modelo e idioma inicia un `llama-server` de su motor: bitnet.cpp para
`bitnet-2b` y `falcon3-3b-ternario`; el fork de PrismML para `bonsai2-2b-ternario`;
llama.cpp para el resto. Opciones:

- `-c 2048 -np 1 -t <hilos> -ngl 0 --jinja --reasoning-format none`;
- la plantilla de chat del propio GGUF y, en Qwen3.5 y Bonsai,
  `enable_thinking: false`. Excepción: `bitnet-2b` usa `plantillas/bitnet-2b.jinja`,
  la plantilla del modelo original `microsoft/bitnet-b1.58-2B-4T` (commit `04c3b9a`),
  porque la incluida en el GGUF pone el fin de texto antes de la respuesta;
- en cada petición a `/v1/chat/completions`: el prompt como único mensaje de usuario,
  `temperature 0`, `top_k 1`, `seed 42`, `max_tokens 512` y sin caché de prompt.

El script comprueba que el servidor use un contexto de 2048. Guarda cada respuesta
sin editar, con los campos de la tabla 3, en `respuestas/<modelo>_<idioma>.jsonl`.
Cada línea incluye además `tokens_por_segundo_prompt` y
`tokens_por_segundo_generacion`, medidos por el servidor.
Mide la memoria pico (RSS) del proceso del servidor cada 0,1 s y la escribe en
`memoria.csv`. Los registros de cada servidor quedan en `registros/`.

## Cambio respecto a la guía

Por acuerdo del equipo, Bonsai usa PQ2_0 en lugar de Q2_0: son los mismos pesos
ternarios con otro empaquetado. En una prueba con 10 problemas de MGSM y 16 hilos,
PQ2_0 en el fork de PrismML generó a 77 t/s, frente a 51 t/s de Q2_0 en llama.cpp,
con la misma memoria pico (~0,9 GB) y sin diferencia de exactitud distinguible.
`versiones.txt` registra el commit del fork.

## Datos

MGSM de [google-research/url-nlp](https://github.com/google-research/url-nlp/tree/main/mgsm),
commit `3622039cf51f7eeffa58b957332a8e8c337981d7`, licencia CC BY 4.0. Los TSV
no se suben al repositorio; `datos` los descarga y verifica.
