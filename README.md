# Script común del laboratorio BitNet

Script único de la sección 3.2 de la guía del laboratorio. Los tres alumnos usan
el mismo commit sin modificarlo; cualquier cambio se hace aquí, con autorización
de los profesores, y cada uno registra en `versiones.txt` el commit que usó.

Fijo en `laboratorio.py`: los 6 modelos de la tabla 1, las condiciones de la
tabla 2, los dos prompts de la sección 3.1 y la versión de MGSM. `config.json`
contiene solo las rutas de cada laptop y no se sube al repositorio.

## Requisitos

- bitnet.cpp y llama.cpp (b11555 o posterior) compilados para CPU, con
  `llama-server` y `llama-bench`. bitnet.cpp necesita el parche de este repositorio
  (sección siguiente).
- El fork de llama.cpp de PrismML (rama `prism`), para Bonsai en PQ2_0:

  ```bash
  git clone --depth 1 -b prism https://github.com/PrismML-Eng/llama.cpp llama.cpp-prism
  cmake -S llama.cpp-prism -B llama.cpp-prism/build -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=ON
  cmake --build llama.cpp-prism/build --target llama-server llama-bench -j
  ```

- Los 6 modelos de la tabla 1 en GGUF. Bonsai en `Ternary-Bonsai-2-2B-PQ2_0.gguf`
  (prism-ml/Ternary-Bonsai-2-2B-gguf, SHA-256 `d5f01a4e…f87008f5b`).
- Python 3.9 o posterior y `pip install -r requirements.txt` (psutil).

## bitnet.cpp: parche obligatorio

El llama.cpp que trae bitnet.cpp (commit `0b341e5`, submódulo `390c307`) tiene
tres fallos. `parches/bitnet-llamacpp.patch` los corrige:

1. **BitNet 2B responde mal.** El grafo de `bitnet-b1.58` usa la activación SiLU,
   pero el modelo usa ReLU al cuadrado. El modelo repite frases ("Problem: Janet's
   ducks…") en lugar de razonar. El parche usa `LLM_FFN_RELU_SQR` solo para esa
   arquitectura.
2. **`llama-quantize` rechaza I2_S** ("invalid ftype 'I2_S'"), así que Falcon3
   ternario no se puede convertir. El parche registra el tipo y, como bitnet.cpp
   original, deja embeddings y capa de salida en F16.
3. **El cuantizador I2_S empaqueta los pesos en un orden distinto al que lee el
   kernel.** Falcon3 ternario generaría texto sin sentido. El parche usa el mismo
   orden de bloques de 128 que el kernel y el conversor de Python.

Instalación en Linux o macOS. En Windows, los mismos pasos en una terminal con Git:

```bash
git clone https://github.com/microsoft/BitNet && cd BitNet
git checkout 0b341e582afbf9e1011f24744b554c96a3477eb5
git submodule update --init --depth 1           # submódulo 390c307
git -C 3rdparty/llama.cpp apply /ruta/a/este/repo/parches/bitnet-llamacpp.patch
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=clang \
      -DCMAKE_CXX_COMPILER=clang++ -DBITNET_X86_TL2=OFF -DLLAMA_BUILD_COMMON=ON \
      -DLLAMA_BUILD_SERVER=ON -DLLAMA_BUILD_TOOLS=ON
cmake --build build --target llama-server llama-bench llama-quantize -j
```

`-DLLAMA_BUILD_COMMON=ON` es necesario: sin él no se generan `llama-server` ni
`llama-quantize`. Si ya habían compilado bitnet.cpp sin el parche, apliquen el parche
y repitan solo el último comando.

Conversión de Falcon3 ternario a I2_S (`tiiuae/Falcon3-3B-Instruct-1.58bit`):

```bash
python utils/convert-hf-to-gguf-bitnet.py <carpeta_falcon> --outtype f32
build/bin/llama-quantize <carpeta_falcon>/ggml-model-f32.gguf \
      <carpeta_falcon>/ggml-model-i2_s.gguf I2_S 1
```

El `1` final usa un solo hilo, porque I2_S guarda una escala por tensor. El GGUF
resultante pesa 2 221 465 856 bytes. `laboratorio.py versiones` indica en
`versiones.txt` si el parche está aplicado.

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

Por acuerdo del equipo, autorizado por el profesor el 28 de octubre de 2026, Bonsai
usa PQ2_0 en lugar de Q2_0: son los mismos pesos ternarios con otro empaquetado. En una prueba con 10 problemas de MGSM y 16 hilos,
PQ2_0 en el fork de PrismML generó a 77 t/s, frente a 51 t/s de Q2_0 en llama.cpp,
con la misma memoria pico (~0,9 GB) y sin diferencia de exactitud distinguible.
`versiones.txt` registra el commit del fork.

## Datos

MGSM de [google-research/url-nlp](https://github.com/google-research/url-nlp/tree/main/mgsm),
commit `3622039cf51f7eeffa58b957332a8e8c337981d7`, licencia CC BY 4.0. Los TSV
no se suben al repositorio; `datos` los descarga y verifica.
